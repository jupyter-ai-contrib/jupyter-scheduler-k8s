"""K8s-based ORM replacement for jupyter-scheduler."""

import json
import logging
import time
from typing import Any, Dict
from kubernetes import client, config
from kubernetes.client.rest import ApiException


logger = logging.getLogger(__name__)


class K8sSession:
    """K8s-native session that creates actual K8s resources.

    Unlike SQLAlchemy, K8s operations are immediate - no transactions.
    We implement the session interface for compatibility with jupyter-scheduler.
    """

    # Class-level flag to track if we've logged the connection type
    _connection_logged = False

    def __init__(self, namespace: str = "default"):
        self.namespace = namespace
        self._init_k8s_client()
        
    def _init_k8s_client(self):
        """Initialize K8s client."""
        try:
            config.load_incluster_config()
            if not K8sSession._connection_logged:
                logger.info("Using in-cluster K8s configuration")
                K8sSession._connection_logged = True
        except config.ConfigException:
            try:
                config.load_kube_config()
                # Only log once per process lifetime
                if not K8sSession._connection_logged:
                    logger.info("Using kubeconfig K8s configuration")
                    K8sSession._connection_logged = True
            except Exception as e:
                logger.error(f"❌ Failed to load K8s configuration: {e}")
                raise RuntimeError(
                    "Cannot connect to Kubernetes cluster. "
                    "Ensure kubectl is configured or running in cluster. "
                    f"Error: {e}"
                )
        
        try:
            self.k8s_batch = client.BatchV1Api()
            self.k8s_core = client.CoreV1Api()

            # Validate connectivity before proceeding
            self.k8s_core.list_namespace(limit=1)
        except Exception as e:
            logger.error(f"❌ Failed to initialize K8s clients: {e}")
            raise RuntimeError(f"Kubernetes API connection failed: {e}")
    
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        
    def query(self, model_class):
        """Create query for model class."""
        return K8sQuery(self, model_class)
        
    def add(self, job_model):
        """Create K8s resource immediately - K8s doesn't have transactions.

        This is called by jupyter-scheduler to create job records.
        We create actual K8s Jobs instead of database records.
        """
        import uuid
        from jupyter_scheduler.utils import get_utc_timestamp

        # Check what type of object this is
        if hasattr(job_model, 'job_id'):
            # It's a Job - create K8s Job
            if job_model.job_id is None:
                # Generate UUID for job_id (internal identifier)
                job_model.job_id = str(uuid.uuid4())
                logger.info(f"✅ K8sSession: Generated job_id={job_model.job_id}")

            # Ensure required fields on model
            if not hasattr(job_model, 'create_time') or job_model.create_time is None:
                job_model.create_time = get_utc_timestamp()

            if not hasattr(job_model, 'update_time') or job_model.update_time is None:
                job_model.update_time = get_utc_timestamp()

            if not hasattr(job_model, 'url') or job_model.url is None:
                job_model.url = f"/jobs/{job_model.job_id}"

            if not hasattr(job_model, 'status') or job_model.status is None:
                job_model.status = 'CREATED'

            # No-op for regular jobs - ExecutionManager will create the K8s Job
            # ExecutionManager will receive job data through constructor
            logger.info(f"K8sSession: Job {job_model.job_id} registered (will be created by ExecutionManager)")

        elif hasattr(job_model, 'job_definition_id'):
            # It's a JobDefinition - create K8s CronJob
            logger.info(f"K8sSession: Creating K8s CronJob for job_definition_id={job_model.job_definition_id}")
            # Note: This is typically handled by K8sScheduler, not here
            logger.warning("JobDefinition creation should be handled by K8sScheduler")
        else:
            logger.warning(f"K8sSession: Unknown model type: {type(job_model)}")
        
    def commit(self):
        """No-op - K8s operations are immediate."""
        pass
    
    def rollback(self):
        """No-op - K8s doesn't support transactions."""
        pass
    
    def _sanitize(self, value: str) -> str:
        """Sanitize value for K8s labels."""
        # K8s labels must be alphanumeric, max 63 chars
        value = str(value).lower()
        value = ''.join(c if c.isalnum() or c in '-_.' else '-' for c in value)
        return value.strip('-_.')[:63] or "none"


