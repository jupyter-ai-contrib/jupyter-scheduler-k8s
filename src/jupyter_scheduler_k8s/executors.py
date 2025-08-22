"""Kubernetes execution manager for jupyter-scheduler."""

import os
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Dict

import nbformat
import nbconvert
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from jupyter_scheduler.executors import ExecutionManager
from jupyter_scheduler.models import JobFeature
from jupyter_scheduler.orm import Job

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# TODO: Use jupyter-scheduler's logging system once it's available
# Currently jupyter-scheduler doesn't expose its logger configuration,
# so we manually add a console handler to ensure our logs appear in the server output
import sys
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('[%(levelname)s %(asctime)s.%(msecs)03d K8sScheduler] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class K8sExecutionManager(ExecutionManager):
    """Execute notebooks in Kubernetes Jobs with PVC storage."""
    
    def __init__(self, job_id: str, root_dir: str, db_url: str, staging_paths: Dict[str, str]):
        super().__init__(job_id, root_dir, db_url, staging_paths)
        
        self.namespace = os.environ.get('K8S_NAMESPACE', 'default')
        self.image = os.environ.get('K8S_IMAGE', 'jupyter-scheduler-k8s:latest')
        self.storage_size = os.environ.get('K8S_STORAGE_SIZE', '100Mi')
        
        default_pull_policy = self._detect_image_pull_policy()
        self.image_pull_policy = os.environ.get('K8S_IMAGE_PULL_POLICY', default_pull_policy)
        
        self.executor_memory_request = os.environ.get('K8S_EXECUTOR_MEMORY_REQUEST', '512Mi')
        self.executor_memory_limit = os.environ.get('K8S_EXECUTOR_MEMORY_LIMIT', '2Gi')
        self.executor_cpu_request = os.environ.get('K8S_EXECUTOR_CPU_REQUEST', '500m')
        self.executor_cpu_limit = os.environ.get('K8S_EXECUTOR_CPU_LIMIT', '2000m')
        
        self.k8s_core = None
        self.k8s_batch = None
    
    def _detect_image_pull_policy(self) -> str:
        """Auto-detect appropriate image pull policy based on K8s context."""
        try:
            config.load_kube_config()
            contexts, active_context = config.list_kube_config_contexts()
            if active_context and active_context.get('name'):
                context_name = active_context['name']
                
                # If context suggests local development, use Never
                if any(local_indicator in context_name.lower() for local_indicator in ['kind', 'minikube', 'docker-desktop']):
                    logger.info(f"Detected local K8s context '{context_name}', using imagePullPolicy: Never")
                    return 'Never'
                else:
                    logger.info(f"Detected remote K8s context '{context_name}', using imagePullPolicy: Always")
                    return 'Always'
        except Exception as e:
            logger.warning(f"Could not detect K8s context: {e}, defaulting to imagePullPolicy: Always")
        
        return 'Always'
    
    @classmethod
    def supported_features(cls) -> Dict[JobFeature, bool]:
        return {
            JobFeature.parameters: True,
            JobFeature.timeout_seconds: False,
            JobFeature.output_formats: True,
            JobFeature.job_name: False,
            JobFeature.stop_job: False,
            JobFeature.delete_job: False,
        }
    
    @classmethod
    def validate(cls, input_path: str) -> bool:
        with open(input_path, encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)
            try:
                nb.metadata.kernelspec["name"]
                return True
            except:
                return False
    
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
                ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=self.namespace))
                self.k8s_core.create_namespace(body=ns)
            else:
                raise
    
    def execute(self):
        logger.info("=== K8s ExecutionManager starting ===")
        self._init_k8s_clients()
        
        job_name = f"nb-job-{self.job_id[:8]}"
        pvc_name = f"{job_name}-storage"
        
        logger.info(f"Starting K8s job {job_name} in namespace {self.namespace}")
        logger.info(f"Using image: {self.image}")
        logger.info(f"Job ID: {self.job_id}")
        logger.info(f"Staging paths: {self.staging_paths}")
        
        try:
            self._create_pvc(pvc_name)
            self._populate_pvc_with_files(pvc_name)
            
            job = self._create_execution_job(job_name, pvc_name)
            self.k8s_batch.create_namespaced_job(namespace=self.namespace, body=job)
            
            logger.info("Waiting for job completion...")
            self._wait_for_job_completion(job_name, timeout=600)
            logger.info("Job completed successfully")
            self._collect_output_files_from_pvc(pvc_name)
            
        except Exception as e:
            logger.error(f"Job execution failed: {e}")
            raise
        finally:
            logger.info("Cleaning up K8s resources")
            self._cleanup(job_name, pvc_name)
    
    
    def _create_pvc(self, pvc_name: str):
        """Create PersistentVolumeClaim for notebook storage."""
        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(name=pvc_name),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1ResourceRequirements(
                    requests={"storage": self.storage_size}
                )
            )
        )
        
        try:
            self.k8s_core.create_namespaced_persistent_volume_claim(
                namespace=self.namespace, body=pvc
            )
            logger.info(f"Created PVC {pvc_name}")
        except ApiException as e:
            # Ignore if already exists
            if e.status != 409:
                raise
    
    def _populate_pvc_with_files(self, pvc_name: str):
        """Pre-populate PVC with files using a helper pod."""
        from kubernetes.client import V1Pod, V1PodSpec, V1Container, V1VolumeMount, V1Volume, V1PersistentVolumeClaimVolumeSource
        import time
        
        helper_pod_name = f"file-helper-{self.job_id[:8]}"
        
        helper_pod = V1Pod(
            metadata={"name": helper_pod_name},
            spec=V1PodSpec(
                restart_policy="Never",
                containers=[V1Container(
                    name="file-copier",
                    image="busybox:latest",
                    command=["sleep", "300"],
                    volume_mounts=[V1VolumeMount(
                        name="workspace",
                        mount_path="/workspace"
                    )]
                )],
                volumes=[V1Volume(
                    name="workspace",
                    persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(
                        claim_name=pvc_name
                    )
                )]
            )
        )
        
        self.k8s_core.create_namespaced_pod(namespace=self.namespace, body=helper_pod)
        self._wait_for_pod_ready(helper_pod_name)
        
        try:
            staging_dir = Path(self.staging_paths['input']).parent
            for file_path in staging_dir.rglob('*'):
                if file_path.is_file():
                    rel_path = file_path.relative_to(staging_dir)
                    self._copy_file_to_helper_pod(helper_pod_name, file_path, f"/workspace/{rel_path}")
            
            logger.info("Files transferred to PVC")
        finally:
            self.k8s_core.delete_namespaced_pod(name=helper_pod_name, namespace=self.namespace)
    
    def _wait_for_pod_ready(self, pod_name: str, timeout: int = 60):
        """Wait for pod to be ready with immediate check."""
        start_time = time.time()
        
        while True:
            try:
                pod = self.k8s_core.read_namespaced_pod(name=pod_name, namespace=self.namespace)
                if pod.status.phase == "Running":
                    logger.info(f"Pod {pod_name} is ready")
                    return
            except Exception:
                # Pod might not exist yet
                pass
            
            if time.time() - start_time > timeout:
                raise RuntimeError(f"Pod {pod_name} not ready after {timeout} seconds")
            
            time.sleep(0.5)
    
    def _copy_file_to_helper_pod(self, pod_name: str, local_file_path: Path, pod_file_path: str):
        """Copy file to helper pod using kubectl cp (no exec needed)."""
        import subprocess
        dir_path = str(Path(pod_file_path).parent)
        if dir_path != "/workspace":
            subprocess.run([
                "kubectl", "exec", pod_name, "-n", self.namespace, "--",
                "mkdir", "-p", dir_path
            ], check=True)
        
        # Use kubectl cp - the standard K8s file transfer method
        subprocess.run([
            "kubectl", "cp", str(local_file_path), 
            f"{self.namespace}/{pod_name}:{pod_file_path}"
        ], check=True)
        
        logger.debug(f"Copied {local_file_path} to helper pod ({local_file_path.stat().st_size} bytes)")
    
    def _create_execution_job(self, job_name: str, pvc_name: str) -> client.V1Job:
        """Create simple K8s Job that executes notebook (PVC already pre-populated)."""
        
        main_container = client.V1Container(
            name="notebook-executor",
            image=self.image,
            image_pull_policy=self._detect_image_pull_policy(),
            env=[
                client.V1EnvVar(name="NOTEBOOK_PATH", value=f"/workspace/{Path(self.staging_paths['input']).name}"),
                client.V1EnvVar(name="OUTPUT_PATH", value=f"/workspace/{Path(self.staging_paths['ipynb']).name}"),
                client.V1EnvVar(name="PARAMETERS", value=json.dumps(self.model.parameters or {})),
                client.V1EnvVar(name="PACKAGE_INPUT_FOLDER", value="true"),
                client.V1EnvVar(name="OUTPUT_FORMATS", value=json.dumps(self.model.output_formats))
            ],
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace")
            ],
            resources=client.V1ResourceRequirements(
                requests={
                    "memory": os.environ.get("K8S_EXECUTOR_MEMORY_REQUEST", "512Mi"),
                    "cpu": os.environ.get("K8S_EXECUTOR_CPU_REQUEST", "500m")
                },
                limits={
                    "memory": os.environ.get("K8S_EXECUTOR_MEMORY_LIMIT", "2Gi"),
                    "cpu": os.environ.get("K8S_EXECUTOR_CPU_LIMIT", "2000m")
                }
            )
        )
        
        pod_spec = client.V1PodSpec(
            restart_policy="Never",
            containers=[main_container],
            volumes=[
                client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=pvc_name
                    )
                )
            ]
        )
        
        job_spec = client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(name=job_name),
                spec=pod_spec
            ),
            # Don't retry on failure
            backoff_limit=0
        )
        
        k8s_job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=job_spec
        )
        
        return k8s_job
    
    def _wait_for_job_completion(self, job_name: str, timeout: int = 600):
        """Wait for job to complete using Watch API."""
        w = watch.Watch()
        start_time = time.time()
        
        try:
            for event in w.stream(
                self.k8s_batch.list_namespaced_job, 
                namespace=self.namespace,
                field_selector=f"metadata.name={job_name}",
                timeout_seconds=timeout
            ):
                job = event['object']
                job_status = job.status
                
                if job_status.succeeded:
                    logger.info(f"Job {job_name} completed successfully")
                    w.stop()
                    return
                    
                if job_status.failed:
                    logger.error(f"Job {job_name} failed")
                    w.stop()
                    raise RuntimeError(f"Job {job_name} failed")
                
                if time.time() - start_time > timeout:
                    w.stop()
                    raise RuntimeError(f"Job {job_name} timed out after {timeout} seconds")
                    
        except Exception as e:
            w.stop()
            raise RuntimeError(f"Error waiting for job completion: {e}")
    
    def _collect_output_files_from_pvc(self, pvc_name: str):
        """Collect output files directly from PVC using helper pod."""
        helper_pod_name = f"output-helper-{self.job_id[:8]}"
        
        helper_pod = client.V1Pod(
            metadata={"name": helper_pod_name},
            spec=client.V1PodSpec(
                restart_policy="Never",
                containers=[client.V1Container(
                    name="file-reader",
                    image="busybox:latest",
                    # Keep alive for file transfer
                    command=["sleep", "300"],
                    volume_mounts=[client.V1VolumeMount(
                        name="workspace",
                        mount_path="/workspace"
                    )]
                )],
                volumes=[client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=pvc_name
                    )
                )]
            )
        )
        
        self.k8s_core.create_namespaced_pod(namespace=self.namespace, body=helper_pod)
        self._wait_for_pod_ready(helper_pod_name)
        
        try:
            job = self.model
            
            # Collect all output formats that jupyter-scheduler expects
            # This mirrors jupyter-scheduler's approach - iterate through job.output_formats
            for output_format in job.output_formats:
                if output_format not in self.staging_paths:
                    logger.warning(f"No staging path configured for format: {output_format}")
                    continue
                    
                container_filename = Path(self.staging_paths[output_format]).name
                
                src_path = f"{self.namespace}/{helper_pod_name}:/workspace/{container_filename}"
                dest_path = self.staging_paths[output_format]
                
                try:
                    subprocess.run([
                        "kubectl", "cp", src_path, dest_path
                    ], check=True)
                    logger.info(f"Collected {output_format} output: {container_filename}")
                except subprocess.CalledProcessError:
                    if output_format == 'ipynb':
                        logger.error(f"Failed to collect required {output_format} output")
                        raise
                    else:
                        logger.warning(f"Could not collect {output_format} output (file may not exist)")
            
        finally:
            self.k8s_core.delete_namespaced_pod(name=helper_pod_name, namespace=self.namespace)
    
    def _cleanup(self, job_name: str, pvc_name: str):
        """Clean up K8s resources."""
        try:
            self.k8s_batch.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                propagation_policy="Background"
            )
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to delete job {job_name}: {e}")
        
        try:
            self.k8s_core.delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=self.namespace
            )
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to delete PVC {pvc_name}: {e}")
    
