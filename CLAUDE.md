# Jupyter Scheduler K8s - Development Guide

This project provides a Kubernetes backend for Jupyter Scheduler.

## Project Structure

- `src/jupyter_scheduler_k8s/` - Main Python package with K8sExecutionManager
- `image/` - Docker image with Pixi-based Python environment and notebook executor
- `local-dev/` - Local development configuration (Kind cluster)
- `Makefile` - Build and development automation with auto-detection

## Prerequisites

**For local development with Makefile:**
- **macOS**: Finch (for container management)
- **Kind**: For local Kubernetes clusters  
- **Python 3.13+**: Required by pyproject.toml

**For any K8s cluster:**
- Existing Kubernetes cluster (Kind, minikube, EKS, GKE, AKS, etc.)
- kubectl configured for your cluster

## Setup

### Local Development (Kind)

```bash
# One-command setup - builds image, loads into Kind
make dev-env
```

This command:
- Runs `make setup` (Finch VM + Kind cluster)
- Runs `make build-image` (builds container)
- Runs `make load-image` (loads into Kind)
- Shows next steps for configuration

```bash
# Install Python dependencies
pip install jupyterlab jupyter-scheduler

# Launch with K8s backend (auto-detects Kind cluster)
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

### Development Workflow

```bash
# After making code changes, rebuild and test
make build-image
make load-image

# Test changes
jupyter lab --SchedulerApp.execution_manager_class="jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

### Debugging

```bash
# Check pods and jobs
kubectl get pods
kubectl get jobs

# Check specific pod details
kubectl describe pod <pod-name>

# Check container logs
kubectl logs <pod-name> -c notebook-executor
kubectl logs <pod-name> -c output-collector
```

### Cleanup

```bash
# Remove Kind cluster and stop Finch VM
make clean
```

## Architecture

**Sidecar Container Pattern**: Single K8s Job with three containers:
1. **Init container** (file-receiver): Handles file transfer via kubectl exec
2. **Main container** (notebook-executor): Executes notebook, signals completion  
3. **Sidecar container** (output-collector): Provides output access, exits when signaled

**Auto-Detection**: Automatically detects local vs cloud K8s contexts and sets appropriate image pull policies.

## Dependencies

- `jupyter-scheduler>=2.11.0` - Core scheduler functionality
- `jupyterlab>=4.4.5` - Jupyter Lab integration  
- `kubernetes>=33.1.0` - Kubernetes API client with Watch API support
- `nbformat`, `nbconvert` - Notebook processing
- `uv` for build system

## Development Status

**Production-Ready** ✅:
- Complete K8sExecutionManager implementation with sidecar pattern
- Auto-detection for local/cloud environments  
- Event-driven coordination using K8s Watch API
- Robust file transfer with base64 encoding
- Configurable resource limits for production deployment
- Works with Kind, minikube, EKS, GKE, AKS clusters

## Code Quality Standards

- **Comments**: Only add comments that explain parts of code that are not evident from the code itself
- Explain WHY something is done when the reasoning isn't obvious
- Explain WHAT is being done when the code logic is complex or non-obvious
- If the code is self-evident, no comment is needed
- **Quality**: Insist on highest quality standards while avoiding over-engineering
- **Scope**: Stay strictly within defined scope - no feature creep or unnecessary complexity

## Implementation Requirements

- **`supported_features` method**: Must be kept up to date with actual backend capabilities
  - Currently supports: `parameters`, `timeout_seconds`, `output_formats`
  - Explicitly declares unsupported: `job_name`, `stop_job`, `delete_job`
  - This method is abstract and required by jupyter-scheduler
  - Controls UI feature availability and user expectations

## Future Enhancements (PLAN.md)

- **Container scheduling support**: Enable containers to handle scheduling logic
- **Publishing capabilities**: Package and publish for broader distribution
- **Feature expansion**: Add job control features (stop/delete) if needed