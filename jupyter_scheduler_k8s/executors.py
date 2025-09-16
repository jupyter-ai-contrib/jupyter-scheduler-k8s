import os
import json
import time
import datetime
import logging
import subprocess
import sys
import nbformat

from pathlib import Path
from typing import Dict
from kubernetes import client, config, watch

# Configuration constants
MAX_ENV_VARS_TO_LOG = 10

# Protected environment variables that cannot be overridden by users
PROTECTED_ENV_VARS = {
    'S3_INPUT_PREFIX', 'S3_OUTPUT_PREFIX', 'NOTEBOOK_PATH',
    'OUTPUT_PATH', 'PARAMETERS', 'OUTPUT_FORMATS', 'PACKAGE_INPUT_FOLDER',
    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
    'AWS_DEFAULT_REGION', 'AWS_REGION', 'S3_ENDPOINT_URL'
}
from kubernetes.client.rest import ApiException
from jupyter_scheduler.executors import ExecutionManager
from jupyter_scheduler.models import JobFeature, Status


class JobStoppedException(Exception):
    """Raised when a job is stopped by user request."""
    pass


logger = logging.getLogger(__name__)


class K8sExecutionManager(ExecutionManager):
    """Executes Jupyter Scheduler jobs as Kubernetes Jobs."""

    def __init__(
        self, job_id: str, root_dir: str, db_url: str, staging_paths: Dict[str, str], database_manager_class
    ):
        super().__init__(job_id, root_dir, db_url, staging_paths, database_manager_class)
        
        logger.info("Initializing K8sExecutionManager")
        
        self.s3_bucket = os.environ.get("S3_BUCKET")
        if not self.s3_bucket:
            logger.error("❌ S3_BUCKET environment variable not set")
            logger.error("Required: export S3_BUCKET=\"your-bucket-name\"")
            raise ValueError("S3_BUCKET environment variable is required")
        
        self.s3_endpoint_url = os.environ.get("S3_ENDPOINT_URL")
        self.namespace = os.environ.get("K8S_NAMESPACE", "default")
        self.image = os.environ.get("K8S_IMAGE", "jupyter-scheduler-k8s:latest")
        
        self.executor_memory_request = os.environ.get(
            "K8S_EXECUTOR_MEMORY_REQUEST", "512Mi"
        )
        self.executor_memory_limit = os.environ.get("K8S_EXECUTOR_MEMORY_LIMIT", "2Gi")
        self.executor_cpu_request = os.environ.get("K8S_EXECUTOR_CPU_REQUEST", "500m")
        self.executor_cpu_limit = os.environ.get("K8S_EXECUTOR_CPU_LIMIT", "2000m")

        # Auto-detect pull policy based on cluster type
        default_pull_policy = self._detect_image_pull_policy()
        self.image_pull_policy = os.environ.get(
            "K8S_IMAGE_PULL_POLICY", default_pull_policy
        )

        self.k8s_core = None
        self.k8s_batch = None
    
    def _create_database_manager(self, database_manager_class):
        """Create database manager, handling both string and class types."""
        if isinstance(database_manager_class, str):
            # Use parent's implementation for string type
            return super()._create_database_manager(database_manager_class)
        else:
            # Handle class object directly
            return database_manager_class()

    def _detect_image_pull_policy(self) -> str:
        """Auto-detect appropriate image pull policy based on K8s context."""
        try:
            config.load_kube_config()
            contexts, active_context = config.list_kube_config_contexts()
            if active_context and active_context.get("name"):
                context_name = active_context["name"]

                # If context suggests local development, use Never
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
    
    def process(self):
        """Override process to handle JobStoppedException and set STOPPED status."""
        self.before_start()
        try:
            self.execute()
        except JobStoppedException as e:
            # Job was stopped by user - mark as STOPPED, not FAILED
            logger.info(f"Job {self.job_id} was stopped: {e}")
            with self.db_session() as session:
                from jupyter_scheduler.orm import Job
                from jupyter_scheduler.utils import get_utc_timestamp
                session.query(Job).filter(Job.job_id == self.job_id).update(
                    {"status": Status.STOPPED, "end_time": get_utc_timestamp()}
                    # Note: No status_message to match vanilla behavior (no red banner)
                )
                session.commit()
        except Exception as e:
            # Other exceptions are failures
            self.on_failure(e)
        else:
            self.on_complete()

    @classmethod
    def supported_features(cls) -> Dict[JobFeature, bool]:
        return {
            JobFeature.parameters: True,
            JobFeature.timeout_seconds: False,
            JobFeature.output_formats: True,
            JobFeature.job_name: False,
            JobFeature.stop_job: True,  # Now supported via K8sScheduler
            JobFeature.delete_job: True,  # Now supported via K8sScheduler
        }

    def validate(self, input_path: str) -> bool:
        with open(input_path, encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)
            try:
                nb.metadata.kernelspec["name"]
                return True
            except:
                return False

    def execute(self):
        logger.info("=== K8s ExecutionManager starting ===")
        self._init_k8s_clients()

        job_name = f"nb-job-{self.job_id[:8]}"

        logger.info(f"Starting K8s job {job_name} in namespace {self.namespace}")
        logger.info(f"Using image: {self.image}")
        logger.info(f"Using S3 bucket: {self.s3_bucket}")
        logger.info(f"Job ID: {self.job_id}")
        logger.info(f"Staging paths: {self.staging_paths}")

        try:
            self._execute_with_s3(job_name)
        except JobStoppedException as e:
            logger.info(f"Job {job_name} stopped by user")
            raise
        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            raise

    def _init_k8s_clients(self):
        if self.k8s_core is not None:
            return

        try:
            config.load_incluster_config()
        except config.ConfigException:
            try:
                config.load_kube_config()
            except config.ConfigException:
                raise RuntimeError("No Kubernetes cluster configured")

        try:
            self.k8s_core = client.CoreV1Api()
            self.k8s_batch = client.BatchV1Api()
            self.k8s_core.list_namespace(limit=1)
        except Exception as e:
            raise RuntimeError(f"Cannot connect to Kubernetes cluster: {e}")

        try:
            self.k8s_core.read_namespace(name=self.namespace)
        except ApiException as e:
            if e.status == 404:
                ns = client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=self.namespace)
                )
                self.k8s_core.create_namespace(body=ns)
            else:
                raise

    def _execute_with_s3(self, job_name: str):
        """Execute job using S3 storage for durability."""
        s3_prefix = f"s3://{self.s3_bucket}/job-{self.job_id}"
        s3_input_prefix = f"{s3_prefix}/inputs/"
        s3_output_prefix = f"{s3_prefix}/outputs/"

        try:
            # Upload staging files to S3
            self._upload_to_s3(s3_input_prefix)

            # Create job with S3 configuration
            job = self._create_s3_execution_job(
                job_name, s3_input_prefix, s3_output_prefix
            )
            
            logger.info(f"🚀 Creating Kubernetes job '{job_name}' in namespace '{self.namespace}'")
            logger.info(f"    Image: {self.image}")
            logger.info(f"    Resource limits: {self.executor_memory_limit} memory, {self.executor_cpu_limit} CPU")
            
            # Log environment variables summary (never log values)
            user_env_dict = self._extract_user_env_vars()
            
            if user_env_dict:
                names = sorted(user_env_dict.keys())
                if len(names) > MAX_ENV_VARS_TO_LOG:
                    logger.info(f"    Environment variables: {len(names)} user variables configured")
                else:
                    logger.info(f"    Environment variables: {', '.join(names)}")
            
            self.k8s_batch.create_namespaced_job(namespace=self.namespace, body=job)
            logger.info(f"✅ Kubernetes job '{job_name}' created successfully")

            logger.info("⏳ Waiting for job completion...")
            self._wait_for_job_completion(job_name, timeout=600)
            logger.info("🎉 Job completed successfully")

            # Download outputs from S3
            self._download_from_s3(s3_output_prefix)

        finally:
            logger.info(f"🗃️  Execution job '{job_name}' preserved as database record")

    def _wait_for_job_completion(self, job_name: str, timeout: int = 600):
        """Wait for job to complete using Watch API."""
        w = watch.Watch()
        start_time = time.time()
        
        # Configurable scheduling timeout for autoscaling clusters
        scheduling_timeout = int(os.environ.get('K8S_SCHEDULING_TIMEOUT', '300'))

        try:
            for event in w.stream(
                self.k8s_batch.list_namespaced_job,
                namespace=self.namespace,
                field_selector=f"metadata.name={job_name}",
                timeout_seconds=timeout,
            ):
                job = event["object"]
                job_status = job.status

                # Check if job was suspended (user stopped it)
                # K8s sets suspend=True when a job is suspended
                if hasattr(job.spec, 'suspend') and job.spec.suspend:
                    logger.info(f"Job {job_name} was stopped by user")
                    w.stop()
                    # Raise our custom exception for stopped jobs
                    raise JobStoppedException(f"Job {job_name} was stopped by user request")

                # Check for pod scheduling issues
                elapsed = time.time() - start_time
                # Wait 30s before checking to allow pod creation and initial scheduling attempt
                if elapsed > 30:
                    pods = self.k8s_core.list_namespaced_pod(
                        namespace=self.namespace,
                        label_selector=f"job-name={job_name}"
                    )
                    
                    for pod in pods.items:
                        if pod.status.phase == "Pending":
                            # Check if we've exceeded scheduling timeout
                            if elapsed > scheduling_timeout:
                                error_msg = self._extract_scheduling_error(pod)
                                if error_msg:
                                    logger.error(f"Job {job_name} scheduling failed: {error_msg}")
                                    self._update_job_status_message(job_name, error_msg)
                                    w.stop()
                                    raise RuntimeError(error_msg)

                if job_status.succeeded:
                    logger.info(f"Job {job_name} completed successfully")
                    w.stop()
                    return

                if job_status.failed:
                    logger.error(f"Job {job_name} failed")
                    # Get pod logs for debugging
                    try:
                        pods = self.k8s_core.list_namespaced_pod(
                            namespace=self.namespace, 
                            label_selector=f"job-name={job_name}"
                        )
                        for pod in pods.items:
                            logger.error(f"Pod {pod.metadata.name} status: {pod.status.phase}")
                            if pod.status.container_statuses:
                                for container in pod.status.container_statuses:
                                    if container.state.waiting:
                                        logger.error(f"Container waiting: {container.state.waiting.reason} - {container.state.waiting.message}")
                                    elif container.state.terminated:
                                        logger.error(f"Container terminated: exit_code={container.state.terminated.exit_code}, reason={container.state.terminated.reason}")
                            
                            # Get pod logs
                            try:
                                logs = self.k8s_core.read_namespaced_pod_log(
                                    name=pod.metadata.name,
                                    namespace=self.namespace,
                                    tail_lines=50
                                )
                                logger.error(f"Pod logs:\n{logs}")
                            except Exception as log_error:
                                logger.error(f"Could not get pod logs: {log_error}")
                    except Exception as debug_error:
                        logger.error(f"Could not get pod debugging info: {debug_error}")
                    
                    w.stop()
                    raise RuntimeError(f"Job {job_name} failed")

                if time.time() - start_time > timeout:
                    w.stop()
                    raise RuntimeError(
                        f"Job {job_name} timed out after {timeout} seconds"
                    )

        except JobStoppedException:
            # Re-raise JobStoppedException without wrapping - it's expected behavior
            w.stop()
            raise
        except Exception as e:
            w.stop()
            raise RuntimeError(f"Error waiting for job completion: {e}")
    
    def _extract_scheduling_error(self, pod) -> str:
        """Extract user-friendly error message from pod scheduling issues."""
        # Check pod conditions for scheduling problems
        if pod.status.conditions:
            for condition in pod.status.conditions:
                if condition.type == "PodScheduled" and condition.status == "False":
                    # Parse the K8s scheduler message into something readable
                    msg = condition.message or condition.reason
                    
                    # Common scheduling failures with better messages
                    if "Insufficient nvidia.com/gpu" in msg:
                        return f"Cannot schedule: No nodes with GPU available"
                    
                    elif "Insufficient memory" in msg:
                        return f"Cannot schedule: Insufficient memory available"
                    
                    elif "Insufficient cpu" in msg:
                        return f"Cannot schedule: Insufficient CPU available"
                    
                    else:
                        # Generic scheduling failure
                        return f"Cannot schedule: {condition.reason} - {condition.message}"
        
        # Check pod events for additional context
        try:
            events = self.k8s_core.list_namespaced_event(
                namespace=self.namespace,
                field_selector=f"involvedObject.name={pod.metadata.name}"
            )
            for event in events.items:
                if event.reason == "FailedScheduling":
                    return f"Cannot schedule: {event.message}"
        except Exception:
            pass
        
        return "Cannot schedule: Pod is pending, check cluster resources"
    
    def _update_job_status_message(self, job_name: str, error_msg: str):
        """Update K8s Job annotation with status message for UI display."""
        try:
            # Get the current job
            job = self.k8s_batch.read_namespaced_job(
                name=job_name,
                namespace=self.namespace
            )
            
            # Update the job data annotation with error message
            if job.metadata.annotations and "scheduler.jupyter.org/job-data" in job.metadata.annotations:
                job_data = json.loads(job.metadata.annotations["scheduler.jupyter.org/job-data"])
                job_data["status"] = "FAILED"
                job_data["status_message"] = error_msg
                job.metadata.annotations["scheduler.jupyter.org/job-data"] = json.dumps(job_data)
                
                # Also update status label for queries
                job.metadata.labels["scheduler.jupyter.org/status"] = "failed"
                
                # Patch the job
                self.k8s_batch.patch_namespaced_job(
                    name=job_name,
                    namespace=self.namespace,
                    body=job
                )
                logger.info(f"Updated job {job_name} with error: {error_msg}")
        except Exception as e:
            logger.error(f"Failed to update job status message: {e}")

    def _upload_to_s3(self, s3_input_prefix: str):
        """Upload staging files to S3 using AWS CLI."""
        staging_dir = Path(self.staging_paths["input"]).parent
        
        logger.info(f"📤 Uploading files from {staging_dir} to S3...")
        logger.info(f"    Source: {staging_dir}")
        logger.info(f"    Destination: {s3_input_prefix}")

        cmd = ["aws", "s3", "sync", str(staging_dir), s3_input_prefix, "--quiet"]

        if self.s3_endpoint_url:
            cmd.extend(["--endpoint-url", self.s3_endpoint_url])
            logger.info(f"    Using S3 endpoint: {self.s3_endpoint_url}")

        logger.info(f"    Command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"❌ S3 upload failed: {result.stderr}")
            raise RuntimeError(f"Failed to upload files to S3: {result.stderr}")

        logger.info(f"✅ Files successfully uploaded to {s3_input_prefix}")

    def _extract_user_env_vars(self):
        """Extract and validate user environment variables from model.
        Returns a dict filtered for protected variables.
        """
        user_env_dict = {}

        if self.model.environment_variables:
            logger.info(f"Processing {len(self.model.environment_variables)} environment variables from model")
            for name, value in self.model.environment_variables.items():
                if name not in PROTECTED_ENV_VARS:
                    user_env_dict[name] = str(value)
        else:
            logger.info("No environment variables in model")

        return user_env_dict
    
    def _download_from_s3(self, s3_output_prefix: str):
        """Download output files from S3 to staging directory using AWS CLI."""
        # Use available staging path to determine directory
        staging_path = self.staging_paths.get("ipynb") or self.staging_paths.get("input")
        staging_dir = Path(staging_path).parent
        
        logger.info(f"📥 Downloading files from S3 to {staging_dir}...")
        logger.info(f"    Source: {s3_output_prefix}")
        logger.info(f"    Destination: {staging_dir}")

        cmd = ["aws", "s3", "sync", s3_output_prefix, str(staging_dir), "--quiet"]

        if self.s3_endpoint_url:
            cmd.extend(["--endpoint-url", self.s3_endpoint_url])
            logger.info(f"    Using S3 endpoint: {self.s3_endpoint_url}")

        logger.info(f"    Command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"❌ S3 download failed: {result.stderr}")
            raise RuntimeError(f"Failed to download files from S3: {result.stderr}")

        logger.info(f"✅ Files successfully downloaded from {s3_output_prefix}")

    def _create_s3_execution_job(
        self, job_name: str, s3_input_prefix: str, s3_output_prefix: str
    ) -> client.V1Job:
        """Create K8s Job that executes notebook using S3 for file storage."""

        # Environment variables for S3-based execution
        env_vars = [
            client.V1EnvVar(name="S3_INPUT_PREFIX", value=s3_input_prefix),
            client.V1EnvVar(name="S3_OUTPUT_PREFIX", value=s3_output_prefix),
            client.V1EnvVar(
                name="NOTEBOOK_PATH",
                value=f"/tmp/inputs/{Path(self.staging_paths['input']).name}",
            ),
            client.V1EnvVar(
                name="OUTPUT_PATH",
                value=f"/tmp/outputs/{Path(self.staging_paths.get('ipynb') or self.staging_paths.get('input')).name}",
            ),
            client.V1EnvVar(
                name="PARAMETERS", value=json.dumps(self.model.parameters or {})
            ),
            client.V1EnvVar(name="PACKAGE_INPUT_FOLDER", value="true"),
            client.V1EnvVar(
                name="OUTPUT_FORMATS", value=json.dumps(self.model.output_formats)
            ),
        ]

        # Add S3 endpoint URL if specified (for S3-compatible storage)
        if self.s3_endpoint_url:
            env_vars.append(
                client.V1EnvVar(name="S3_ENDPOINT_URL", value=self.s3_endpoint_url)
            )
            
        # Add AWS credentials from environment
        aws_credentials = [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY", 
            "AWS_SESSION_TOKEN",
            "AWS_DEFAULT_REGION",
            "AWS_REGION"
        ]
        passed_credentials = []
        for cred_var in aws_credentials:
            if cred_var in os.environ:
                env_vars.append(
                    client.V1EnvVar(name=cred_var, value=os.environ[cred_var])
                )
                passed_credentials.append(cred_var)
        
        if passed_credentials:
            logger.info(f"    Passing AWS credentials: {', '.join(passed_credentials)}")
        else:
            logger.warning("    ⚠️  No AWS credentials found in environment - container may fail")
        
        # Add user-defined environment variables (filtering protected ones)
        user_env_dict = self._extract_user_env_vars()
        
        # Check for any protected vars that were filtered
        if self.model.environment_variables:
            protected_attempted = [
                name for name in self.model.environment_variables.keys()
                if name in PROTECTED_ENV_VARS
            ]
            if protected_attempted:
                logger.warning(f"    Skipped protected environment variables: {', '.join(protected_attempted)}")
        
        # Add user env vars to container
        for name, value in user_env_dict.items():
            env_vars.append(client.V1EnvVar(name=name, value=value))
        
        # Log user env vars
        if user_env_dict:
            names = sorted(user_env_dict.keys())
            if len(names) > MAX_ENV_VARS_TO_LOG:
                logger.info(f"    User environment variables: {len(names)} variables defined")
            else:
                logger.info(f"    User environment variables: {', '.join(names)}")

        # Extract K8s resources from runtime environment parameters
        runtime_params = getattr(self.model, 'runtime_environment_parameters', {}) or {}
        k8s_cpu = runtime_params.get('k8s_cpu')
        k8s_memory = runtime_params.get('k8s_memory')
        k8s_gpu = runtime_params.get('k8s_gpu', 0)
        try:
            k8s_gpu = int(k8s_gpu) if k8s_gpu else 0
        except (ValueError, TypeError):
            k8s_gpu = 0
        
        resource_limits = {}
        resource_requests = {}
        
        if k8s_cpu:
            resource_limits["cpu"] = k8s_cpu
            resource_requests["cpu"] = k8s_cpu
            
        if k8s_memory:
            resource_limits["memory"] = k8s_memory
            resource_requests["memory"] = k8s_memory
        
        has_gpu = k8s_gpu and int(k8s_gpu) > 0
        if has_gpu:
            resource_limits["nvidia.com/gpu"] = str(k8s_gpu)
        
        if resource_limits or resource_requests:
            logger.info(f"    Resource configuration:")
            if k8s_cpu:
                logger.info(f"       CPU: {k8s_cpu}")
            if k8s_memory:
                logger.info(f"       Memory: {k8s_memory}")
            if has_gpu:
                logger.info(f"       GPU: {k8s_gpu}")
        else:
            logger.info(f"    Resource configuration: Using cluster defaults")
        
        # Build container spec
        container_spec = {
            "name": "notebook-executor",
            "image": self.image,
            "image_pull_policy": self.image_pull_policy,
            "env": env_vars,
        }
        
        # Only add resources if any were specified
        if resource_limits or resource_requests:
            container_spec["resources"] = client.V1ResourceRequirements(
                requests=resource_requests if resource_requests else None,
                limits=resource_limits if resource_limits else None,
            )
        
        main_container = client.V1Container(**container_spec)

        pod_spec = client.V1PodSpec(restart_policy="Never", containers=[main_container])

        job_spec = client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(name=job_name), spec=pod_spec
            ),
            # Don't retry on failure
            backoff_limit=0,
        )

        # Create labels for database-style querying
        labels = {
            "app.kubernetes.io/managed-by": "jupyter-scheduler-k8s",
            "scheduler.jupyter.org/job-id": self._sanitize(self.job_id),
            "scheduler.jupyter.org/status": self._sanitize(self.model.status or "pending"),
        }

        if hasattr(self.model, 'name') and self.model.name:
            labels["scheduler.jupyter.org/name"] = self._sanitize(self.model.name)

        if hasattr(self.model, 'job_definition_id') and self.model.job_definition_id:
            labels["scheduler.jupyter.org/job-definition-id"] = self._sanitize(self.model.job_definition_id)

        if hasattr(self.model, 'idempotency_token') and self.model.idempotency_token:
            labels["scheduler.jupyter.org/idempotency-token"] = self._sanitize(self.model.idempotency_token)

        # Note: start_time is not added as a label because:
        # 1. K8s labels can't do >= comparisons needed for start_time queries
        # 2. Storing timestamps as labels violates K8s best practices
        # 3. The data is available in annotations for post-filtering
        
        # Store complete job data in annotation for database queries
        job_data = {
            "job_id": self.job_id,
            "name": getattr(self.model, 'name', None),
            "status": getattr(self.model, 'status', 'pending'),
            "input_filename": getattr(self.model, 'input_filename', None),
            "runtime_environment_name": getattr(self.model, 'runtime_environment_name', None),
            "parameters": getattr(self.model, 'parameters', {}),
            "output_formats": getattr(self.model, 'output_formats', []),
            "create_time": getattr(self.model, 'create_time', None),
            "update_time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "k8s_resource_profile": getattr(self.model, 'k8s_resource_profile', ''),
            "k8s_cpu": k8s_cpu,
            "k8s_memory": k8s_memory,
            "k8s_gpu": k8s_gpu,
        }
        
        annotations = {
            "scheduler.jupyter.org/job-data": json.dumps(job_data)
        }
        
        k8s_job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name, labels=labels, annotations=annotations),
            spec=job_spec,
        )

        return k8s_job
    
    def _sanitize(self, value: str) -> str:
        """Sanitize value for K8s labels."""
        value = str(value).lower()
        value = ''.join(c if c.isalnum() or c in '-_.' else '-' for c in value)
        return value.strip('-_.')[:63] or "none"

