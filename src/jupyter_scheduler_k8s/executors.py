"""Kubernetes execution manager for jupyter-scheduler."""

import os
import json
import time
import logging
from pathlib import Path
from typing import Dict

import nbformat
import nbconvert
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream
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
        
        # Auto-detect image pull policy based on context
        default_pull_policy = self._detect_image_pull_policy()
        self.image_pull_policy = os.environ.get('K8S_IMAGE_PULL_POLICY', default_pull_policy)
        
        # Resource limits for file receiver pod
        self.receiver_memory_request = os.environ.get('K8S_RECEIVER_MEMORY_REQUEST', '64Mi')
        self.receiver_memory_limit = os.environ.get('K8S_RECEIVER_MEMORY_LIMIT', '256Mi')
        self.receiver_cpu_request = os.environ.get('K8S_RECEIVER_CPU_REQUEST', '100m')
        self.receiver_cpu_limit = os.environ.get('K8S_RECEIVER_CPU_LIMIT', '500m')
        
        # Resource limits for notebook execution pod
        self.executor_memory_request = os.environ.get('K8S_EXECUTOR_MEMORY_REQUEST', '512Mi')
        self.executor_memory_limit = os.environ.get('K8S_EXECUTOR_MEMORY_LIMIT', '2Gi')
        self.executor_cpu_request = os.environ.get('K8S_EXECUTOR_CPU_REQUEST', '500m')
        self.executor_cpu_limit = os.environ.get('K8S_EXECUTOR_CPU_LIMIT', '2000m')
        
        self.k8s_core = None
        self.k8s_batch = None
    
    def _detect_image_pull_policy(self) -> str:
        """Auto-detect appropriate image pull policy based on K8s context."""
        try:
            # Try to load kubeconfig to get current context
            config.load_kube_config()
            
            # Get current context name
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
            # Create PVC for notebook storage
            self._create_pvc(pvc_name)
            
            # Create job with init container for file transfer
            job = self._create_job_with_init_container(job_name, pvc_name)
            self.k8s_batch.create_namespaced_job(namespace=self.namespace, body=job)
            
            # Wait for init container readiness and batch transfer files
            self._transfer_files_to_job(job_name)
            
            # Wait for main container completion (not entire job, since sidecar is still running)
            logger.info("Waiting for main container completion...")
            self._wait_for_main_container_completion(job_name, timeout=600)
            logger.info("Main container completed successfully")
            
            # Collect output files (using exec, not logs)
            self._collect_output_files(job_name, pvc_name)
            
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
            if e.status != 409:  # Ignore if already exists
                raise
    
    def _transfer_files_to_job(self, job_name: str):
        """Transfer files to job's init container and signal completion."""
        # Wait for pod to be created by the job
        pod_name = self._wait_for_pod_creation(job_name)
        
        # Wait for init container to be ready
        self._wait_for_init_container_ready(pod_name)
        
        # Batch transfer all files
        staging_dir = Path(self.staging_paths['input']).parent
        for file_path in staging_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(staging_dir)
                self._copy_file_to_init_container(pod_name, file_path, f"/workspace/{rel_path}")
        
        # Signal completion - init container will exit
        self._exec_command_in_init_container(pod_name, ["touch", "/workspace/.transfer_complete"])
        logger.info("Files transferred to job")
    
    def _wait_for_pod_creation(self, job_name: str, timeout: int = 60) -> str:
        """Wait for job to create its pod using Kubernetes Watch API (event-driven)."""
        w = watch.Watch()
        
        try:
            # Use Watch API for event-driven notification
            for event in w.stream(
                self.k8s_core.list_namespaced_pod,
                namespace=self.namespace,
                label_selector=f"job-name={job_name}",
                timeout_seconds=timeout
            ):
                event_type = event['type']
                pod = event['object']
                
                if event_type in ['ADDED', 'MODIFIED'] and pod.metadata.name:
                    logger.info(f"Pod {pod.metadata.name} created for job {job_name}")
                    return pod.metadata.name
                    
        except Exception as e:
            logger.warning(f"Watch failed, falling back to polling: {e}")
            # Fallback to simple check if watch fails
            pods = self.k8s_core.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"job-name={job_name}"
            )
            if pods.items:
                return pods.items[0].metadata.name
        finally:
            w.stop()
        
        raise RuntimeError(f"Pod not created for job {job_name} after {timeout} seconds")
    
    def _wait_for_init_container_ready(self, pod_name: str, timeout: int = 60):
        """Wait for init container to be running using Watch API (event-driven)."""
        w = watch.Watch()
        start_time = time.time()
        
        try:
            # Watch pod status changes
            for event in w.stream(
                self.k8s_core.list_namespaced_pod,
                namespace=self.namespace,
                field_selector=f"metadata.name={pod_name}",
                timeout_seconds=timeout
            ):
                # Check timeout manually since we're watching list instead of read
                if time.time() - start_time > timeout:
                    break
                    
                pod = event['object']
                
                # Check init container status
                if pod.status.init_container_statuses:
                    init_status = pod.status.init_container_statuses[0]
                    if init_status.state.running:
                        logger.info(f"Init container ready in pod {pod_name}")
                        return
                    elif init_status.state.terminated:
                        raise RuntimeError(f"Init container terminated unexpectedly: {init_status.state.terminated.reason}")
                        
        except Exception as e:
            logger.warning(f"Watch failed for init container, falling back to polling: {e}")
            # Fallback to simple check with debugging
            try:
                pod = self.k8s_core.read_namespaced_pod(name=pod_name, namespace=self.namespace)
                logger.info(f"Pod phase: {pod.status.phase}")
                
                if pod.status.init_container_statuses:
                    init_status = pod.status.init_container_statuses[0]
                    logger.info(f"Init container state: {init_status.state}")
                    if init_status.state.running:
                        return
                    elif init_status.state.waiting:
                        logger.warning(f"Init container waiting: {init_status.state.waiting.reason}")
                    elif init_status.state.terminated:
                        logger.error(f"Init container terminated: {init_status.state.terminated.reason}")
                else:
                    logger.warning("No init container statuses found")
            except ApiException as e:
                logger.error(f"Failed to read pod status: {e}")
        finally:
            w.stop()
        
        raise RuntimeError(f"Init container not ready after {timeout} seconds")
    
    def _copy_file_to_init_container(self, pod_name: str, local_file_path: Path, pod_file_path: str):
        """Copy file to init container using exec."""
        import base64
        
        # Read file as binary
        with open(local_file_path, 'rb') as f:
            content = f.read()
        
        # Create directory if needed
        dir_path = str(Path(pod_file_path).parent)
        if dir_path != "/workspace":
            self._exec_command_in_init_container(pod_name, ["mkdir", "-p", dir_path])
        
        # Transfer via base64 to handle binary files
        content_b64 = base64.b64encode(content).decode('ascii')
        command = ["sh", "-c", f"echo '{content_b64}' | base64 -d > {pod_file_path}"]
        self._exec_command_in_init_container(pod_name, command)
        
        logger.debug(f"Copied {local_file_path} to init container")
    
    def _exec_command_in_init_container(self, pod_name: str, command: list) -> str:
        """Execute command in init container."""
        resp = stream(
            self.k8s_core.connect_get_namespaced_pod_exec,
            pod_name,
            self.namespace,
            container="file-receiver",  # Init container name
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        return resp
    
    def _create_job_with_init_container(self, job_name: str, pvc_name: str) -> client.V1Job:
        """Create K8s Job with init container, main container, and sidecar for output collection."""
        job = self.model
        
        # Init container that waits for file transfer
        init_container = client.V1Container(
            name="file-receiver",
            image="busybox:latest",
            command=["sh", "-c", """
                echo 'Ready for file transfer'
                while [ ! -f /workspace/.transfer_complete ]; do
                    sleep 1
                done
                echo 'Transfer complete, starting notebook execution'
            """],
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace")
            ],
            resources=client.V1ResourceRequirements(
                requests={"memory": self.receiver_memory_request, "cpu": self.receiver_cpu_request},
                limits={"memory": self.receiver_memory_limit, "cpu": self.receiver_cpu_limit}
            )
        )
        
        # Environment variables for notebook execution
        env_vars = [
            client.V1EnvVar(name="NOTEBOOK_PATH", value=f"/workspace/{Path(self.staging_paths['input']).name}"),
            client.V1EnvVar(name="OUTPUT_PATH", value=f"/workspace/output.ipynb"),
        ]
        
        # Add optional parameters
        if job.parameters:
            env_vars.append(client.V1EnvVar(name="PARAMETERS", value=json.dumps(job.parameters)))
        
        # Get kernel from notebook metadata 
        with open(self.staging_paths['input'], 'r') as f:
            nb_data = json.load(f)
            if 'metadata' in nb_data and 'kernelspec' in nb_data['metadata']:
                kernel_name = nb_data['metadata']['kernelspec'].get('name', 'python3')
                env_vars.append(client.V1EnvVar(name="KERNEL_NAME", value=kernel_name))
            
        if job.package_input_folder:
            env_vars.append(client.V1EnvVar(name="PACKAGE_INPUT_FOLDER", value="true"))
        
        # Main container for notebook execution  
        main_container = client.V1Container(
            name="notebook-executor",
            image=self.image,
            image_pull_policy=self.image_pull_policy,
            env=env_vars,
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace")
            ],
            working_dir="/app",
            resources=client.V1ResourceRequirements(
                requests={"memory": self.executor_memory_request, "cpu": self.executor_cpu_request},
                limits={"memory": self.executor_memory_limit, "cpu": self.executor_cpu_limit}
            )
        )
        
        # Sidecar container for output collection - stays alive after main exits
        sidecar_container = client.V1Container(
            name="output-collector",
            image="busybox:latest",
            command=["sh", "-c", """
                echo 'Output collector ready'
                # Wait for main container to finish (creates .execution_complete)
                while [ ! -f /workspace/.execution_complete ]; do
                    sleep 2
                done
                echo 'Main execution complete, ready for output collection'
                # Stay alive until signaled to exit
                while [ ! -f /workspace/.collection_complete ]; do
                    sleep 1
                done
                echo 'Output collection complete, exiting'
            """],
            volume_mounts=[
                client.V1VolumeMount(name="workspace", mount_path="/workspace")
            ],
            resources=client.V1ResourceRequirements(
                requests={"memory": "32Mi", "cpu": "50m"},
                limits={"memory": "128Mi", "cpu": "200m"}
            )
        )
        
        # Pod spec with init container, main container, and sidecar
        pod_spec = client.V1PodSpec(
            init_containers=[init_container],
            containers=[main_container, sidecar_container],
            volumes=[
                client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=pvc_name
                    )
                )
            ],
            restart_policy="Never"
        )
        
        # Job spec
        job_spec = client.V1JobSpec(
            template=client.V1PodTemplateSpec(spec=pod_spec),
            backoff_limit=0
        )
        
        return client.V1Job(
            metadata=client.V1ObjectMeta(name=job_name),
            spec=job_spec
        )
    
    def _collect_output_files(self, job_name: str, pvc_name: str):
        """Collect output files using pod exec (not logs)."""
        # Get the pod for the completed job
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=f"job-name={job_name}"
        )
        
        if not pods.items:
            raise RuntimeError(f"No pod found for job {job_name}")
        
        pod_name = pods.items[0].metadata.name
        
        # Get executed notebook via pod exec
        notebook_content = self._exec_command_in_pod(pod_name, ["cat", "/workspace/output.ipynb"])
        
        # Save to staging directory 
        staging_dir = Path(self.staging_paths['input']).parent
        output_path = staging_dir / "executed_output.ipynb"
        with open(output_path, 'w') as f:
            f.write(notebook_content)
        
        # Read and process executed notebook
        with open(output_path, 'r') as f:
            nb = nbformat.read(f, as_version=4)
        
        # Create output files and handle side effects
        self._create_output_files(nb)
        self._handle_side_effects(pod_name, staging_dir)
    
    def _exec_command_in_pod(self, pod_name: str, command: list) -> str:
        """Execute command in pod and return output."""
        resp = stream(
            self.k8s_core.connect_get_namespaced_pod_exec,
            pod_name,
            self.namespace,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        return resp
    
    def _create_output_files(self, nb):
        """Create output files in different formats."""
        job = self.model
        for output_format in job.output_formats:
            cls = nbconvert.get_exporter(output_format)
            output, _ = cls().from_notebook_node(nb)
            with open(self.staging_paths[output_format], 'w', encoding='utf-8') as f:
                f.write(output)
    
    def _handle_side_effects(self, pod_name: str, staging_dir: Path):
        """Handle side effect files by copying them from pod."""
        # Get list of all files (excluding notebooks)
        files_output = self._exec_command_in_pod(pod_name, 
            ["find", "/workspace", "-type", "f", "!", "-name", "*.ipynb"])
        
        side_effect_files = []
        for file_path in files_output.strip().split('\n'):
            if file_path.strip():
                rel_path = file_path.replace('/workspace/', '')
                if rel_path:
                    # Copy file content
                    content = self._exec_command_in_pod(pod_name, ["cat", file_path])
                    local_path = staging_dir / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_path, 'w') as f:
                        f.write(content)
                    side_effect_files.append(rel_path)
        
        # Update database with side effect files
        if side_effect_files:
            with self.db_session() as session:
                current_packaged_files = set(
                    session.query(Job.packaged_files).filter(Job.job_id == self.job_id).scalar() or []
                )
                updated_packaged_files = list(current_packaged_files.union(side_effect_files))
                session.query(Job).filter(Job.job_id == self.job_id).update(
                    {"packaged_files": updated_packaged_files}
                )
                session.commit()
    
    def _cleanup(self, job_name: str, pvc_name: str):
        """Clean up K8s resources."""
        # Delete job
        try:
            self.k8s_batch.delete_namespaced_job(
                name=job_name,
                namespace=self.namespace,
                propagation_policy="Background"
            )
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to delete job {job_name}: {e}")
        
        # Delete PVC
        try:
            self.k8s_core.delete_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=self.namespace
            )
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to delete PVC {pvc_name}: {e}")
    
    def _wait_for_completion(self, job_name: str, timeout: int = 600):
        """Wait for job to complete."""
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                job = self.k8s_batch.read_namespaced_job(
                    name=job_name,
                    namespace=self.namespace
                )
                
                if job.status.succeeded:
                    return
                elif job.status.failed:
                    # Try to get pod logs for debugging
                    pods = self.k8s_core.list_namespaced_pod(
                        namespace=self.namespace,
                        label_selector=f"job-name={job_name}"
                    )
                    if pods.items:
                        logs = self.k8s_core.read_namespaced_pod_log(
                            name=pods.items[0].metadata.name,
                            namespace=self.namespace,
                            tail_lines=50
                        )
                        logger.error(f"Job failed. Last 50 lines of logs:\n{logs}")
                    raise RuntimeError(f"Job {job_name} failed")
                
                time.sleep(5)
                
            except ApiException as e:
                if e.status == 404:
                    time.sleep(5)  # Job might not be created yet
                else:
                    raise
        
        raise RuntimeError(f"Job {job_name} timed out after {timeout} seconds")
    
    def _wait_for_main_container_completion(self, job_name: str, timeout: int = 600):
        """Wait for main container (notebook-executor) to complete."""
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                # Get the pod for the job
                pods = self.k8s_core.list_namespaced_pod(
                    namespace=self.namespace,
                    label_selector=f"job-name={job_name}"
                )
                
                if not pods.items:
                    time.sleep(5)
                    continue
                    
                pod = pods.items[0]
                
                # Check main container status
                if pod.status.container_statuses:
                    for container_status in pod.status.container_statuses:
                        if container_status.name == "notebook-executor":
                            if container_status.state.terminated:
                                if container_status.state.terminated.exit_code == 0:
                                    return  # Success
                                else:
                                    # Get logs for debugging
                                    logs = self.k8s_core.read_namespaced_pod_log(
                                        name=pod.metadata.name,
                                        namespace=self.namespace,
                                        container="notebook-executor",
                                        tail_lines=50
                                    )
                                    logger.error(f"Main container failed. Last 50 lines of logs:\n{logs}")
                                    raise RuntimeError(f"Main container failed with exit code {container_status.state.terminated.exit_code}")
                            # Container still running, continue waiting
                            break
                
                time.sleep(5)
                
            except ApiException as e:
                if e.status == 404:
                    time.sleep(5)
                else:
                    raise
        
        raise RuntimeError(f"Main container did not complete after {timeout} seconds")
    
    def _collect_output_files(self, job_name: str, pvc_name: str):
        """Collect output files using sidecar container."""
        # Get the pod for the job (contains the sidecar)
        pods = self.k8s_core.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=f"job-name={job_name}"
        )
        
        if not pods.items:
            raise RuntimeError(f"No pod found for job {job_name}")
        
        pod_name = pods.items[0].metadata.name
        
        # Main container should already be complete when we get here
        # Just verify sidecar detected the completion
        logger.info("Verifying sidecar detected execution completion...")
        
        try:
            # Collect files via sidecar container
            exec_command = ["find", "/workspace", "-type", "f"]
            files_output = self._exec_in_sidecar(pod_name, exec_command)
            logger.info(f"Files in workspace: {files_output}")
            
            # Get the executed notebook (container saves to output.ipynb)
            executed_notebook_path = "/workspace/output.ipynb"
            notebook_content = self._get_file_from_sidecar(pod_name, executed_notebook_path)
            
            # Save to staging directory
            staging_dir = Path(self.staging_paths['input']).parent
            local_executed_path = staging_dir / f"{Path(self.staging_paths['input']).stem}_executed.ipynb"
            with open(local_executed_path, 'w') as f:
                f.write(notebook_content)
            
            # Read and process the executed notebook
            with open(local_executed_path, 'r') as f:
                nb = nbformat.read(f, as_version=4)
            
            # Handle side effects and create output files
            self._handle_side_effects_via_sidecar(job_name, pod_name, staging_dir)
            self._create_output_files(nb)
            
        finally:
            # Signal sidecar to exit
            self._signal_collection_complete(pod_name)
    
    def _wait_for_execution_complete(self, pod_name: str, timeout: int = 60):
        """Wait for main container to signal execution completion."""
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                # Check if execution complete signal exists
                result = self._exec_in_sidecar(pod_name, ["test", "-f", "/workspace/.execution_complete"])
                return  # File exists, execution complete
            except:
                time.sleep(2)
        
        raise RuntimeError(f"Execution completion not signaled after {timeout} seconds")
    
    def _exec_in_sidecar(self, pod_name: str, command: list) -> str:
        """Execute command in sidecar container."""
        resp = stream(
            self.k8s_core.connect_get_namespaced_pod_exec,
            pod_name,
            self.namespace,
            container="output-collector",  # Sidecar container name
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        return resp
    
    def _get_file_from_sidecar(self, pod_name: str, file_path: str) -> str:
        """Get file content from sidecar container using base64 to avoid corruption."""
        # Use base64 encoding to prevent JSON corruption issues
        command = ["sh", "-c", f"base64 {file_path}"]
        base64_content = self._exec_in_sidecar(pod_name, command)
        
        import base64
        try:
            # Decode base64 content
            decoded_bytes = base64.b64decode(base64_content.strip())
            return decoded_bytes.decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to decode base64 content: {e}")
            # Fallback to direct cat if base64 fails
            command = ["cat", file_path]
            return self._exec_in_sidecar(pod_name, command)
    
    def _signal_collection_complete(self, pod_name: str):
        """Signal sidecar that output collection is complete."""
        try:
            self._exec_in_sidecar(pod_name, ["touch", "/workspace/.collection_complete"])
            logger.info("Signaled sidecar to exit")
        except Exception as e:
            logger.warning(f"Failed to signal sidecar completion: {e}")
    
    def _handle_side_effects_via_sidecar(self, job_name: str, pod_name: str, staging_dir: Path):
        """Handle side effect files by copying them via sidecar."""
        # Get list of all files created (excluding input and executed notebook)
        command = ["find", "/workspace", "-type", "f", "-not", "-name", "*.ipynb", "-not", "-name", ".*"]
        side_effect_files = self._exec_in_sidecar(pod_name, command)
        
        new_files = []
        for file_path in side_effect_files.strip().split('\n'):
            if file_path.strip():
                # Copy file from sidecar to staging directory  
                rel_path = file_path.replace('/workspace/', '')
                if rel_path:
                    content = self._get_file_from_sidecar(pod_name, file_path)
                    local_path = staging_dir / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Handle binary vs text files
                    try:
                        with open(local_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                    except UnicodeDecodeError:
                        # Handle binary files
                        with open(local_path, 'wb') as f:
                            f.write(content.encode('utf-8'))
                    
                    new_files.append(rel_path)
        
        # Update database with side effect files
        if new_files:
            with self.db_session() as session:
                current_packaged_files_set = set(
                    session.query(Job.packaged_files).filter(Job.job_id == self.job_id).scalar()
                    or []
                )
                updated_packaged_files = list(current_packaged_files_set.union(new_files))
                session.query(Job).filter(Job.job_id == self.job_id).update(
                    {"packaged_files": updated_packaged_files}
                )
                session.commit()
    
    
    def _exec_in_pod(self, pod_name: str, command: list) -> str:
        """Execute a command in a pod and return output."""
        from kubernetes.stream import stream
        
        resp = stream(
            self.k8s_core.connect_get_namespaced_pod_exec,
            pod_name,
            self.namespace,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
        return resp
    
    def _get_file_from_pod(self, pod_name: str, file_path: str) -> str:
        """Get file content from pod."""
        command = ["cat", file_path]
        return self._exec_in_pod(pod_name, command)
    
    def _handle_side_effects(self, job_name: str, pod_name: str, staging_dir: Path):
        """Handle side effect files by copying them from pod."""
        # Get list of all files created (excluding input and executed notebook)
        command = ["find", "/workspace", "-type", "f", "-not", "-name", "*.ipynb"]
        side_effect_files = self._exec_in_pod(pod_name, command)
        
        new_files = []
        for file_path in side_effect_files.strip().split('\n'):
            if file_path.strip():
                # Copy file from pod to staging directory
                rel_path = file_path.replace('/workspace/', '')
                if rel_path:
                    content = self._get_file_from_pod(pod_name, file_path)
                    local_path = staging_dir / rel_path
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_path, 'w') as f:
                        f.write(content)
                    new_files.append(rel_path)
        
        # Update database with side effect files
        if new_files:
            with self.db_session() as session:
                current_packaged_files_set = set(
                    session.query(Job.packaged_files).filter(Job.job_id == self.job_id).scalar()
                    or []
                )
                updated_packaged_files = list(current_packaged_files_set.union(new_files))
                session.query(Job).filter(Job.job_id == self.job_id).update(
                    {"packaged_files": updated_packaged_files}
                )
                session.commit()
    
    def _handle_output_files(self):
        """Handle executed notebook and side effect files."""
        staging_dir = Path(self.staging_paths['input']).parent
        executed_nb_path = staging_dir / f"{Path(self.staging_paths['input']).stem}_executed.ipynb"
        
        if not executed_nb_path.exists():
            raise RuntimeError(f"Executed notebook not found: {executed_nb_path}")
        
        # Read executed notebook
        with open(executed_nb_path, 'r') as f:
            nb = nbformat.read(f, as_version=4)
        
        # Handle side effects files (like DefaultExecutionManager)
        self._add_side_effects_files(staging_dir)
        
        # Create output files in different formats
        self._create_output_files(nb)
    
    def _add_side_effects_files(self, staging_dir: Path):
        """Scan for side effect files and update job's packaged_files."""
        input_notebook = Path(self.staging_paths['input']).name
        executed_notebook = f"{Path(self.staging_paths['input']).stem}_executed.ipynb"
        
        new_files_set = set()
        for file_path in staging_dir.rglob('*'):
            if file_path.is_file():
                rel_path = file_path.relative_to(staging_dir)
                if str(rel_path) not in [input_notebook, executed_notebook]:
                    new_files_set.add(str(rel_path))
        
        if new_files_set:
            with self.db_session() as session:
                current_packaged_files_set = set(
                    session.query(Job.packaged_files).filter(Job.job_id == self.job_id).scalar()
                    or []
                )
                updated_packaged_files = list(current_packaged_files_set.union(new_files_set))
                session.query(Job).filter(Job.job_id == self.job_id).update(
                    {"packaged_files": updated_packaged_files}
                )
                session.commit()
    
    def _create_output_files(self, nb):
        """Create output files in different formats."""
        job = self.model
        for output_format in job.output_formats:
            cls = nbconvert.get_exporter(output_format)
            output, _ = cls().from_notebook_node(nb)
            with open(self.staging_paths[output_format], 'w', encoding='utf-8') as f:
                f.write(output)