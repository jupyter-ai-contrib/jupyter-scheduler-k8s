# Jupyter Scheduler K8s - Development Guide

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) that extends the ExecutionManager to run notebook jobs in containers.

## Project Structure

- `src/jupyter_scheduler_k8s/` - Main Python package with K8sExecutionManager
- `image/` - Docker image with Pixi-based Python environment and notebook executor
- `local-dev/` - Local development configuration (Kind cluster)
- `Makefile` - Build and development automation with auto-detection

## Prerequisites

**For local development with Makefile:**
- **macOS**: Finch (for container management)
- **Kind**: For local Kubernetes clusters  
- **Python 3.9+**: Required by pyproject.toml

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
kubectl logs <pod-name>
```

### Cleanup

```bash
# Remove Kind cluster and stop Finch VM
make clean
```

## How It Works

1. Create PVC for storage
2. Helper pod transfers input files via `kubectl cp`
3. Job executes notebook in container
4. Helper pod retrieves outputs via `kubectl cp`

```
jupyter-scheduler → K8sExecutionManager → Kubernetes Job → Container
```

## Dependencies

- `jupyter-scheduler>=2.11.0` - Core scheduler functionality
- `jupyterlab>=4.4.5` - Jupyter Lab integration  
- `kubernetes>=33.1.0` - Kubernetes API client with Watch API support
- `nbformat`, `nbconvert` - Notebook processing
- `uv` for build system

## Implementation Requirements

- **`supported_features` method**: Must be kept up to date with actual backend capabilities
  - Currently supports: `parameters`, `output_formats`
  - Explicitly declares unsupported: `timeout_seconds`, `job_name`, `stop_job`, `delete_job`
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

## Key Design Principles

1. **Minimal Extension**: Only override ExecutionManager, reuse everything else from jupyter-scheduler
2. **Container Simplicity**: Container just executes notebooks, unaware of K8s or scheduler
3. **No Circular Dependencies**: Container doesn't depend on jupyter-scheduler package
4. **Staging Compatibility**: Work with jupyter-scheduler's existing file staging mechanism

## Data Flow (Pre-Populated PVC Architecture)

1. User creates job → jupyter-scheduler copies files to staging directory
2. jupyter-scheduler calls our K8sExecutionManager.execute()
3. K8sExecutionManager creates PVC for storage
4. Helper pod created → files transferred via `kubectl cp` → helper pod deleted
5. Main execution job runs with pre-populated PVC
6. After completion, new helper pod retrieves outputs via `kubectl cp`
7. K8sExecutionManager places outputs in staging directory
8. User can download results via jupyter-scheduler UI

## Implementation Status

### Phase 1: Container Implementation ✅
- Kind cluster setup complete
- Container executes notebooks with parameters
- Uses nbconvert (same as jupyter-scheduler)
- Minimal dependencies, no circular refs
- Supports `PACKAGE_INPUT_FOLDER` for including data files

### Phase 2: K8s Backend Implementation ✅

### Pre-Populated PVC Architecture (Production-Ready)
- **Storage**: PVC (PersistentVolumeClaim) for production-ready file handling
  - Works with all standard K8s clusters (Kind, minikube, EKS, GKE, AKS)
  - Handles notebooks of any size, not limited by ConfigMap 1MB restriction
  - Standard K8s pattern used in production

- **File Transfer**: Helper pods with `kubectl cp`
  - Pre-populate PVC before execution
  - Retrieve outputs after completion
  - Standard K8s file transfer method (used by Helm, Argo, etc.)
  - Sequential operations instead of complex container coordination

- **Auto-Detection**: Smart environment detection
  - **Local clusters** (Kind/minikube) → `imagePullPolicy: Never`
  - **Cloud clusters** (EKS/GKE/AKS) → `imagePullPolicy: Always`
  - **Context-aware**: Reads kubectl current-context for detection

- **Watch API**: Event-driven job monitoring
  - **Real-time**: Uses K8s Watch API instead of polling
  - **Efficient**: Immediate response to state changes
  - **Fallback**: Graceful degradation to polling if watch fails

- **Resource Management**: Configurable resource allocation
  - **Configurable limits**: Resource controls for execution containers
  - **Right-sized defaults**: Appropriate resource allocation for each role

### Cluster Configuration (Platform Agnostic)
- **User provides K8s cluster** - any distribution (Kind, minikube, EKS, GKE, etc.)
- **Default**: Uses `~/.kube/config` (standard kubectl location)
- **Configurable**: Can point to any kubeconfig path
- **In-cluster**: When jupyter-scheduler runs inside K8s
- Settings: namespace, image, resource limits all configurable

### Phase 3: Future Enhancements
- **GPU resource configuration for k8s jobs from UI**: Configure GPU count/type for ML workloads
- **Kubernetes job stop/deletion from UI**: Implement `stop_job` and `delete_job` methods
- **Kubernetes-native scheduling from UI**: Use K8s CronJobs instead of SQL-based job definitions
- **PyPI package publishing**: Set up publishing scaffolding and publish to PyPI
- **CI/CD**: Set up automated testing and deployment pipeline
- **Cloud Cluster Testing**: Test deployment on EKS, GKE, AKS (should work but untested)

## Configuration

To use the K8s backend with jupyter-scheduler:

```python
# jupyter_server_config.py
c.SchedulerApp.execution_manager_class = "jupyter_scheduler_k8s.executors.K8sExecutionManager"
```

**Environment variables:**
- `K8S_NAMESPACE`: Kubernetes namespace (default: "default")
- `K8S_IMAGE`: Container image to use (default: "jupyter-scheduler-k8s:latest")
- `K8S_IMAGE_PULL_POLICY`: Image pull policy (default: "Always", use "Never" for local)
- `K8S_STORAGE_SIZE`: PVC size for notebooks (default: "100Mi")
- Resource limits:
  - Notebook executor: `K8S_EXECUTOR_MEMORY_REQUEST/LIMIT`, `K8S_EXECUTOR_CPU_REQUEST/LIMIT`

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
- **Temporary pods anti-pattern**: Avoid creating separate pods for file access after job completion. Use sidecar containers within the same job for atomic lifecycle management.
- **jupyter-scheduler integration**: Only need to implement ExecutionManager interface - don't overthink the integration scope.
- **File size assumptions**: Never artificially limit file sizes (like "small files only") - real notebooks can be large and have data dependencies.
- **JSON corruption via kubectl exec**: When transferring JSON/notebooks via exec, output gets corrupted (Python dict format `{'key':` instead of valid JSON `{"key":`). Always use base64 encoding for structured data transfer.

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

### Output File Generation and Collection
- **Filename consistency is critical**: Container-generated filenames must exactly match jupyter-scheduler's staging path expectations
- **Use staging paths for filenames**: Set OUTPUT_PATH using `Path(self.staging_paths['ipynb']).name` to ensure container generates files with correct timestamps
- **Align generation and collection**: Both container generation and K8s collection logic must derive filenames from the same staging path source
- **Debugging pattern**: When download options are missing, check:
  1. Container logs for file generation errors
  2. Output collection logs for file transfer failures  
  3. Filename mismatches between generation and collection
- **Generic output handling**: Let nbconvert handle format details, treat all outputs as text (following jupyter-scheduler pattern)
- **Environment variable coordination**: OUTPUT_PATH and OUTPUT_FORMATS must align between executor and container

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

## Code Quality Standards

- **Comments**: Only add comments that explain parts of code that are not evident from the code itself
- Explain WHY something is done when the reasoning isn't obvious
- Explain WHAT is being done when the code logic is complex or non-obvious
- If the code is self-evident, no comment is needed
- **Quality**: Insist on highest quality standards while avoiding over-engineering
- **Scope**: Stay strictly within defined scope - no feature creep or unnecessary complexity
- **Dead Code Removal**: Always aggressively remove dead code from old implementations
  - When architecture evolves (e.g., sidecar → pre-populated PVC), delete all unused methods
  - Don't leave old approaches "just in case" - it creates confusion and bloat
  - A 240+ line reduction (like removing the old sidecar code) is a good thing
  - Clean implementations are easier to understand, debug, and maintain
