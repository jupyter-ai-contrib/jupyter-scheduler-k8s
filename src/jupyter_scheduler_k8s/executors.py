import os
import json
import time
import logging
import subprocess
import sys
import nbformat

from pathlib import Path
from typing import Dict
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from jupyter_scheduler.executors import ExecutionManager
from jupyter_scheduler.models import JobFeature

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# TODO: Use jupyter-scheduler's logging system once it's available
# Currently jupyter-scheduler doesn't expose its logger configuration,
# so we manually add a console handler to ensure our logs appear in the server output

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter(
    "[%(levelname)s %(asctime)s.%(msecs)03d K8sScheduler] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class K8sExecutionManager(ExecutionManager):
    """Executes Jupyter Scheduler jobs as Kubernetes Jobs."""

    def __init__(
        self, job_id: str, root_dir: str, db_url: str, staging_paths: Dict[str, str]
    ):
        super().__init__(job_id, root_dir, db_url, staging_paths)
        self.s3_bucket = os.environ.get("S3_BUCKET")
        if not self.s3_bucket:
            raise ValueError("S3_BUCKET environment variable is required")
        self.namespace = os.environ.get("K8S_NAMESPACE", "default")
        self.image = os.environ.get("K8S_IMAGE", "jupyter-scheduler-k8s:latest")
        self.s3_endpoint_url = os.environ.get("S3_ENDPOINT_URL")
        self.executor_memory_request = os.environ.get(
            "K8S_EXECUTOR_MEMORY_REQUEST", "512Mi"
        )
        self.executor_memory_limit = os.environ.get("K8S_EXECUTOR_MEMORY_LIMIT", "2Gi")
        self.executor_cpu_request = os.environ.get("K8S_EXECUTOR_CPU_REQUEST", "500m")
        self.executor_cpu_limit = os.environ.get("K8S_EXECUTOR_CPU_LIMIT", "2000m")

        default_pull_policy = self._detect_image_pull_policy()
        self.image_pull_policy = os.environ.get(
            "K8S_IMAGE_PULL_POLICY", default_pull_policy
        )

        self.k8s_core = None
        self.k8s_batch = None

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
            self.k8s_batch.create_namespaced_job(namespace=self.namespace, body=job)
            logger.info(f"✅ Kubernetes job '{job_name}' created successfully")

            logger.info("⏳ Waiting for job completion...")
            self._wait_for_job_completion(job_name, timeout=600)
            logger.info("🎉 Job completed successfully")

            # Download outputs from S3
            self._download_from_s3(s3_output_prefix)

        finally:
            logger.info("Cleaning up K8s job")
            self._cleanup_job(job_name)

    def _wait_for_job_completion(self, job_name: str, timeout: int = 600):
        """Wait for job to complete using Watch API."""
        w = watch.Watch()
        start_time = time.time()

        try:
            for event in w.stream(
                self.k8s_batch.list_namespaced_job,
                namespace=self.namespace,
                field_selector=f"metadata.name={job_name}",
                timeout_seconds=timeout,
            ):
                job = event["object"]
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
                    raise RuntimeError(
                        f"Job {job_name} timed out after {timeout} seconds"
                    )

        except Exception as e:
            w.stop()
            raise RuntimeError(f"Error waiting for job completion: {e}")

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

    def _download_from_s3(self, s3_output_prefix: str):
        """Download output files from S3 to staging directory using AWS CLI."""
        staging_dir = Path(self.staging_paths["ipynb"]).parent
        
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
                value=f"/tmp/outputs/{Path(self.staging_paths['ipynb']).name}",
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

        main_container = client.V1Container(
            name="notebook-executor",
            image=self.image,
            image_pull_policy=self.image_pull_policy,
            env=env_vars,
            resources=client.V1ResourceRequirements(
                requests={
                    "memory": self.executor_memory_request,
                    "cpu": self.executor_cpu_request,
                },
                limits={
                    "memory": self.executor_memory_limit,
                    "cpu": self.executor_cpu_limit,
                },
            ),
        )

        pod_spec = client.V1PodSpec(restart_policy="Never", containers=[main_container])

        job_spec = client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(name=job_name), spec=pod_spec
            ),
            # Don't retry on failure
            backoff_limit=0,
        )

        k8s_job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(name=job_name),
            spec=job_spec,
        )

        return k8s_job

    def _cleanup_job(self, job_name: str):
        """Clean up K8s job (S3 mode - no PVC to clean)."""
        try:
            self.k8s_batch.delete_namespaced_job(
                name=job_name, namespace=self.namespace, propagation_policy="Background"
            )
            logger.info(f"Cleaned up job {job_name}")
        except ApiException as e:
            if e.status != 404:
                logger.warning(f"Failed to delete job {job_name}: {e}")
