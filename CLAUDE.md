# Jupyter Scheduler K8s - Development Guide

**📖 Read README.md first for installation, setup, and usage instructions.**

This document contains development notes, architecture decisions, and lessons learned for maintainers.

## Project Structure

- `src/jupyter_scheduler_k8s/` - Main Python package with K8sExecutionManager
- `image/` - Docker image with Pixi-based Python environment and notebook executor
- `local-dev/` - Local development configuration (Kind cluster)
- `Makefile` - Build and development automation with auto-detection

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

### S3 Configuration (Optional - for Durability)

**Purpose:** Persist files beyond jupyter-scheduler server and K8s cluster failures

**Environment Variables:**
- `S3_BUCKET`: S3 bucket name (required for S3 mode, e.g., "my-notebook-outputs")
- `S3_ENDPOINT_URL`: Custom S3 endpoint (optional, for MinIO, GCS S3 API, etc.)

**AWS Credentials:** Use standard AWS credential methods:
- IAM roles (recommended for EC2/EKS)
- AWS credentials file (`~/.aws/credentials`)  
- Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)

**Required Configuration:**
- `S3_BUCKET` must be set - no fallback mode
- Ensures consistent production-like testing across all environments

**S3-Compatible Storage:**
- **AWS S3**: Standard configuration
- **MinIO**: Set `S3_ENDPOINT_URL=<your-minio-server-url>`
- **Google Cloud Storage**: Set `S3_ENDPOINT_URL=https://storage.googleapis.com`

**Example Configuration:**
```bash
# Required: S3 bucket and AWS credentials
export S3_BUCKET="..."
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
# Optional: for temporary credentials
export AWS_SESSION_TOKEN="..."
# Optional: for S3-compatible storage
export S3_ENDPOINT_URL="..."

jupyter lab --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
```

**Critical:** AWS credentials must be set in the same terminal session where you launch Jupyter Lab. The system passes these credentials to Kubernetes containers for S3 access.

### Phase 3: Future Enhancements
- **GPU resource configuration for k8s jobs from UI**: Configure GPU count/type for ML workloads
- **Kubernetes job stop/deletion from UI**: Implement `stop_job` and `delete_job` methods
- **Kubernetes-native scheduling from UI**: Use K8s CronJobs instead of SQL-based job definitions
- **PyPI package publishing**: Set up publishing scaffolding and publish to PyPI
- **CI/CD**: Set up automated testing and deployment pipeline
- **Cloud Cluster Testing**: Test deployment on EKS, GKE, AKS (should work but untested)


## Lessons Learned

### Development Principles Learned

**Balancing High Standards with Practical Implementation:**
- **Insist on high standards**: Question quick fixes and temporary solutions - they often lead to better architecture
- **Stay within scope**: Focus on implementing ExecutionManager interface, not reinventing jupyter-scheduler
- **Avoid over-engineering**: Start simple, evolve based on real requirements, don't build for imaginary problems
- **Use standard patterns**: Follow established K8s community practices rather than inventing custom solutions
- **Question "good enough"**: When something feels hacky (like exec file transfer), investigate proper alternatives

### Debugging Insights
- **Pod debugging sequence**: Always check in this order: `kubectl get pods` → `kubectl describe pod` → `kubectl logs pod` → `kubectl exec` commands
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

## S3 Implementation Architecture

**Requirement:** Files must survive jupyter-scheduler server and K8s cluster failures.

**Solution: AWS CLI for S3 operations**
- Handles directory recursion, multipart uploads, retries automatically
- Works with S3-compatible storage (AWS S3, MinIO, GCS with S3 API)
- Single command for complex operations: `aws s3 sync source/ dest/`
- **Industry standard**: Used by major systems for reliable S3 operations:
  - **AWS Batch**: Official container file transfers
  - **GitHub Actions**: aws-actions/configure-aws-credentials + aws s3 sync
  - **Kubernetes Jobs**: Argo Workflows, Tekton Pipelines S3 artifacts
  - **Apache Airflow**: S3Hook uses aws cli subprocess calls
  - **Jupyter Enterprise Gateway**: AWS CLI for remote kernel file management

**Implementation:**
- K8sExecutionManager: `subprocess.run(['aws', 's3', 'sync', staging_dir, s3_path])`
- Container: `aws s3 sync $S3_INPUT_PREFIX /tmp/inputs/`
- Add `awscli` to both pyproject.toml and container image
- Required S3_BUCKET env var, no fallback for consistency

## Current Implementation Status

### Latest Architecture: S3 Storage (Production Ready ✅)
1. **Upload inputs** - AWS CLI sync to S3 bucket
2. **Container execution** - Job downloads from S3, executes notebook, uploads outputs  
3. **Download outputs** - AWS CLI sync from S3 to staging directory
4. **Durability** - Files survive cluster failures, can be retrieved later

**Key Implementation Details:**
- **AWS credentials passed at runtime**: K8sExecutionManager passes host AWS credentials to containers via environment variables
- **Auto pod debugging**: When jobs fail, automatically captures pod logs and container status for troubleshooting


## Code Quality Standards

- **Comments**: Only add comments that explain parts of code that are not evident from the code itself
- Explain WHY something is done when the reasoning isn't obvious
- Comments above the line they describe, not inline
- Explain WHAT is being done when the code logic is complex or non-obvious
- If the code is self-evident, no comment is needed
- **Quality**: Insist on highest quality standards while avoiding over-engineering
- **Scope**: Stay strictly within defined scope - no feature creep or unnecessary complexity

## Documentation Standards

- **Placeholder URLs/Values**: Use angle bracket format `<placeholder-description>`. Examples: `<your-s3-endpoint-url>`, `<your-minio-server-url>`, `<your-namespace>`
- **Comment Style**: Comments above the line they describe, not inline
- **README vs CLAUDE.md**: README is user-facing, CLAUDE.md is development/architectural context
