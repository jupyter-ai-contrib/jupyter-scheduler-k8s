"""K8s-based ORM replacement for jupyter-scheduler."""

import json
import logging
from typing import Any, Dict
from kubernetes import client, config
from kubernetes.client.rest import ApiException


logger = logging.getLogger(__name__)


class K8sSession:
    """K8s-backed session that mimics SQLAlchemy session interface."""
    
    def __init__(self, namespace: str = "default"):
        self.namespace = namespace
        self._pending_operations = []
        self._init_k8s_client()
        
    def _init_k8s_client(self):
        """Initialize K8s client."""
        try:
            config.load_incluster_config()
            logger.info("🔗 Using in-cluster K8s configuration")
        except config.ConfigException:
            try:
                config.load_kube_config()
                logger.info("🔗 Using kubeconfig K8s configuration")
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
            self.k8s_core.get_api_versions()
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
        
    def add(self, job):
        """Buffer job for batch commit."""
        self._pending_operations.append(('create', job))
        
    def commit(self):
        """Execute buffered operations."""
        self._pending_operations.clear()
    
    def rollback(self):
        """Clear pending operations."""
        self._pending_operations.clear()
    
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
        
        # Auto-add model-specific filters
        if hasattr(model_class, '__name__'):
            if model_class.__name__ == 'JobDefinition':
                self._label_filters['jupyter-scheduler.io/has-schedule'] = 'true'
            elif model_class.__name__ == 'Job':
                # Job table includes both scheduled and non-scheduled
                pass
        
    def filter(self, condition):
        """Add filter condition."""
        # Convert SQLAlchemy conditions to K8s label selectors or annotation filters
        if hasattr(condition, 'left') and hasattr(condition.left, 'name'):
            field_name = condition.left.name
            value = getattr(condition.right, 'value', condition.right)
            
            if field_name in ['job_id', 'status', 'name']:
                self._label_filters[f'jupyter-scheduler.io/{field_name.replace("_", "-")}'] = self._sanitize(str(value))
            else:
                # Complex fields stored in annotations, filtered post-query
                self._filters[field_name] = value
        elif hasattr(condition, 'type') and condition.type.name == 'in_':
            # IN clauses require annotation filtering since K8s labels don't support OR
            field_name = condition.left.name
            if field_name == 'status':
                # Multiple values require post-query filtering
                self._filters['status_in'] = [self._sanitize(str(v)) for v in condition.right.value]
        
        return self
    
    def _sanitize(self, value: str) -> str:
        """Sanitize value for K8s labels."""
        value = str(value).lower()
        value = ''.join(c if c.isalnum() or c in '-_.' else '-' for c in value)
        return value.strip('-_.')[:63] or "none"
        
    def update(self, values: Dict):
        """Update matching jobs."""
        # Use labels for efficient K8s filtering
        label_selector = ",".join([f"{k}={v}" for k, v in self._label_filters.items()])
        if not label_selector:
            raise ValueError("Update requires filterable conditions")
        
        # Query matching jobs using label selector
        jobs = self.session.k8s_batch.list_namespaced_job(
            namespace=self.session.namespace,
            label_selector=label_selector
        )
        
        for job in jobs.items:
            # Merge new values into existing job data
            if job.metadata.annotations and "jupyter-scheduler.io/job-data" in job.metadata.annotations:
                job_data = json.loads(job.metadata.annotations["jupyter-scheduler.io/job-data"])
                job_data.update(values)
                job_data["update_time"] = get_utc_timestamp()
                
                # Store updated data back to annotation
                job.metadata.annotations["jupyter-scheduler.io/job-data"] = json.dumps(job_data)
                
                # Sync searchable fields to labels for query performance
                for field, value in values.items():
                    if field in ['status', 'name']:
                        label_key = f"jupyter-scheduler.io/{field.replace('_', '-')}"
                        job.metadata.labels[label_key] = self.session._sanitize(str(value))
                
                # Apply changes to K8s resource
                self.session.k8s_batch.patch_namespaced_job(
                    name=job.metadata.name,
                    namespace=self.session.namespace, 
                    body=job
                )
    
    def one(self):
        """Get single job or raise."""
        result = self.first()
        if result is None:
            raise ValueError("Job not found")
        return result
        
    def first(self):
        """Get first matching job."""
        jobs = self._get_matching_jobs()
        return jobs[0] if jobs else None
        
    def all(self):
        """Get all matching jobs."""
        return self._get_matching_jobs()
    
    def delete(self):
        """Delete matching jobs."""
        label_selector = ",".join([f"{k}={v}" for k, v in self._label_filters.items()])
        if not label_selector:
            raise ValueError("Delete requires filterable conditions")
            
        jobs = self.session.k8s_batch.list_namespaced_job(
            namespace=self.session.namespace,
            label_selector=label_selector
        )
        
        for job in jobs.items:
            self.session.k8s_batch.delete_namespaced_job(
                name=job.metadata.name,
                namespace=self.session.namespace
            )
    
    def _get_matching_jobs(self):
        """Query jobs matching filters."""
        # Use labels for efficient server-side filtering
        label_selector = ",".join([f"{k}={v}" for k, v in self._label_filters.items()])
        
        jobs = self.session.k8s_batch.list_namespaced_job(
            namespace=self.session.namespace,
            label_selector=label_selector if label_selector else None
        )
        
        results = []
        for job in jobs.items:
            if job.metadata.annotations and "jupyter-scheduler.io/job-data" in job.metadata.annotations:
                job_data = json.loads(job.metadata.annotations["jupyter-scheduler.io/job-data"])
                
                # Post-filter using annotation data for complex conditions
                if self._matches_annotation_filters(job_data):
                    results.append(self._dict_to_job(job_data))
        
        return results
    
    def _matches_annotation_filters(self, job_data: Dict) -> bool:
        """Check annotation-based filter matches."""
        for field, value in self._filters.items():
            if field == 'status_in':
                if job_data.get('status') not in value:
                    return False
            elif field == 'job_definition_id':
                if job_data.get('job_definition_id') != value:
                    return False
            elif field == 'start_time':
                if not job_data.get('start_time') or job_data['start_time'] < value:
                    return False
            elif field.endswith('_like'):
                # SQL LIKE converted to string prefix matching
                actual_field = field[:-5]
                actual_value = job_data.get(actual_field, "")
                if not actual_value.startswith(str(value).rstrip('%')):
                    return False
            else:
                if job_data.get(field) != value:
                    return False
        return True
    
    def _dict_to_job(self, job_data: Dict):
        """Convert dict to Job-like object."""
        class JobRecord:
            def __init__(self, data):
                for k, v in data.items():
                    setattr(self, k, v)
        
        return JobRecord(job_data)