# jupyter-scheduler-k8s

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) - execute notebook jobs in containers.

## Overview

Extends [Jupyter Scheduler](https://github.com/jupyter-server/jupyter-scheduler) to run notebook jobs in Kubernetes pods instead of local processes by implementing a custom ExecutionManager.

## How It Works

1. Create PVC for storage
2. Helper pod transfers input files via `kubectl cp`
3. Job executes notebook in container
4. Helper pod retrieves outputs via `kubectl cp`

```
jupyter-scheduler → K8sExecutionManager → Kubernetes Job → Container
```

Supports parameter injection, multiple output formats, and works with any K8s cluster (Kind, minikube, EKS, GKE, AKS).

## Requirements

- Kubernetes cluster with kubectl access
- Python 3.9+
- jupyter-scheduler>=2.11.0

**For local development:**
- Finch and Kind (install guides: [Finch](https://github.com/runfinch/finch#installation), [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation))

## Installation

### Local Deployment

```bash
# One-command setup: builds image, loads into Kind cluster (run from repo directory)
make dev-env

# Install jupyter-scheduler
pip install jupyterlab jupyter-scheduler

# Configure environment (as shown by make dev-env output)
export K8S_IMAGE="jupyter-scheduler-k8s:latest"
export K8S_IMAGE_PULL_POLICY="Never"

# Launch Jupyter Lab with Jupyter Scheduler configured to use Kubernetes backend
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

### Cloud Deployment

```bash
# Install the Python package (run from repo directory)
pip install -e .

# Install jupyter-scheduler
pip install jupyterlab jupyter-scheduler

# Build image (run from repo directory)
make build-image

# Tag and push to your registry
finch tag jupyter-scheduler-k8s:latest your-registry/jupyter-scheduler-k8s:latest
finch push your-registry/jupyter-scheduler-k8s:latest

# Configure environment
export K8S_IMAGE="your-registry/jupyter-scheduler-k8s:latest"
export K8S_NAMESPACE="your-namespace"

# Launch Jupyter Lab with Jupyter Scheduler configured to use Kubernetes backend
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NOTEBOOK_PATH` | Yes | - | Path to notebook file |
| `OUTPUT_PATH` | Yes | - | Path to save executed notebook |
| `PARAMETERS` | No | `{}` | JSON parameters to inject |
| `PACKAGE_INPUT_FOLDER` | No | `false` | Include all files from notebook directory |
| `K8S_NAMESPACE` | No | `default` | Kubernetes namespace |
| `K8S_IMAGE` | No | `jupyter-scheduler-k8s:latest` | Container image to use |
| `K8S_IMAGE_PULL_POLICY` | No | Auto-detected | `Never` for local clusters, `Always` for cloud |
| `K8S_STORAGE_SIZE` | No | `100Mi` | PVC size per job |
| `K8S_EXECUTOR_MEMORY_REQUEST` | No | `512Mi` | Container memory request |
| `K8S_EXECUTOR_MEMORY_LIMIT` | No | `2Gi` | Container memory limit |
| `K8S_EXECUTOR_CPU_REQUEST` | No | `500m` | Container CPU request |
| `K8S_EXECUTOR_CPU_LIMIT` | No | `2000m` | Container CPU limit |

## Testing

**Prerequisites:**
- **macOS**: `brew install finch kind`
- **Linux/Windows**: [Finch install guide](https://github.com/runfinch/finch#installation), [Kind install guide](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)

**End-to-end testing:**

1. Setup local K8s environment:
```bash
make dev-env  # Creates Kind cluster, builds and loads image (run from repo directory)
```

2. Install dependencies:
```bash
pip install jupyterlab jupyter-scheduler
```

3. Launch with K8s backend:
```bash
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

4. Create and run notebook jobs through the jupyter-scheduler UI

5. Monitor execution (optional):
```bash
kubectl get pods    # See helper pods and execution jobs
kubectl get jobs    # See notebook execution jobs
kubectl logs <pod>  # See notebook execution logs
```

6. Cleanup:
```bash
make clean  # Remove Kind cluster
```

**Test container directly:**

```bash
# Test with provided notebook (run from repo directory)
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_notebook.ipynb" \
  -e OUTPUT_PATH="/workspace/output.ipynb" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest

# Test with copying input folder (run from repo directory)
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_with_data.ipynb" \
  -e OUTPUT_PATH="/workspace/output.ipynb" \
  -e PACKAGE_INPUT_FOLDER="true" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest
```

## Development

### Development Workflow

```bash
# After making code changes, rebuild and test
make build-image
make load-image

# Test changes
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

### Manual Setup Steps

```bash
make setup          # Finch VM + Kind cluster  
make build-image    # Build container
make load-image     # Load into Kind
make kubectl-kind   # Configure kubectl
```

### Cleanup

```bash
make clean          # Remove cluster + stop Finch VM
```

## Implementation Status

### Working Features ✅
- Parameter injection and multiple output formats
- PVC-based file handling for any notebook size
- Configurable CPU/memory limits
- Event-driven job monitoring

### Planned 🚧
- GPU resource configuration for k8s jobs from UI
- Kubernetes job stop/deletion from UI
- Kubernetes-native scheduling from UI
- PyPI package publishing
