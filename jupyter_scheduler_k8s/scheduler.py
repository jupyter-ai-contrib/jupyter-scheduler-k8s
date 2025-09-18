"""K8s Scheduler implementation for jupyter-scheduler."""

import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Union

from jupyter_scheduler.scheduler import Scheduler
from jupyter_scheduler.models import (
    Status,
    CreateJobDefinition,
    UpdateJobDefinition,
    DescribeJobDefinition,
    DescribeJob,
    ListJobDefinitionsQuery,
    ListJobDefinitionsResponse,
    CreateJobFromDefinition,
    CreateJob
)
from jupyter_scheduler.utils import get_utc_timestamp
from kubernetes import client, config
from kubernetes.client.rest import ApiException



logger = logging.getLogger(__name__)


class K8sScheduler(Scheduler):
    """Pure K8s-native scheduler implementation.

    All operations use Kubernetes resources directly:
    - K8s Jobs serve as job records
    - K8s CronJobs handle scheduling
    - No SQLite dependency
    """
    
    def __init__(self, **kwargs):
        # Validate configuration
        if 'database_manager' in kwargs:
            from jupyter_scheduler.managers import SQLAlchemyDatabaseManager
            if isinstance(kwargs['database_manager'], SQLAlchemyDatabaseManager):
                logger.error("="*70)
                logger.error("Configuration Error: K8sScheduler requires K8sDatabaseManager")
                logger.error("")
                logger.error("Option 1 - Launch with command line arguments:")
                logger.error("  jupyter lab \\")
                logger.error('    --SchedulerApp.scheduler_class="jupyter_scheduler_k8s.K8sScheduler" \\')
                logger.error('    --SchedulerApp.database_manager_class="jupyter_scheduler_k8s.K8sDatabaseManager" \\')
                logger.error('    --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"')
                logger.error("")
                logger.error("Option 2 - Add to ~/.jupyter/jupyter_lab_config.py:")
                logger.error('  c.SchedulerApp.scheduler_class = "jupyter_scheduler_k8s.K8sScheduler"')
                logger.error('  c.SchedulerApp.database_manager_class = "jupyter_scheduler_k8s.K8sDatabaseManager"')
                logger.error('  c.Scheduler.execution_manager_class = "jupyter_scheduler_k8s.K8sExecutionManager"')
                logger.error("="*70)
                raise ValueError(
                    "K8sScheduler requires K8sDatabaseManager. "
                    "Use --SchedulerApp.database_manager_class (not --Scheduler.database_manager_class)"
                )

        # Set db_url for K8s database manager
        kwargs['db_url'] = f"k8s://{os.environ.get('K8S_NAMESPACE', 'default')}"
        super().__init__(**kwargs)
        self._k8s_batch = None
        self._k8s_core = None
        self.namespace = os.environ.get("K8S_NAMESPACE", "default")
        self.s3_bucket = os.environ.get("S3_BUCKET")
        
    def _init_k8s_clients(self):
        """Initialize Kubernetes API clients."""
        if self._k8s_batch is not None:
            return
            
        try:
            config.load_incluster_config()
            logger.info("Using in-cluster Kubernetes configuration")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("Using kubeconfig file")
            except config.ConfigException as e:
                logger.error(f"Failed to load Kubernetes configuration: {e}")
                raise
                
        self._k8s_batch = client.BatchV1Api()
        self._k8s_core = client.CoreV1Api()

    def _detect_image_pull_policy(self) -> str:
        """Auto-detect appropriate image pull policy based on K8s context."""
        try:
            contexts, active_context = config.list_kube_config_contexts()
            if active_context and active_context.get("name"):
                context_name = active_context["name"]
                # Check if running on local development cluster
                if any(
                    local_indicator in context_name.lower()
                    for local_indicator in ["kind", "minikube", "docker-desktop"]
                ):
                    logger.info(
                        f"Detected local K8s context '{context_name}', using imagePullPolicy: Never"
                    )
                    return "Never"
                else:
                    logger.info(
                        f"Detected remote K8s context '{context_name}', using imagePullPolicy: Always"
                    )
                    return "Always"
        except Exception as e:
            logger.warning(
                f"Could not detect K8s context: {e}, defaulting to imagePullPolicy: Always"
            )
        return "Always"

    def stop_job(self, job_id: str):
        """Stop a running job using K8s Job suspension.
        
        This matches jupyter-scheduler behavior: stops execution but preserves
        staging files (in our case, S3 files) for debugging/recovery.
        Uses K8s native job suspension for clean state management.
        """
        logger.info(f"=== STOP JOB REQUEST: {job_id} ===")
        job = self.get_job(job_id)
        logger.info(f"Current job status: {job.status}")
        if job.status != Status.IN_PROGRESS:
            logger.info(f"Job {job_id} not in progress, skipping stop")
            return
            
        self._init_k8s_clients()

        # Handle both regular jobs (UUID) and CronJob-spawned jobs (K8s name)
        if job_id.startswith("nb-"):
            # CronJob-spawned job - job_id IS the K8s name
            job_name = job_id
        else:
            # Regular job - construct name from UUID
            job_name = f"nb-job-{job_id[:8]}"
        
        try:
            k8s_job = self._k8s_batch.read_namespaced_job(job_name, self.namespace)
            if k8s_job.metadata.annotations and "scheduler.jupyter.org/job-data" in k8s_job.metadata.annotations:
                job_data = json.loads(k8s_job.metadata.annotations["scheduler.jupyter.org/job-data"])
                job_data["status"] = Status.STOPPING.value
                
                body = client.V1Job(
                    metadata=client.V1ObjectMeta(
                        annotations={"scheduler.jupyter.org/job-data": json.dumps(job_data)}
                    )
                )
                self._k8s_batch.patch_namespaced_job(
                    name=job_name,
                    namespace=self.namespace,
                    body=body
                )
            
            # Suspend the K8s Job - this cleanly stops pods without marking job as failed
            # This is the K8s-native way to pause/stop a job (available since K8s 1.21)
            suspend_body = {"spec": {"suspend": True}}
            self._k8s_batch.patch_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                body=suspend_body
            )
            logger.info(f"✅ Successfully suspended K8s Job {job_name}")
                        
            if k8s_job.metadata.annotations and "scheduler.jupyter.org/job-data" in k8s_job.metadata.annotations:
                job_data["status"] = Status.STOPPED.value
                body = client.V1Job(
                    metadata=client.V1ObjectMeta(
                        annotations={"scheduler.jupyter.org/job-data": json.dumps(job_data)}
                    )
                )
                self._k8s_batch.patch_namespaced_job(
                    name=job_name,
                    namespace=self.namespace,
                    body=body
                )
                
            logger.info(f"✅ Job {job_id} stopped successfully")
            
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"K8s Job {job_name} not found")
            else:
                logger.error(f"Failed to stop job {job_id}: {e}")
                raise
                
    def delete_job(self, job_id: str):
        """Delete a job record and clean up resources.

        Since we use K8s Jobs as our database records, the parent's delete_job
        will call K8sQuery.delete() which removes the K8s Job.
        """
        logger.warning(f"🗑️ K8sScheduler.delete_job called for job_id: {job_id}")
        logger.info(f"=== DELETE JOB REQUEST: {job_id} ===")

        # Let parent handle the full delete flow:
        # 1. Query job via K8sSession
        # 2. Stop if running (calls our stop_job)
        # 3. Clean staging files
        # 4. Delete via K8sQuery.delete() (removes K8s Job)
        try:
            super().delete_job(job_id)
            logger.warning(f"✅ Successfully deleted job {job_id}")
        except Exception as e:
            logger.error(f"❌ Failed to delete job {job_id}: {e}")
            raise
                
        if self.s3_bucket:
            s3_prefix = f"s3://{self.s3_bucket}/job-{job_id}/"
            try:
                result = subprocess.run(
                    ['aws', 's3', 'rm', s3_prefix, '--recursive'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    logger.debug(f"Deleted S3 files for job {job_id}")
                else:
                    logger.warning(f"S3 deletion returned non-zero: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                logger.error(f"S3 deletion timed out for job {job_id}")
            except Exception as e:
                logger.error(f"Failed to delete S3 files for job {job_id}: {e}")
                
        logger.info(f"✅ Successfully deleted job {job_id} from K8s and S3")

    def create_job_definition(self, model: CreateJobDefinition) -> str:
        """Create a job definition - in SQLite or as K8s-native CronJob.

        Files are staged once to S3 and reused by all spawned jobs.
        """
        logger.info(f"=== CREATE JOB DEFINITION: {model.name} ===")
        logger.info(f"Schedule: {model.schedule} ({model.timezone or 'UTC'})")

        if not self.s3_bucket:
            raise ValueError("S3_BUCKET required for job definitions")

        # Generate job definition ID
        job_definition_id = str(uuid.uuid4())
        logger.info(f"Generated job definition ID: {job_definition_id}")

        self._init_k8s_clients()
        cronjob_name = f"nb-jobdef-{job_definition_id[:8]}"

        # Stage files to S3 (one-time copy for all future runs)
        s3_staging_prefix = f"s3://{self.s3_bucket}/job-definitions/{job_definition_id}/"
        self._stage_files_for_definition(model, s3_staging_prefix, job_definition_id)

        # Convert day names to numbers for K8s cron format
        cron_schedule = self._convert_schedule_to_cron(model.schedule)

        # Create pod template from model
        pod_template = self._create_pod_template_for_definition(
            model, job_definition_id, s3_staging_prefix
        )

        # Create CronJob
        cronjob = client.V1CronJob(
            metadata=client.V1ObjectMeta(
                name=cronjob_name,
                namespace=self.namespace,
                labels={
                    "app.kubernetes.io/managed-by": "jupyter-scheduler-k8s",
                    "scheduler.jupyter.org/job-definition-id": job_definition_id,
                    "scheduler.jupyter.org/name": self._sanitize(model.name)
                },
                annotations={
                    "scheduler.jupyter.org/job-definition-data": json.dumps({
                        "job_definition_id": job_definition_id,
                        "name": model.name,
                        "input_filename": model.input_filename,
                        "runtime_environment_name": model.runtime_environment_name,
                        "runtime_environment_parameters": model.runtime_environment_parameters,
                        "environment_variables": model.environment_variables,
                        "output_formats": model.output_formats,
                        "parameters": model.parameters,
                        "tags": model.tags,
                        "compute_type": model.compute_type,
                        "schedule": model.schedule,
                        "timezone": model.timezone,
                        "create_time": get_utc_timestamp(),
                        "update_time": get_utc_timestamp(),
                        "active": True,
                        "package_input_folder": model.package_input_folder,
                        "s3_staging_prefix": s3_staging_prefix
                    })
                }
            ),
            spec=client.V1CronJobSpec(
                schedule=cron_schedule,
                time_zone=model.timezone if model.timezone else "UTC",  # K8s 1.27+ native timezone support
                job_template=client.V1JobTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        generate_name=f"nb-jobdef-{job_definition_id[:8]}-",  # K8s will append unique suffix
                        labels={
                            "app.kubernetes.io/managed-by": "jupyter-scheduler-k8s",
                            "scheduler.jupyter.org/job-definition-id": job_definition_id,
                            "scheduler.jupyter.org/triggered-by": "cronjob",
                            "scheduler.jupyter.org/cronjob-spawned": "true",  # Easy identification of CronJob-spawned jobs
                            "scheduler.jupyter.org/status": "IN_PROGRESS",
                            "scheduler.jupyter.org/name": self._sanitize(model.name)
                        },
                        annotations={
                            "scheduler.jupyter.org/job-data": json.dumps({
                                "job_id": "PENDING",  # Will be derived from generated name
                                "name": model.name,
                                "url": "/jobs/PENDING",  # Will be updated when job_id is derived
                                "input_uri": model.input_uri,
                                "input_filename": model.input_filename,
                                "runtime_environment_name": model.runtime_environment_name,
                                "runtime_environment_parameters": model.runtime_environment_parameters,
                                "environment_variables": model.environment_variables,
                                "output_formats": model.output_formats,
                                "parameters": model.parameters,
                                "tags": model.tags,
                                "compute_type": model.compute_type,
                                "status": "IN_PROGRESS",
                                "job_definition_id": job_definition_id,
                                "create_time": get_utc_timestamp(),
                                "update_time": get_utc_timestamp(),
                                "start_time": get_utc_timestamp(),  # Job starts when created
                                "s3_staging_prefix": s3_staging_prefix,
                                "package_input_folder": model.package_input_folder
                            })
                        }
                    ),
                    spec=client.V1JobSpec(
                        template=pod_template,
                        backoff_limit=0,  # Don't retry failed jobs
                        ttl_seconds_after_finished=86400  # Clean up after 24 hours
                    )
                ),
                successful_jobs_history_limit=3,
                failed_jobs_history_limit=3,
                suspend=False  # Start in active state
            )
        )

        try:
            self._k8s_batch.create_namespaced_cron_job(
                namespace=self.namespace,
                body=cronjob
            )
            logger.info(f"✅ Created CronJob {cronjob_name} for scheduled job '{model.name}'")
            logger.info(f"   Schedule: {cron_schedule} {model.timezone or 'UTC'}")
            logger.info(f"   Job Definition ID: {job_definition_id}")
        except ApiException as e:
            logger.error(f"Failed to create CronJob: {e}")
            # Clean up S3 files on failure
            self._cleanup_s3_staging(s3_staging_prefix)
            raise

        return job_definition_id

    def update_job_definition(self, job_definition_id: str, model: UpdateJobDefinition):
        """Update a K8s CronJob configuration."""
        logger.info(f"=== UPDATE JOB DEFINITION: {job_definition_id} ===")
        logger.info(f"Updates: schedule={model.schedule}, active={model.active}, name={model.name}")

        self._init_k8s_clients()
        cronjob_name = f"nb-jobdef-{job_definition_id[:8]}"

        try:
            # Get existing CronJob
            cronjob = self._k8s_batch.read_namespaced_cron_job(
                name=cronjob_name,
                namespace=self.namespace
            )

            # Parse existing data
            job_data = json.loads(
                cronjob.metadata.annotations.get("scheduler.jupyter.org/job-definition-data", "{}")
            )

            # Update schedule if provided
            if model.schedule:
                new_cron = self._convert_schedule_to_cron(model.schedule)
                cronjob.spec.schedule = new_cron
                job_data["schedule"] = model.schedule

            # Update timezone (K8s 1.27+ native support)
            if model.timezone:
                cronjob.spec.time_zone = model.timezone
                job_data["timezone"] = model.timezone
            elif model.schedule and not model.timezone:
                # If schedule updated but no timezone, keep existing or default to UTC
                cronjob.spec.time_zone = job_data.get("timezone", "UTC")

            # Update active state (suspend/resume)
            if model.active is not None:
                cronjob.spec.suspend = not model.active
                job_data["active"] = model.active

            # Update other fields
            if model.name:
                job_data["name"] = model.name
                cronjob.metadata.labels["scheduler.jupyter.org/name"] = self._sanitize(model.name)

            if model.parameters is not None:
                job_data["parameters"] = model.parameters

            if model.environment_variables is not None:
                job_data["environment_variables"] = model.environment_variables

            # Update timestamp
            job_data["update_time"] = get_utc_timestamp()

            # Update annotation
            cronjob.metadata.annotations["scheduler.jupyter.org/job-definition-data"] = json.dumps(job_data)

            # Apply changes
            self._k8s_batch.patch_namespaced_cron_job(
                name=cronjob_name,
                namespace=self.namespace,
                body=cronjob
            )

            logger.info(f"✅ Successfully updated CronJob {cronjob_name}")

        except ApiException as e:
            if e.status == 404:
                logger.error(f"CronJob {cronjob_name} not found")
            else:
                logger.error(f"Failed to update CronJob: {e}")
            raise

        logger.info(f"CronJob is the source of truth, no separate database update needed")

    def delete_job_definition(self, job_definition_id: str):
        """Delete K8s CronJob and its spawned jobs."""
        logger.info(f"=== DELETE JOB DEFINITION: {job_definition_id} ===")
        self._init_k8s_clients()
        cronjob_name = f"nb-jobdef-{job_definition_id[:8]}"

        try:
            # First check if CronJob exists
            try:
                cronjob = self._k8s_batch.read_namespaced_cron_job(
                    name=cronjob_name,
                    namespace=self.namespace
                )
                logger.info(f"🔍 Found CronJob {cronjob_name} to delete")
            except ApiException as e:
                if e.status == 404:
                    logger.warning(f"⚠️ CronJob {cronjob_name} not found - may already be deleted")
                    return
                raise

            # Delete CronJob (this will cascade delete child Jobs)
            self._k8s_batch.delete_namespaced_cron_job(
                name=cronjob_name,
                namespace=self.namespace,
                propagation_policy='Foreground'  # Wait for child Jobs to delete
            )
            logger.info(f"✅ Successfully deleted CronJob {cronjob_name} and all child Jobs")

        except ApiException as e:
            logger.error(f"❌ Failed to delete CronJob {cronjob_name}: {e}")
            raise

        # Clean up S3 staging files
        if self.s3_bucket:
            s3_prefix = f"s3://{self.s3_bucket}/job-definitions/{job_definition_id}/"
            self._cleanup_s3_staging(s3_prefix)

    def get_staging_paths(self, model: Union[DescribeJob, DescribeJobDefinition]) -> Dict[str, str]:
        """Smart staging path resolution with fallback to parent for generation.

        This method handles two distinct scenarios:
        1. Path generation (job/definition creation): Uses parent's timestamp-based generation
        2. Path discovery (file download): Finds actual files, handling timestamp mismatches

        The staging directory existence serves as a natural and reliable indicator
        of which mode we're in, avoiding complex state checking or additional parameters.
        """
        # For job definitions, always use parent's generation logic
        if isinstance(model, DescribeJobDefinition):
            return super().get_staging_paths(model)

        # For jobs, determine context by staging directory existence
        staging_dir = os.path.join(self.staging_path, model.job_id)

        # Directory doesn't exist = job creation phase, need generated paths
        if not os.path.exists(staging_dir):
            logger.debug(f"Staging directory not found, using path generation for job {model.job_id}")
            return super().get_staging_paths(model)

        # Directory exists = download phase, discover actual files
        # This handles timestamp mismatches between generation and execution
        return self._discover_staging_files(staging_dir, model)

    def _discover_staging_files(self, staging_dir: str, model: DescribeJob) -> Dict[str, str]:
        """Discover actual files in staging directory by pattern matching.

        Handles both simple jobs and package_input_folder jobs with side effects.
        Maps all files found, including subdirectories and side-effect files.
        """
        staging_paths = {}

        # Check if package_input_folder mode is enabled
        package_mode = getattr(model, 'package_input_folder', False)

        if package_mode:
            # In package mode, discover ALL files recursively
            logger.debug(f"Package mode: discovering all files in {staging_dir}")

            for root, dirs, files in os.walk(staging_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    # Use relative path from staging_dir as key for nested files
                    rel_path = os.path.relpath(file_path, staging_dir)

                    # Map well-known output formats to their semantic keys
                    base_name = os.path.splitext(model.input_filename)[0]
                    if filename == model.input_filename and root == staging_dir:
                        staging_paths["input"] = file_path
                    elif filename.startswith(base_name) and root == staging_dir:
                        # Standard output files in root directory
                        if filename.endswith('.ipynb') and filename != model.input_filename:
                            staging_paths["ipynb"] = file_path
                        elif filename.endswith('.html'):
                            staging_paths["html"] = file_path
                        elif filename.endswith('.pdf'):
                            staging_paths["pdf"] = file_path
                        else:
                            # Other output formats or side-effect files
                            staging_paths[rel_path] = file_path
                    else:
                        # All other files (packaged inputs, side effects, etc.)
                        staging_paths[rel_path] = file_path
        else:
            # Original single-file mode
            try:
                files = os.listdir(staging_dir)
            except OSError as e:
                logger.warning(f"Failed to list staging directory {staging_dir}: {e}")
                return staging_paths

            logger.debug(f"Single-file mode: discovering files in {staging_dir}: {files}")

            for filename in files:
                file_path = os.path.join(staging_dir, filename)

                # Input file: exact name match without timestamp
                if filename == model.input_filename:
                    staging_paths["input"] = file_path

                # Output files: name prefix match with format extension
                elif filename.startswith(os.path.splitext(model.input_filename)[0]):
                    # Extract format from filename
                    if filename.endswith('.ipynb') and filename != model.input_filename:
                        staging_paths["ipynb"] = file_path
                    elif filename.endswith('.html'):
                        staging_paths["html"] = file_path
                    elif filename.endswith('.pdf'):
                        staging_paths["pdf"] = file_path
                    # Include any other generated files as well
                    else:
                        staging_paths[filename] = file_path

        logger.debug(f"Discovered staging paths for job {model.job_id}: {list(staging_paths.keys())}")
        return staging_paths

    def get_job_definition(self, job_definition_id: str) -> DescribeJobDefinition:
        """Get a single job definition from K8s CronJob."""
        logger.debug(f"Getting job definition {job_definition_id}")
        self._init_k8s_clients()
        cronjob_name = f"nb-jobdef-{job_definition_id[:8]}"

        try:
            cronjob = self._k8s_batch.read_namespaced_cron_job(
                name=cronjob_name,
                namespace=self.namespace
            )

            # Extract data from annotation
            job_data = json.loads(
                cronjob.metadata.annotations.get("scheduler.jupyter.org/job-definition-data", "{}")
            )

            # Convert to DescribeJobDefinition
            return DescribeJobDefinition(**job_data)

        except ApiException as e:
            if e.status == 404:
                logger.error(f"CronJob {cronjob_name} not found")
                return None
            else:
                logger.error(f"Failed to get CronJob: {e}")
                raise

    def list_job_definitions(self, query: ListJobDefinitionsQuery) -> ListJobDefinitionsResponse:
        """List job definitions from K8s CronJobs."""
        logger.debug(f"Listing job definitions with query: {query}")
        self._init_k8s_clients()

        # Build label selector - all CronJobs managed by us are job definitions
        label_selector = "app.kubernetes.io/managed-by=jupyter-scheduler-k8s"
        if query and query.name:
            # Add name filter if provided
            label_selector += f",scheduler.jupyter.org/name={self._sanitize(query.name)}"

        try:
            cronjobs = self._k8s_batch.list_namespaced_cron_job(
                namespace=self.namespace,
                label_selector=label_selector
            )

            definitions = []
            for cronjob in cronjobs.items:
                try:
                    job_data = json.loads(
                        cronjob.metadata.annotations.get("scheduler.jupyter.org/job-definition-data", "{}")
                    )
                    definitions.append(DescribeJobDefinition(**job_data))
                except Exception as e:
                    logger.warning(f"Failed to parse CronJob {cronjob.metadata.name}: {e}")

            # Sort by create_time desc by default
            definitions.sort(key=lambda x: x.create_time, reverse=True)

            # Apply pagination if needed
            total = len(definitions)
            # Pagination not yet implemented for K8s backend

            return ListJobDefinitionsResponse(
                job_definitions=definitions,
                total_count=total
            )

        except ApiException as e:
            logger.error(f"Failed to list CronJobs: {e}")
            raise

    def create_job_from_definition(self, job_definition_id: str, model: CreateJobFromDefinition) -> str:
        """Manually trigger a job from a job definition."""
        logger.info(f"=== MANUAL TRIGGER: Creating job from definition {job_definition_id} ===")

        # Get the job definition from the CronJob
        definition = self.get_job_definition(job_definition_id)
        if not definition:
            raise ValueError(f"Job definition {job_definition_id} not found")

        # Generate job ID
        job_id = str(uuid.uuid4())

        # Create K8s Job directly with metadata in labels/annotations
        self._init_k8s_clients()

        # Handle both regular jobs (UUID) and CronJob-spawned jobs (K8s name)
        if job_id.startswith("nb-"):
            # CronJob-spawned job - job_id IS the K8s name
            job_name = job_id
        else:
            # Regular job - construct name from UUID
            job_name = f"nb-job-{job_id[:8]}"

        # Build job data
        job_data = {
            "job_id": job_id,
            "name": f"{definition.name}-manual",
            "input_uri": f"s3://{self.s3_bucket}/job-definitions/{job_definition_id}/{definition.input_filename}",
            "input_filename": definition.input_filename,
            "runtime_environment_name": definition.runtime_environment_name,
            "runtime_environment_parameters": definition.runtime_environment_parameters,
            "environment_variables": definition.environment_variables,
            "output_formats": definition.output_formats,
            "parameters": model.parameters or definition.parameters,
            "tags": definition.tags,
            "compute_type": definition.compute_type,
            "package_input_folder": definition.package_input_folder,
            "job_definition_id": job_definition_id,
            "status": "PENDING",
            "create_time": get_utc_timestamp(),
            "update_time": get_utc_timestamp()
        }

        # Create pod template similar to CronJob
        pod_template = self._create_pod_template_for_manual_job(
            definition, job_id, job_data["input_uri"]
        )

        # Create K8s Job
        k8s_job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    "app.kubernetes.io/managed-by": "jupyter-scheduler-k8s",
                    "scheduler.jupyter.org/job-id": job_id,
                    "scheduler.jupyter.org/job-definition-id": job_definition_id,
                    "scheduler.jupyter.org/triggered-by": "manual",
                    "scheduler.jupyter.org/status": "PENDING",
                    "scheduler.jupyter.org/name": self._sanitize(job_data["name"])
                },
                annotations={
                    "scheduler.jupyter.org/job-data": json.dumps(job_data)
                }
            ),
            spec=client.V1JobSpec(
                template=pod_template,
                backoff_limit=0,
                ttl_seconds_after_finished=86400
            )
        )

        try:
            self._k8s_batch.create_namespaced_job(
                namespace=self.namespace,
                body=k8s_job
            )
            logger.info(f"✅ Created manual job {job_name} from definition {job_definition_id}")
        except ApiException as e:
            logger.error(f"Failed to create job: {e}")
            raise

        return job_id

    # Helper methods

    def add_job_files(self, model: DescribeJob):
        """Override to handle edge cases where packaged_files might be missing.

        If package_input_folder is True but packaged_files is empty/None,
        we discover files from the staging directory. This handles:
        - Old jobs created before we started storing packaged_files
        - Jobs where annotation update failed
        - Fallback discovery for robustness
        """
        # If package_input_folder but no packaged_files, try to discover them
        if model.package_input_folder and not model.packaged_files:
            staging_paths = self.get_staging_paths(model)
            if staging_paths:
                staging_path = staging_paths.get("input")
                if staging_path and os.path.exists(os.path.dirname(staging_path)):
                    staging_dir = os.path.dirname(staging_path)
                    discovered_files = []

                    # Discover all files in staging directory
                    for root, dirs, files in os.walk(staging_dir):
                        for filename in files:
                            # Skip main output files (they're tracked separately)
                            base_name = os.path.splitext(model.input_filename)[0]
                            if filename.startswith(base_name) and root == staging_dir:
                                continue

                            file_path = os.path.join(root, filename)
                            rel_path = os.path.relpath(file_path, staging_dir)
                            discovered_files.append(rel_path)

                    if discovered_files:
                        model.packaged_files = discovered_files
                        logger.debug(f"Discovered {len(discovered_files)} packaged files for job {model.job_id}")

        # Call parent implementation
        super().add_job_files(model)

    def _sanitize(self, value: str) -> str:
        """Sanitize value for K8s labels."""
        value = str(value).lower()
        value = ''.join(c if c.isalnum() or c in '-_.' else '-' for c in value)
        return value.strip('-_.')[:63] or "none"

    def _convert_schedule_to_cron(self, schedule: str) -> str:
        """Convert jupyter-scheduler schedule format to K8s cron format.

        Only handles day name to number conversion (MON -> 1).
        Timezone handling is delegated to K8s native support (1.27+).
        """
        if not schedule:
            raise ValueError("Schedule cannot be empty")

        # Day name to number conversion for K8s cron format
        DAY_MAP = {
            'MON': '1', 'TUE': '2', 'WED': '3', 'THU': '4',
            'FRI': '5', 'SAT': '6', 'SUN': '0'
        }

        cron = schedule
        for day_name, day_num in DAY_MAP.items():
            cron = cron.replace(day_name, day_num)

        return cron


    def _stage_files_for_definition(self, model: CreateJobDefinition, s3_prefix: str, job_definition_id: str):
        """Stage input files to S3 for job definition."""
        # Create local staging directory
        local_staging_dir = Path(self.staging_path) / job_definition_id
        local_staging_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Copy input file(s) to staging
            input_path = Path(model.input_uri)

            if model.package_input_folder and input_path.parent.is_dir():
                # Copy entire directory
                logger.debug(f"Copying directory {input_path.parent} to {local_staging_dir}")
                shutil.copytree(input_path.parent, local_staging_dir, dirs_exist_ok=True)
            else:
                # Copy single file
                logger.debug(f"Copying file {input_path} to {local_staging_dir}")
                shutil.copy2(input_path, local_staging_dir / input_path.name)

            # Upload to S3
            logger.debug(f"Uploading staged files to {s3_prefix}")
            result = subprocess.run(
                ['aws', 's3', 'sync', str(local_staging_dir), s3_prefix, '--quiet'],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                raise RuntimeError(f"Failed to upload to S3: {result.stderr}")

            logger.debug(f"Staged files to {s3_prefix}")

        finally:
            # Clean up local staging
            if local_staging_dir.exists():
                shutil.rmtree(local_staging_dir)

    def _cleanup_s3_staging(self, s3_prefix: str):
        """Clean up S3 staging files."""
        try:
            logger.debug(f"Cleaning up S3 files at {s3_prefix}")
            subprocess.run(
                ['aws', 's3', 'rm', s3_prefix, '--recursive', '--quiet'],
                capture_output=True,
                text=True,
                timeout=30
            )
        except Exception as e:
            logger.warning(f"Failed to clean up S3 files: {e}")

    def _create_pod_template_for_manual_job(
        self,
        definition: DescribeJobDefinition,
        job_id: str,
        input_uri: str
    ) -> client.V1PodTemplateSpec:
        """Create pod template for manually triggered job."""

        # Build environment variables
        env_vars = [
            client.V1EnvVar(name="S3_INPUT_PREFIX", value=input_uri.rsplit('/', 1)[0] + '/'),
            client.V1EnvVar(name="S3_OUTPUT_PREFIX", value=f"s3://{self.s3_bucket}/job-{job_id}/"),
            client.V1EnvVar(name="NOTEBOOK_PATH", value=f"/tmp/inputs/{definition.input_filename}"),
            client.V1EnvVar(name="OUTPUT_PATH", value=f"/tmp/outputs/{Path(definition.input_filename).stem}-{get_utc_timestamp()}.ipynb"),
            client.V1EnvVar(name="PARAMETERS", value=json.dumps(definition.parameters or {})),
            client.V1EnvVar(name="PACKAGE_INPUT_FOLDER", value="true" if definition.package_input_folder else "false"),
            client.V1EnvVar(name="OUTPUT_FORMATS", value=json.dumps(definition.output_formats or ["ipynb"]))
        ]

        # Add AWS credentials if available
        if os.environ.get("AWS_ACCESS_KEY_ID"):
            env_vars.append(client.V1EnvVar(name="AWS_ACCESS_KEY_ID", value=os.environ["AWS_ACCESS_KEY_ID"]))
        if os.environ.get("AWS_SECRET_ACCESS_KEY"):
            env_vars.append(client.V1EnvVar(name="AWS_SECRET_ACCESS_KEY", value=os.environ["AWS_SECRET_ACCESS_KEY"]))

        # Add user environment variables
        if definition.environment_variables:
            for name, value in definition.environment_variables.items():
                env_vars.append(client.V1EnvVar(name=name, value=str(value)))

        # Extract resource requirements
        resources = {}
        if definition.runtime_environment_parameters:
            k8s_cpu = definition.runtime_environment_parameters.get('k8s_cpu')
            k8s_memory = definition.runtime_environment_parameters.get('k8s_memory')
            k8s_gpu = definition.runtime_environment_parameters.get('k8s_gpu')

            if k8s_cpu or k8s_memory or k8s_gpu:
                resources = client.V1ResourceRequirements(
                    limits={},
                    requests={}
                )
                if k8s_cpu:
                    resources.limits["cpu"] = k8s_cpu
                    resources.requests["cpu"] = k8s_cpu
                if k8s_memory:
                    resources.limits["memory"] = k8s_memory
                    resources.requests["memory"] = k8s_memory
                if k8s_gpu and int(k8s_gpu) > 0:
                    resources.limits["nvidia.com/gpu"] = str(k8s_gpu)

        # Detect appropriate image pull policy
        image_pull_policy = self._detect_image_pull_policy()

        # Create container spec
        container = client.V1Container(
            name="notebook-executor",
            image=os.environ.get("K8S_IMAGE", "jupyter-scheduler-k8s:latest"),
            image_pull_policy=image_pull_policy,
            env=env_vars,
            resources=resources if resources else None
        )

        # Create pod template
        return client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(
                labels={
                    "app.kubernetes.io/managed-by": "jupyter-scheduler-k8s",
                    "scheduler.jupyter.org/job-id": job_id,
                    "scheduler.jupyter.org/triggered-by": "manual"
                }
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[container]
            )
        )

    def _create_pod_template_for_definition(
        self,
        model: CreateJobDefinition,
        job_definition_id: str,
        s3_staging_prefix: str
    ) -> client.V1PodTemplateSpec:
        """Create pod template for CronJob from job definition model."""

        # Build environment variables
        env_vars = [
            client.V1EnvVar(name="S3_INPUT_PREFIX", value=s3_staging_prefix),
            client.V1EnvVar(name="S3_OUTPUT_PREFIX", value=f"s3://{self.s3_bucket}/job-outputs/"),  # Will be unique per job
            client.V1EnvVar(name="NOTEBOOK_PATH", value=f"/tmp/inputs/{model.input_filename}"),
            client.V1EnvVar(name="OUTPUT_PATH", value=f"/tmp/outputs/output.ipynb"),  # Will be timestamped
            client.V1EnvVar(name="PARAMETERS", value=json.dumps(model.parameters or {})),
            client.V1EnvVar(name="PACKAGE_INPUT_FOLDER", value="true" if model.package_input_folder else "false"),
            client.V1EnvVar(name="OUTPUT_FORMATS", value=json.dumps(model.output_formats or ["ipynb"]))
        ]

        # Add AWS credentials if available
        if os.environ.get("AWS_ACCESS_KEY_ID"):
            env_vars.append(client.V1EnvVar(name="AWS_ACCESS_KEY_ID", value=os.environ["AWS_ACCESS_KEY_ID"]))
        if os.environ.get("AWS_SECRET_ACCESS_KEY"):
            env_vars.append(client.V1EnvVar(name="AWS_SECRET_ACCESS_KEY", value=os.environ["AWS_SECRET_ACCESS_KEY"]))

        # Add user environment variables
        if model.environment_variables:
            for name, value in model.environment_variables.items():
                env_vars.append(client.V1EnvVar(name=name, value=str(value)))

        # Extract resource requirements
        resources = {}
        if model.runtime_environment_parameters:
            k8s_cpu = model.runtime_environment_parameters.get('k8s_cpu')
            k8s_memory = model.runtime_environment_parameters.get('k8s_memory')
            k8s_gpu = model.runtime_environment_parameters.get('k8s_gpu')

            if k8s_cpu or k8s_memory or k8s_gpu:
                resources = client.V1ResourceRequirements(
                    limits={},
                    requests={}
                )
                if k8s_cpu:
                    resources.limits["cpu"] = k8s_cpu
                    resources.requests["cpu"] = k8s_cpu
                if k8s_memory:
                    resources.limits["memory"] = k8s_memory
                    resources.requests["memory"] = k8s_memory
                if k8s_gpu and int(k8s_gpu) > 0:
                    resources.limits["nvidia.com/gpu"] = str(k8s_gpu)

        # Detect appropriate image pull policy
        image_pull_policy = self._detect_image_pull_policy()

        # Create container spec
        container = client.V1Container(
            name="notebook-executor",
            image=os.environ.get("K8S_IMAGE", "jupyter-scheduler-k8s:latest"),
            image_pull_policy=image_pull_policy,
            env=env_vars,
            resources=resources if resources else None
        )

        # Create pod template
        return client.V1PodTemplateSpec(
            metadata=client.V1ObjectMeta(
                labels={
                    "app.kubernetes.io/managed-by": "jupyter-scheduler-k8s",
                    "scheduler.jupyter.org/job-definition-id": job_definition_id,
                    "scheduler.jupyter.org/triggered-by": "schedule"
                }
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[container]
            )
        )