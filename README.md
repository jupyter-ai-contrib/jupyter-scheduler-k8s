# jupyter-scheduler-k8s

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) - execute notebook jobs in containers instead of local processes.

## How It Works

1. Schedule notebook jobs through JupyterLab UI (runs locally)
2. jupyter-scheduler-k8s sends job to your Kubernetes cluster (local or cloud)
3. Notebook executes in isolated container with dependencies
4. Results return to JupyterLab for download

**Key features:**
- Parameter injection for notebook customization
- Multiple output formats (HTML, PDF, etc.)
- Works with any Kubernetes cluster (Kind, minikube, EKS, GKE, AKS)
- Configurable resource limits (CPU/memory)

## Requirements

- Kubernetes cluster (Kind, minikube, or cloud provider)
- Python 3.9+
- jupyter-scheduler>=2.11.0

**For local development:**
- Finch and Kind (install guides: [Finch](https://github.com/runfinch/finch#installation), [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation))

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

# Configure environment (as shown by make dev-env output)
export K8S_IMAGE="jupyter-scheduler-k8s:latest"
export K8S_IMAGE_PULL_POLICY="Never"

# Launch Jupyter Lab with K8s backend
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
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

# Configure environment
export K8S_IMAGE="your-registry/jupyter-scheduler-k8s:latest"
export K8S_NAMESPACE="your-namespace"

# Launch Jupyter Lab with K8s backend
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
```

## Configuration

### Environment Variables

**K8s Backend Configuration** (set by user):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `K8S_NAMESPACE` | No | `default` | Kubernetes namespace |
| `K8S_IMAGE` | No | `jupyter-scheduler-k8s:latest` | Container image to use |
| `K8S_IMAGE_PULL_POLICY` | No | Auto-detected | `Never` for local clusters, `Always` for cloud |
| `K8S_STORAGE_SIZE` | No | `100Mi` | PVC size per job |
| `K8S_EXECUTOR_MEMORY_REQUEST` | No | `512Mi` | Container memory request |
| `K8S_EXECUTOR_MEMORY_LIMIT` | No | `2Gi` | Container memory limit |
| `K8S_EXECUTOR_CPU_REQUEST` | No | `500m` | Container CPU request |
| `K8S_EXECUTOR_CPU_LIMIT` | No | `2000m` | Container CPU limit |

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
- **macOS**: `brew install finch kind`
- **Linux/Windows**: [Finch install guide](https://github.com/runfinch/finch#installation), [Kind install guide](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)

**End-to-end testing:**

1. Setup local K8s environment:
```bash
make dev-env  # Creates Kind cluster, builds and loads image

# Configure environment
export K8S_IMAGE="jupyter-scheduler-k8s:latest"
export K8S_IMAGE_PULL_POLICY="Never"
```

2. Install the package and dependencies:
```bash
pip install -e .
```

3. Launch with K8s backend:
```bash
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
```

4. Create and run notebook jobs through the jupyter-scheduler UI (supports parameters and multiple output formats)

5. Cleanup:
```bash
make clean  # Remove Kind cluster
```

**Test container directly:**

```bash
# Basic test with provided notebook (run from repo directory)
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_notebook.ipynb" \
  -e OUTPUT_PATH="/workspace/output.ipynb" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest

# Test with parameters - note the single quotes to avoid shell escaping issues
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_notebook.ipynb" \
  -e OUTPUT_PATH="/workspace/output_with_params.ipynb" \
  -e 'PARAMETERS={"test_param":"Hello from K8s"}' \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest

# Test with copying input folder (includes all files from notebook directory)
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_with_data.ipynb" \
  -e OUTPUT_PATH="/workspace/output_with_data.ipynb" \
  -e PACKAGE_INPUT_FOLDER="true" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest
```

**Note on parameters**: When passing JSON via PARAMETERS environment variable, use single quotes around the entire `KEY=VALUE` pair to avoid shell escaping issues. In production, K8sExecutionManager sets these programmatically, avoiding shell complexity.

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
- Custom `K8sExecutionManager` that extends `jupyter-scheduler.ExecutionManager` and runs notebook jobs in Kubernetes pods using a pre-populated Persistent Volume Claim (PVC) approach for input file handling
- Parameter injection and multiple output formats
- PVC-based file handling for any notebook size
- Configurable CPU/memory limits
- Event-driven job monitoring with Watch API

### Planned 🚧
- s3 / configurable approach for input file handling
- GPU resource configuration for k8s jobs from UI
- Kubernetes job stop/deletion from UI
- Kubernetes-native scheduling from UI
- PyPI package publishing
