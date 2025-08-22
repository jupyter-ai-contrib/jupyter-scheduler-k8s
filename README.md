# jupyter-scheduler-k8s

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) - execute notebook jobs in containers instead of local processes.

## How It Works

1. Schedule notebook jobs through JupyterLab UI
2. Files uploaded to S3 bucket for storage
3. Kubernetes job downloads files, executes notebook in isolated pod
4. Results uploaded back to S3, then downloaded to JupyterLab and accessible through the UI

**Key features:**
- **S3 storage** - files survive Kubernetes cluster or Jupyter Server failures. Supports any S3-compatible storage like AWS S3, MinIO, GCS with S3 API, and so on
- Parameter injection for notebook customization
- Multiple output formats (HTML, PDF, etc.)
- Works with any Kubernetes cluster (Kind, minikube, EKS, GKE, AKS)
- Configurable resource limits (CPU/memory)

## Requirements

- Kubernetes cluster (Kind, minikube, or cloud provider)  
- S3-compatible storage (AWS S3, MinIO, GCS with S3 API, etc.)
- Python 3.9+
- jupyter-scheduler>=2.11.0

**For local development:**
- Finch and Kind (install guides: [Finch](https://github.com/runfinch/finch#installation), [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation))
- S3-compatible storage for testing (see S3 setup guides for local options)

**Connecting to your cluster:**
- Default: Reads cluster credentials from `~/.kube/config`
- Custom: Set `KUBECONFIG` environment variable to your kubeconfig path
- Cloud: Your provider's CLI sets this up (e.g., `aws eks update-kubeconfig`)

## Installation

### Local Deployment

```bash
# One-command setup: builds image, loads into Kind cluster (run from repo directory)
make dev-env

# (Optional) Verify Kind cluster and Finch image are ready
make status

# Install the package and all dependencies (including jupyterlab and jupyter-scheduler)
pip install -e .

# Configure S3 storage (required)
export S3_BUCKET="<your-bucket-name>"

# Configure AWS credentials (required)
export AWS_ACCESS_KEY_ID="<your-access-key>"
export AWS_SECRET_ACCESS_KEY="<your-secret-key>"

# Optional: For temporary credentials
# export AWS_SESSION_TOKEN="<your-session-token>"

# Launch Jupyter Lab with K8s backend (from same terminal with env vars)
jupyter lab --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
```

### Cloud Deployment

```bash
# Install the package and all dependencies (run from repo directory)
pip install -e .

# Build image using Makefile
make build-image

# Tag and push to your registry (manual steps - registry-specific)
finch tag jupyter-scheduler-k8s:latest your-registry/jupyter-scheduler-k8s:latest
finch push your-registry/jupyter-scheduler-k8s:latest

# Configure required environment
export S3_BUCKET="<your-company-notebooks>"
export AWS_ACCESS_KEY_ID="<your-access-key>"
export AWS_SECRET_ACCESS_KEY="<your-secret-key>"

# Configure for cloud deployment
export K8S_IMAGE="your-registry/jupyter-scheduler-k8s:latest"
export K8S_NAMESPACE="<your-namespace>"

# Launch Jupyter Lab with K8s backend
jupyter lab --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
```

## Configuration

### Environment Variables

**K8s Backend Configuration** (set by user):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `K8S_NAMESPACE` | No | `default` | Kubernetes namespace |
| `K8S_IMAGE` | No | `jupyter-scheduler-k8s:latest` | Container image to use |
| `K8S_IMAGE_PULL_POLICY` | No | Auto-detected | `Never` for local clusters, `Always` for cloud |
| `K8S_EXECUTOR_MEMORY_REQUEST` | No | `512Mi` | Container memory request |
| `K8S_EXECUTOR_MEMORY_LIMIT` | No | `2Gi` | Container memory limit |
| `K8S_EXECUTOR_CPU_REQUEST` | No | `500m` | Container CPU request |
| `K8S_EXECUTOR_CPU_LIMIT` | No | `2000m` | Container CPU limit |

**S3 Storage Configuration** (required):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `S3_BUCKET` | Yes | - | S3 bucket name for file storage |
| `S3_ENDPOINT_URL` | No | - | Custom S3 endpoint (for MinIO, GCS S3 API, etc.) |

**AWS Credentials** (when using S3):
- **IAM roles** (recommended for EC2/EKS): Automatic
- **Credentials file**: `~/.aws/credentials` 
- **Environment**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`

**Container Execution Variables** (set automatically by K8sExecutionManager, or manually for testing):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NOTEBOOK_PATH` | Yes | - | Path to notebook file to execute |
| `OUTPUT_PATH` | Yes | - | Path where executed notebook will be saved |
| `PARAMETERS` | No | `{}` | JSON string of parameters to inject into notebook |
| `OUTPUT_FORMATS` | No | `[]` | JSON array of output formats (e.g., `["html", "pdf"]`) |
| `PACKAGE_INPUT_FOLDER` | No | `false` | Copy entire notebook directory to working directory |
| `KERNEL_NAME` | No | `python3` | Jupyter kernel to use for execution |
| `TIMEOUT` | No | `600` | Execution timeout in seconds |

## Testing

**Prerequisites:**
```bash
# macOS
brew install finch kind
```

**Linux/Windows:** See install guides for [Finch](https://github.com/runfinch/finch#installation) and [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)

**Quick test:**
```bash
# Setup
make dev-env && pip install -e .

# Configure required environment
export S3_BUCKET="<your-test-bucket>"
export AWS_ACCESS_KEY_ID="<your-access-key>"
export AWS_SECRET_ACCESS_KEY="<your-secret-key>"

# Launch and test through JupyterLab UI
jupyter lab --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"

# Cleanup
make clean
```

**Test container directly:**
```bash
# Basic test with provided notebook
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_notebook.ipynb" \
  -e OUTPUT_PATH="/workspace/output.ipynb" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest

# Test with data files - copies entire notebook directory
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_with_data.ipynb" \
  -e OUTPUT_PATH="/workspace/output_with_data.ipynb" \
  -e PACKAGE_INPUT_FOLDER="true" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest
```

## Development

**Initial setup:**
1. `make dev-env` - Create Kind cluster and load container image
2. `pip install -e .` - Install package in editable mode

**Python code changes** (K8sExecutionManager):
- Changes are picked up automatically (editable install)
- Just restart JupyterLab

**Container changes** (notebook executor):
```bash
make build-image
make load-image
```

**Useful commands:**
```bash
make status         # Check environment status
make clean          # Remove cluster and cleanup
```


## Implementation Status

### Working Features ✅
- Custom `K8sExecutionManager` that extends `jupyter-scheduler.ExecutionManager` and runs notebook jobs in Kubernetes pods
- Parameter injection and multiple output formats
- File handling for any notebook size with proven S3 operations
- Configurable CPU/memory limits
- Event-driven job monitoring with Watch API
- S3 storage: Files persist beyond kubernetes cluster or jupyter server failures using AWS CLI for reliable transfers

### Planned 🚧
- GPU resource configuration for k8s jobs from UI
- Kubernetes job stop/deletion from UI
- Kubernetes-native scheduling from UI
- PyPI package publishing
