"""K8s Scheduler implementation for jupyter-scheduler."""

import json
import logging
import os
import subprocess

from jupyter_scheduler.scheduler import Scheduler
from jupyter_scheduler.models import Status
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)


class K8sScheduler(Scheduler):
    """Scheduler implementation for Kubernetes backend.
    
    Extends the base Scheduler to handle K8s Job lifecycle management.
    Maintains the Jobs-as-Records pattern where K8s Jobs serve as both
    execution workload and database records.
    """
    
    def __init__(self, **kwargs):
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
        
    def stop_job(self, job_id: str):
        """Stop a running job using K8s Job suspension.
        
        This matches jupyter-scheduler behavior: stops execution but preserves
        staging files (in our case, S3 files) for debugging/recovery.
        Uses K8s native job suspension for clean state management.
        """
        logger.info(f"Stopping job {job_id}")
        
        job = self.get_job(job_id)
        if job.status != Status.IN_PROGRESS:
            logger.info(f"Job {job_id} is not running (status: {job.status})")
            return
            
        self._init_k8s_clients()
        job_name = f"nb-job-{job_id[:8]}"
        
        try:
            k8s_job = self._k8s_batch.read_namespaced_job(job_name, self.namespace)
            if k8s_job.metadata.annotations and "jupyter-scheduler.io/job-data" in k8s_job.metadata.annotations:
                job_data = json.loads(k8s_job.metadata.annotations["jupyter-scheduler.io/job-data"])
                job_data["status"] = Status.STOPPING.value
                
                body = client.V1Job(
                    metadata=client.V1ObjectMeta(
                        annotations={"jupyter-scheduler.io/job-data": json.dumps(job_data)}
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
            logger.info(f"Suspended K8s Job {job_name}")
                        
            if k8s_job.metadata.annotations and "jupyter-scheduler.io/job-data" in k8s_job.metadata.annotations:
                job_data["status"] = Status.STOPPED.value
                body = client.V1Job(
                    metadata=client.V1ObjectMeta(
                        annotations={"jupyter-scheduler.io/job-data": json.dumps(job_data)}
                    )
                )
                self._k8s_batch.patch_namespaced_job(
                    name=job_name,
                    namespace=self.namespace,
                    body=body
                )
                
            logger.info(f"Successfully stopped job {job_id}")
            
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"K8s Job {job_name} not found")
            else:
                logger.error(f"Failed to stop job {job_id}: {e}")
                raise
                
    def delete_job(self, job_id: str):
        """Delete a job, its K8s Job record, and S3 files.
        
        This matches jupyter-scheduler behavior: stops the job if running,
        deletes the database record, and removes staging files (S3 in our case).
        """
        logger.info(f"Deleting job {job_id}")
        
        try:
            job = self.get_job(job_id)
            if job.status == Status.IN_PROGRESS:
                logger.info(f"Stopping running job {job_id} before deletion")
                self.stop_job(job_id)
        except Exception as e:
            logger.warning(f"Could not get job {job_id} status: {e}")
            pass
            
        self._init_k8s_clients()
        job_name = f"nb-job-{job_id[:8]}"
        
        try:
            self._k8s_batch.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                propagation_policy='Foreground'
            )
            logger.info(f"Deleted K8s Job {job_name}")
            
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"K8s Job {job_name} not found, may already be deleted")
            else:
                logger.error(f"Failed to delete K8s Job {job_name}: {e}")
                raise
                
        if self.s3_bucket:
            s3_prefix = f"s3://{self.s3_bucket}/job-{job_id}/"
            try:
                logger.info(f"Deleting S3 files at {s3_prefix}")
                result = subprocess.run(
                    ['aws', 's3', 'rm', s3_prefix, '--recursive'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0:
                    logger.info(f"Successfully deleted S3 files for job {job_id}")
                else:
                    logger.warning(f"S3 deletion returned non-zero: {result.stderr}")
                    
            except subprocess.TimeoutExpired:
                logger.error(f"S3 deletion timed out for job {job_id}")
            except Exception as e:
                logger.error(f"Failed to delete S3 files for job {job_id}: {e}")
                
        logger.info(f"Successfully deleted job {job_id}")