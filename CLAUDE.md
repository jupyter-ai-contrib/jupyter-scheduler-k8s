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

## Architecture Overview

```
[JupyterLab + jupyter-scheduler]  (runs locally or on server)
            ↓
[jupyter-scheduler-k8s]  (our Python package - extends ExecutionManager)
            ↓
[Kubernetes Cluster]  (configurable: local Kind or remote)
            ↓
[Container Pod]  (runs notebook in isolation)
```


## Data Flow

1. User creates job → jupyter-scheduler copies files to staging directory
2. jupyter-scheduler calls our K8sExecutionManager.execute()
3. K8sExecutionManager creates PVC for storage
4. Helper pod created → files transferred via `kubectl cp` → helper pod deleted
5. Main execution job runs with pre-populated PVC
6. After completion, new helper pod retrieves outputs via `kubectl cp`
7. K8sExecutionManager places outputs in staging directory
8. User can download results via jupyter-scheduler UI

## Implementation Status

### IMPLEMENTED

**Core K8s Backend**
- `K8sExecutionManager` that extends jupyter-scheduler's `ExecutionManager`
- Container with Pixi-based Python environment for notebook execution
- Uses nbconvert (same as jupyter-scheduler) for output generation
- Supports parameterized notebook execution via `PARAMETERS` env var
- Supports `PACKAGE_INPUT_FOLDER` for including data files

**Pre-Populated PVC Architecture**
- **Storage**: PVC (PersistentVolumeClaim) for file handling
  - Works with all standard K8s clusters (Kind, minikube, EKS, GKE, AKS)
  - Handles notebooks of any size (no ConfigMap 1MB limit)
  - Standard K8s pattern used in production

- **File Transfer**: Helper pods with `kubectl cp`
  - Pre-populate PVC before execution
  - Retrieve outputs after completion
  - Standard K8s file transfer method (used by Helm, Argo, etc.)

- **Auto-Detection**: Smart environment detection
  - **Local clusters** (Kind/minikube) → `imagePullPolicy: Never`
  - **Cloud clusters** (EKS/GKE/AKS) → `imagePullPolicy: Always`
  - Reads kubectl current-context for automatic configuration

- **Watch API**: Event-driven job monitoring
  - Uses K8s Watch API for real-time status updates
  - Immediate response to job completion/failure
  
- **Resource Management**: Configurable limits
  - Separate controls for helper pods and execution pods
  - Environment variables for all resource settings
  - Production-ready defaults

**Platform Support**
- Works with any K8s distribution (Kind, minikube, EKS, GKE, AKS, etc.)
- Uses standard `~/.kube/config` (or configurable kubeconfig)
- Supports in-cluster execution when jupyter-scheduler runs inside K8s
- Configurable namespace, image, and resource limits

### NEXT STEPS

- **GPU Support**: Resource allocation for ML workloads with K8s GPU scheduling
- **Cloud Cluster Testing**: Validate deployment on EKS, GKE, AKS (should work but untested)
- **PyPI Publishing**: Package and publish to PyPI for easy installation
- **CI/CD Pipeline**: Automated testing and deployment with GitHub Actions
- **Advanced Job Control**: Enable stop/delete K8s jobs from jupyter-scheduler UI
  - Implement `stop_job` and `delete_job` methods in K8sExecutionManager
  - Update `supported_features` to enable UI controls
- **Container-Based Scheduling**: Support scheduled execution within containers
  - Integrate with jupyter-scheduler's `Scheduler` class
  - Container runs continuously with cron-like scheduling
  - Maintains connection to jupyter-scheduler UI for monitoring

## Configuration

To use the K8s backend with jupyter-scheduler:

