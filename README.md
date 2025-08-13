# jupyter-scheduler-k8s

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) - execute notebook jobs in containers.

## Quick Start

**Prerequisites**: 
- **macOS**: 
  ```bash
  brew install finch kind
  ```
- **Linux/Windows**: [Finch install guide](https://github.com/runfinch/finch#installation), [Kind install guide](https://kind.sigs.k8s.io/docs/user/quick-start/#installation)

1. **Build container** (from project root):
   ```bash
   make build-image
   ```

2. **Test with provided notebook**:
   ```bash
   finch run --rm \
     -e NOTEBOOK_PATH="/workspace/tests/test_notebook.ipynb" \
     -e OUTPUT_PATH="/workspace/output.ipynb" \
     -v "$(pwd):/workspace" \
     jupyter-scheduler-k8s:latest
   ```

## Container Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NOTEBOOK_PATH` | Yes | - | Path to notebook file |
| `OUTPUT_PATH` | Yes | - | Path to save executed notebook |
| `PARAMETERS` | No | `{}` | JSON parameters to inject |

## Development Setup

**Prerequisites**: Finch, Kubernetes cluster

**Local cluster with Kind**:
```bash
kind create cluster --name jupyter-scheduler-k8s
kubectl get nodes
```

