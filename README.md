# jupyter-scheduler-k8s

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) - execute notebook jobs in containers.

## Quick Start

### Local Development (Kind)

**Prerequisites**: 
- **macOS**: `brew install finch kind`
- **Linux/Windows**: [Finch install guide](https://github.com/runfinch/finch#installation), [Kind install guide](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)

```bash
# Setup environment (from project root)
make dev-env        # Creates Kind cluster, builds and loads image

# Install dependencies
pip install jupyterlab jupyter-scheduler

# Launch with K8s backend (auto-detects Kind cluster)
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

### Other Kubernetes Clusters

*Cloud deployment instructions coming soon (EKS, GKE, AKS, etc.)*

## Container Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NOTEBOOK_PATH` | Yes | - | Path to notebook file |
| `OUTPUT_PATH` | Yes | - | Path to save executed notebook |
| `PARAMETERS` | No | `{}` | JSON parameters to inject |
| `PACKAGE_INPUT_FOLDER` | No | `false` | Include all files from notebook directory |

## Testing Container Locally

**Test with provided notebook**:
```bash
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_notebook.ipynb" \
  -e OUTPUT_PATH="/workspace/output.ipynb" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest
```

**Test with copying input folder** (using `PACKAGE_INPUT_FOLDER` environment variable):
```bash
finch run --rm \
  -e NOTEBOOK_PATH="/workspace/tests/test_with_data.ipynb" \
  -e OUTPUT_PATH="/workspace/output.ipynb" \
  -e PACKAGE_INPUT_FOLDER="true" \
  -v "$(pwd):/workspace" \
  jupyter-scheduler-k8s:latest
```

## Configuration Options

### Auto-Detection
- **Image Pull Policy**: Automatically detected based on K8s context
  - Kind/minikube/docker-desktop → `Never` (local images)
  - EKS/GKE/AKS/others → `Always` (registry images)

### Basic Settings
- `K8S_NAMESPACE` - Kubernetes namespace (default: `default`)
- `K8S_IMAGE` - Container image to use (default: `jupyter-scheduler-k8s:latest`)
- `K8S_IMAGE_PULL_POLICY` - Override auto-detection (`Always` | `Never`)
- `K8S_STORAGE_SIZE` - PVC size per job (default: `100Mi`)

### Resource Limits
- `K8S_RECEIVER_MEMORY_REQUEST/LIMIT` - Init container memory (default: `64Mi`/`256Mi`)
- `K8S_RECEIVER_CPU_REQUEST/LIMIT` - Init container CPU (default: `100m`/`500m`)
- `K8S_EXECUTOR_MEMORY_REQUEST/LIMIT` - Main container memory (default: `512Mi`/`2Gi`)
- `K8S_EXECUTOR_CPU_REQUEST/LIMIT` - Main container CPU (default: `500m`/`2000m`)

## Development

**Individual steps** (if needed):
```bash
make setup          # Finch VM + Kind cluster  
make build-image    # Build container
make load-image     # Load into Kind
make kubectl-kind   # Configure kubectl
```

**Cleanup**:
```bash
make clean          # Remove cluster + stop Finch VM
```