class K8sQuery:
    """K8s query that mimics SQLAlchemy Query interface."""

    def __init__(self, session: K8sSession, model_class):
        self.session = session
        self.model_class = model_class
        self._filters = {}
        self._label_filters = {}
        self._limit = None
        self._offset = None
        self._order_by_fields = []

        # Note: K8sQuery handles both Jobs and JobDefinitions
        # Jobs are K8s Job resources, JobDefinitions are K8s CronJobs
        self._is_job_definition_query = (
            hasattr(model_class, '__name__') and
            model_class.__name__ == 'JobDefinition'
        )
        
    def filter(self, condition):
        """Add filter condition."""
        # Convert SQLAlchemy conditions to K8s label selectors
        if hasattr(condition, 'left') and hasattr(condition.left, 'name'):
            field_name = condition.left.name

            # Extract the actual value from SQLAlchemy expressions
            if hasattr(condition.right, 'value'):
                value = condition.right.value
            elif hasattr(condition.right, 'element'):
                # Handle bindparam and other wrapped types
                value = condition.right.element.value if hasattr(condition.right.element, 'value') else condition.right
            else:
                value = condition.right

            # Special handling for None comparisons (e.g., schedule != None)
            # SQLAlchemy uses special None_ type which we need to convert
            if value is None or str(type(value).__name__) == 'NoneType':
                value = None

            # Map queryable fields to labels for exact matches
            if field_name in ['job_id', 'status', 'name', 'job_definition_id', 'idempotency_token']:
                label_key = f"scheduler.jupyter.org/{field_name.replace('_', '-')}"
                self._label_filters[label_key] = self.session._sanitize(str(value))
            elif field_name == 'start_time':
                # start_time uses >= comparison, must be post-filtered
                self._filters['start_time'] = value
            elif field_name == 'tags':
                # tags uses contains, must be post-filtered
                self._filters['tags'] = value
            else:
                # Unknown fields go to post-filtering
                self._filters[field_name] = value
        elif hasattr(condition, 'type') and condition.type.name == 'in_':
            # IN clauses need post-query filtering since K8s labels don't support OR
            field_name = condition.left.name
            if field_name == 'status':
                # Multiple values require post-query filtering
                self._filters['status_in'] = [self.session._sanitize(str(v)) for v in condition.right.value]
        
        return self
    
    def filter_by(self, **kwargs):
        """Helper for filtering queries by exact field values."""
        # Fields that can be filtered via labels (exact match only)
        LABEL_FIELDS = {'job_id', 'status', 'name', 'job_definition_id', 'idempotency_token'}

        # Fields that require post-filtering
        POST_FILTER_FIELDS = {'start_time', 'tags', 'input_filename', 'runtime_environment_name'}

        for field_name, value in kwargs.items():
            if field_name in LABEL_FIELDS:
                label_key = f"scheduler.jupyter.org/{field_name.replace('_', '-')}"
                self._label_filters[label_key] = self.session._sanitize(str(value))
            elif field_name in POST_FILTER_FIELDS:
                self._filters[field_name] = value
            else:
                # Fail fast for unsupported fields
                raise ValueError(
                    f"Field '{field_name}' is not supported for filtering. "
                    f"Supported fields: {LABEL_FIELDS | POST_FILTER_FIELDS}"
                )
        return self

    def count(self):
        """Return count of matching results."""
        if self._is_job_definition_query:
            definitions = self._get_matching_job_definitions()
            return len(definitions)
        jobs = self._get_matching_jobs()
        return len(jobs)

    def limit(self, limit_value):
        """Set limit for query results."""
        self._limit = limit_value
        return self

    def offset(self, offset_value):
        """Set offset for query results."""
        self._offset = offset_value
        return self

    def order_by(self, *args):
        """Set ordering for query results."""
        for arg in args:
            # Handle both desc(Job.field) and Job.field formats
            if hasattr(arg, 'element') and hasattr(arg.element, 'name'):
                # It's a desc() or asc() wrapped field
                field_name = arg.element.name
                is_desc = hasattr(arg, 'modifier') and 'desc' in str(arg.modifier).lower()
                self._order_by_fields.append((field_name, is_desc))
            elif hasattr(arg, 'name'):
                # It's a plain field
                self._order_by_fields.append((arg.name, False))
        return self

    def update(self, values: Dict):
        """Update matching jobs."""
        logger.info(f"K8sQuery: Updating jobs with values: {values}")

        # Use labels for efficient K8s filtering
        label_selector = ",".join([f"{k}={v}" for k, v in self._label_filters.items()])
        if not label_selector:
            raise ValueError("Update requires filterable conditions")

        logger.debug(f"K8sQuery: Using label selector for update: {label_selector}")

        # Query matching jobs using label selector
        jobs = self.session.k8s_batch.list_namespaced_job(
            namespace=self.session.namespace,
            label_selector=label_selector
        )

        logger.info(f"K8sQuery: Found {len(jobs.items)} jobs to update")
        
        for job in jobs.items:
            # Merge new values into existing job data
            if job.metadata.annotations and "scheduler.jupyter.org/job-data" in job.metadata.annotations:
                job_data = json.loads(job.metadata.annotations["scheduler.jupyter.org/job-data"])
                job_data.update(values)
                job_data["update_time"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

                # Store updated data back to annotation
                job.metadata.annotations["scheduler.jupyter.org/job-data"] = json.dumps(job_data)
                
                # Sync searchable fields to labels for query performance
                for field, value in values.items():
                    if field in ['status', 'name']:
                        label_key = f"scheduler.jupyter.org/{field.replace('_', '-')}"
                        job.metadata.labels[label_key] = self.session._sanitize(str(value))
                
                # Apply changes to K8s resource
                self.session.k8s_batch.patch_namespaced_job(
                    name=job.metadata.name,
                    namespace=self.session.namespace,
                    body=job
                )
                logger.debug(f"K8sQuery: Updated job {job.metadata.name}")
    
    def one(self):
        """Get single job or raise."""
        result = self.first()
        if result is None:
            raise ValueError("Job not found")
        return result
        
    def first(self):
        """Get first matching result."""
        if self._is_job_definition_query:
            definitions = self._get_matching_job_definitions()
            return definitions[0] if definitions else None

        # Optimization: If querying by job_id only, try direct K8s name lookup first
        if self._label_filters.get("scheduler.jupyter.org/job-id") and len(self._label_filters) == 1:
            job_id = self._label_filters["scheduler.jupyter.org/job-id"]

            # For CronJob-spawned jobs, job_id might be the K8s name itself
            # Try direct lookup by name (O(1) operation)
            if job_id.startswith("nb-jobdef-"):
                try:
                    k8s_job = self.session.k8s_batch.read_namespaced_job(
                        name=job_id,
                        namespace=self.session.namespace
                    )
                    if k8s_job.metadata.annotations and "scheduler.jupyter.org/job-data" in k8s_job.metadata.annotations:
                        job_data = json.loads(k8s_job.metadata.annotations["scheduler.jupyter.org/job-data"])
                        # Ensure job_id is set to K8s name for CronJob-spawned jobs
                        if job_data.get('job_id') == 'PENDING':
                            job_data['job_id'] = k8s_job.metadata.name
                        return self._dict_to_job(job_data)
                except:
                    # Not found by name, fall back to label query
                    pass

        jobs = self._get_matching_jobs()
        return jobs[0] if jobs else None
        
    def all(self):
        """Get all matching results."""
        if self._is_job_definition_query:
            return self._get_matching_job_definitions()
        return self._get_matching_jobs()
    
    def delete(self):
        """Delete matching jobs."""
        logger.info(f"K8sQuery: Deleting jobs with filters: {self._label_filters}")

        # Special case: If filtering by job_id that's a K8s name (CronJob-spawned)
        if self._label_filters.get("scheduler.jupyter.org/job-id") and len(self._label_filters) == 1:
            job_id = self._label_filters["scheduler.jupyter.org/job-id"]

            # CronJob-spawned jobs use K8s name as job_id
            if job_id.startswith("nb-jobdef-"):
                logger.info(f"K8sQuery: Deleting CronJob-spawned job by name: {job_id}")
                try:
                    self.session.k8s_batch.delete_namespaced_job(
                        name=job_id,
                        namespace=self.session.namespace,
                        propagation_policy='Foreground'
                    )
                    logger.debug(f"K8sQuery: Deleted job {job_id}")
                    return
                except ApiException as e:
                    if e.status == 404:
                        logger.warning(f"Job {job_id} not found")
                        return
                    raise

        # Standard deletion using label selectors
        label_selector = ",".join([f"{k}={v}" for k, v in self._label_filters.items()])
        if not label_selector:
            raise ValueError("Delete requires filterable conditions")

        logger.debug(f"K8sQuery: Using label selector for delete: {label_selector}")

        jobs = self.session.k8s_batch.list_namespaced_job(
            namespace=self.session.namespace,
            label_selector=label_selector
        )

        logger.info(f"K8sQuery: Found {len(jobs.items)} jobs to delete")

        for job in jobs.items:
            self.session.k8s_batch.delete_namespaced_job(
                name=job.metadata.name,
                namespace=self.session.namespace,
                propagation_policy='Foreground'
            )
            logger.debug(f"K8sQuery: Deleted job {job.metadata.name}")
    
    def _get_matching_jobs(self):
        """Query jobs matching filters."""
        # Use labels for efficient server-side filtering
        label_selector = ",".join([f"{k}={v}" for k, v in self._label_filters.items()])

        # When no filters specified, query ALL managed jobs (including CronJob-spawned)
        # This ensures we find jobs that don't have job-id labels (e.g., CronJob-spawned)
        if not label_selector:
            label_selector = "app.kubernetes.io/managed-by=jupyter-scheduler-k8s"

        logger.debug(f"K8sQuery: Querying jobs with label_selector: {label_selector}")
        logger.debug(f"K8sQuery: Post-filters: {self._filters}")

        jobs = self.session.k8s_batch.list_namespaced_job(
            namespace=self.session.namespace,
            label_selector=label_selector
        )

        logger.debug(f"K8sQuery: Found {len(jobs.items)} K8s jobs matching labels")

        results = []
        for job in jobs.items:
            if job.metadata.annotations and "scheduler.jupyter.org/job-data" in job.metadata.annotations:
                job_data = json.loads(job.metadata.annotations["scheduler.jupyter.org/job-data"])

                # Derive job_id from K8s job name if it's PENDING (CronJob-spawned)
                if job_data.get('job_id') == 'PENDING' and job.metadata.name:
                    # For CronJob-spawned jobs, use the entire K8s name as the job_id
                    # This ensures uniqueness since K8s guarantees unique names
                    # Example: nb-jobdef-50adecf0-29300576-abcde becomes the job_id
                    job_data['job_id'] = job.metadata.name
                    logger.debug(f"K8sQuery: Derived job_id from K8s name: {job.metadata.name}")
                    # Also update the URL to match
                    job_data['url'] = f"/jobs/{job.metadata.name}"

                    # Use K8s creationTimestamp for accurate timing instead of static template values
                    if job.metadata.creation_timestamp:
                        timestamp_ms = int(job.metadata.creation_timestamp.timestamp() * 1000)
                        job_data['create_time'] = timestamp_ms
                        job_data['update_time'] = timestamp_ms
                        job_data['start_time'] = timestamp_ms
                        logger.debug(f"K8sQuery: Updated timestamps from K8s creationTimestamp: {timestamp_ms}")

                # Ensure url field is present (required for DescribeJob)
                elif not job_data.get('url'):
                    job_data['url'] = f"/jobs/{job_data.get('job_id', '')}"

                # Fix update_time type consistency (should be int, not string)
                if isinstance(job_data.get('update_time'), str):
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(job_data['update_time'].replace('Z', '+00:00'))
                        job_data['update_time'] = int(dt.timestamp() * 1000)
                    except:
                        from jupyter_scheduler.utils import get_utc_timestamp
                        job_data['update_time'] = get_utc_timestamp()

                # Derive actual status from K8s Job status (overrides annotation value)
                # This is critical for CronJob-spawned jobs which start with IN_PROGRESS
                if job.status:
                    if job.status.succeeded and job.status.succeeded > 0:
                        job_data['status'] = 'COMPLETED'
                        logger.debug(f"K8sQuery: Job {job.metadata.name} status: COMPLETED (K8s succeeded={job.status.succeeded})")
                    elif job.status.failed and job.status.failed > 0:
                        job_data['status'] = 'FAILED'
                        logger.debug(f"K8sQuery: Job {job.metadata.name} status: FAILED (K8s failed={job.status.failed})")
                    # If active or no completion info, keep existing status (likely IN_PROGRESS)

                # Apply post-filters for complex conditions not supported by label selectors
                if self._matches_post_filters(job_data):
                    results.append(self._dict_to_job(job_data))
                    logger.debug(f"K8sQuery: Included job {job_data.get('job_id', 'NO_ID')} ({job.metadata.name})")
                else:
                    logger.debug(f"K8sQuery: Filtered out job {job.metadata.name} - didn't match post-filters")
            else:
                logger.debug(f"K8sQuery: Skipped job {job.metadata.name} - no scheduler annotation")

        # Apply ordering if specified
        if self._order_by_fields:
            for field_name, is_desc in reversed(self._order_by_fields):
                # Use a default value that works for sorting (0 for numbers, empty string for text)
                results.sort(key=lambda x: getattr(x, field_name, None) or 0, reverse=is_desc)

        # Apply offset and limit for pagination
        if self._offset is not None:
            results = results[self._offset:]
        if self._limit is not None:
            results = results[:self._limit]

        logger.debug(f"K8sQuery: Returning {len(results)} jobs after all filtering, sorting, and pagination")
        return results
    
    def _matches_post_filters(self, job_data: Dict) -> bool:
        """Apply post-filters for conditions not supported by K8s label selectors."""
        for field, value in self._filters.items():
            if field == 'status_in':
                # Handle IN clause for status
                if job_data.get('status') not in value:
                    return False
            elif field == 'start_time':
                # Handle >= comparison for start_time
                if not job_data.get('start_time') or job_data['start_time'] < value:
                    return False
            elif field == 'name' and isinstance(value, str) and value.endswith('%'):
                # Handle LIKE queries (name LIKE 'prefix%')
                actual_value = job_data.get('name', "")
                if not actual_value.startswith(value.rstrip('%')):
                    return False
            elif field == 'tags':
                # Handle tags contains
                job_tags = job_data.get('tags', [])
                if not all(tag in job_tags for tag in value):
                    return False
            else:
                # Direct equality comparison
                if job_data.get(field) != value:
                    return False
        return True
    
    def _dict_to_job(self, job_data: Dict):
        """Convert dict to Job-like object."""
        from jupyter_scheduler.utils import get_utc_timestamp

        # Ensure required fields have default values for old jobs
        if not job_data.get('create_time'):
            job_data['create_time'] = get_utc_timestamp()
        if not job_data.get('update_time'):
            job_data['update_time'] = get_utc_timestamp()
        if not job_data.get('url'):
            job_data['url'] = f"/jobs/{job_data.get('job_id', 'unknown')}"
        if not job_data.get('status'):
            job_data['status'] = 'CREATED'

        class JobRecord:
            def __init__(self, data):
                for k, v in data.items():
                    setattr(self, k, v)

        return JobRecord(job_data)

    def _get_matching_job_definitions(self):
        """Get matching JobDefinitions from K8s CronJobs."""
        try:
            # Get all CronJobs managed by jupyter-scheduler-k8s
            label_selector = "app.kubernetes.io/managed-by=jupyter-scheduler-k8s"

            cronjobs = self.session.k8s_batch.list_namespaced_cron_job(
                namespace=self.session.namespace,
                label_selector=label_selector
            )

            definitions = []
            for cronjob in cronjobs.items:
                # Extract JobDefinition data from CronJob annotations
                if cronjob.metadata.annotations and "scheduler.jupyter.org/job-definition-data" in cronjob.metadata.annotations:
                    job_def_data = json.loads(cronjob.metadata.annotations["scheduler.jupyter.org/job-definition-data"])

                    # Apply post-filters if needed (e.g., schedule != None)
                    if self._matches_post_filters(job_def_data):
                        # Convert to JobDefinition-like object
                        class JobDefinitionRecord:
                            def __init__(self, data):
                                for k, v in data.items():
                                    setattr(self, k, v)

                        definitions.append(JobDefinitionRecord(job_def_data))

            return definitions

        except Exception as e:
            logger.error(f"Failed to query JobDefinitions: {e}")
            return []