```python
# jupyter_server_config.py
c.SchedulerApp.execution_manager_class = "jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

**Environment variables:**
- `K8S_NAMESPACE`: Kubernetes namespace (default: "default")
- `K8S_IMAGE`: Container image to use (default: "jupyter-scheduler-k8s:latest")
- `K8S_IMAGE_PULL_POLICY`: Image pull policy (default: `Never` for local, `Always` for cloud)
- `K8S_STORAGE_SIZE`: PVC size for notebooks (default: "100Mi")
- Resource limits:
  - Helper pods: `K8S_RECEIVER_MEMORY_REQUEST/LIMIT`, `K8S_RECEIVER_CPU_REQUEST/LIMIT`
  - Execution pods: `K8S_EXECUTOR_MEMORY_REQUEST/LIMIT`, `K8S_EXECUTOR_CPU_REQUEST/LIMIT`

## Lessons Learned

### Development Principles Learned

**Balancing High Standards with Practical Implementation:**
- **Insist on high standards**: Question quick fixes and temporary solutions - they often lead to better architecture
- **Stay within scope**: Focus on implementing ExecutionManager interface, not reinventing jupyter-scheduler
- **Avoid over-engineering**: Start simple, evolve based on real requirements, don't build for imaginary problems
- **Use standard patterns**: Follow established K8s community practices rather than inventing custom solutions
- **Question "good enough"**: When something feels hacky (like exec file transfer), investigate proper alternatives

### Architecture Evolution
- **Started**: ConfigMap approach → hit 1MB limit 
- **Tried**: Temporary pod pattern → worked but not elegant
- **Questioned**: Is this the right approach? (good instincts!)
- **Designed**: Sidecar pattern → proper K8s architecture  
- **Debugged**: Race conditions, timing issues, JSON corruption
- **Evolved**: Pre-populated PVC approach → simpler, more standard
- **Final**: Helper pods + kubectl cp → follows K8s best practices

### Key Technical Lessons
- **ConfigMap 1MB limit**: Don't use ConfigMaps for file storage - they have a 1MB size limit. Use PVCs instead.
- **Helper pods for file transfer**: Use temporary helper pods with `kubectl cp` for PVC access - cleaner than complex container coordination.
- **jupyter-scheduler integration**: Only need to implement ExecutionManager interface - don't overthink the integration scope.
- **File size assumptions**: Never artificially limit file sizes - real notebooks can be large and have data dependencies.
- **JSON corruption via kubectl exec**: When transferring JSON/notebooks via exec+stdin, output gets corrupted. Use `kubectl cp` instead.

### K8s Best Practices Learned
- **PVC over ConfigMap**: Use PersistentVolumeClaims for file storage - ConfigMaps have 1MB limit and etcd storage overhead
- **kubectl cp over exec hacks**: Use `kubectl cp` for file transfer instead of custom exec+stdin solutions - it's the standard K8s way
- **Helper pods for PVC access**: Create temporary helper pods to populate/access PVCs - cleaner than complex container coordination
- **Sequential operations over coordination**: Pre-populate → execute → collect is simpler than coordinating multiple containers
- **Watch API over polling**: Use K8s Watch API instead of polling for event-driven monitoring  
- **Auto-detection over manual config**: Check kubectl current-context and set `imagePullPolicy: Never` for local clusters (kind/minikube), `Always` for cloud
- **Standard patterns over custom solutions**: Follow how major K8s projects (Helm, Argo) handle file operations

### Debugging Insights
- **Pod debugging sequence**: Always check in this order: `kubectl get pods` → `kubectl describe pod` → `kubectl logs pod` → `kubectl exec` commands
- **HTTP 431 errors**: "Request Header Fields Too Large" errors occur when passing large base64 content via command arguments - use proper file transfer methods instead
- **Pattern recognition**: When you've seen a problem before (like JSON corruption), apply lessons learned immediately
- **Simplification through debugging**: Heavy debugging sessions often reveal simpler, more standard solutions
- **Community wisdom**: When fighting against the platform, step back and research how the community solves similar problems

## Current Implementation Status

### Latest Architecture: Pre-Populated PVC
1. **Create PVC** - Request storage from K8s
2. **Helper Pod + kubectl cp** - Populate PVC with input files  
3. **Simple Job** - Single container execution (no coordination complexity)
4. **Helper Pod + kubectl cp** - Collect outputs from PVC

### Benefits Achieved
- **No more HTTP 431 errors**: Eliminated exec+stdin file transfer hacks
- **Standard K8s patterns**: Using kubectl cp and helper pods like major projects
- **Simplified coordination**: Sequential operations instead of complex container signaling
- **Better error handling**: Clear separation between file transfer and execution phases

## Code Quality Standards

- **Comments**: Only add comments that explain parts of code that are not evident from the code itself
- Explain WHY something is done when the reasoning isn't obvious
- Explain WHAT is being done when the code logic is complex or non-obvious
- If the code is self-evident, no comment is needed
- **Quality**: Insist on highest quality standards while avoiding over-engineering
- **Scope**: Stay strictly within defined scope - no feature creep or unnecessary complexity
