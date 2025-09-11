# Jupyter Scheduler K8s - Development Guide

**📖 Read README.md first for installation, setup, and usage instructions.**

This document contains development notes, architecture decisions, and lessons learned for maintainers.

## Project Structure

- `src/jupyter_scheduler_k8s/` - Main Python package with K8sExecutionManager and K8sDatabaseManager
- `src/advanced-options.tsx` - React component for resource configuration UI
- `src/index.ts` - JupyterLab plugin registration
- `image/` - Docker image with Pixi-based Python environment and notebook executor
- `local-dev/` - Local development configuration (Kind cluster)
- `package.json` - Frontend build configuration
- `tsconfig.json` - TypeScript configuration
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

1. **Minimal Extension**: Only override ExecutionManager and DatabaseManager, reuse everything else from jupyter-scheduler
2. **Container Simplicity**: Container just executes notebooks, unaware of K8s or scheduler
3. **No Circular Dependencies**: Container doesn't depend on jupyter-scheduler package
4. **Jobs as Records**: Execution Jobs serve as both the computational workload AND the database records
5. **Staging Compatibility**: Work with jupyter-scheduler's existing file staging mechanism

## Data Flow (Jobs-as-Records with S3 Storage)

1. User creates job → jupyter-scheduler copies files to staging directory
2. jupyter-scheduler calls K8sExecutionManager.execute()
3. K8sExecutionManager uploads files to S3
4. Execution Job is created with database metadata (labels/annotations)
5. Job downloads files from S3, executes notebook, uploads outputs to S3
6. K8sExecutionManager downloads outputs from S3 to staging directory
7. **Job persists as database record** (no cleanup)
8. User can download results and view job history via jupyter-scheduler UI

## Implementation Status

### Current Architecture: Jobs-as-Records with S3 Storage ✅
- **Database**: Execution Jobs (`nb-job-*`) serve as permanent records with labels/annotations
- **File Storage**: S3 for durability across cluster failures
- **Monitoring**: Watch API for real-time job status updates  
- **Resource Management**: User-configurable CPU/memory/GPU resources via UI
- **Platform Support**: Works with any K8s cluster (Kind, minikube, cloud providers)
- **Frontend Extension**: JupyterLab extension for resource configuration UI

### Development Environment ✅  
- **Local Setup**: Kind + Finch for development
- **Container**: Pixi-based Python environment with nbconvert
- **Auto-Detection**: Smart imagePullPolicy based on cluster context
- **Debugging**: Automatic pod log capture on failures

### S3 Configuration (Required)

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

### GPU and Resource Management ✅ 

**UI Extension**: JupyterLab plugin that extends jupyter-scheduler's AdvancedOptions
- **Direct Input Fields**: Simple text fields for CPU, memory, GPU specifications
- **Optional Configuration**: Default uses cluster administrator settings (no resource limits)
- **Format Examples**: CPU ("2", "500m"), Memory ("4Gi", "512Mi"), GPU ("1" or empty)

**Backend Processing**:
- **K8sExecutionManager**: Extracts resource specifications from job model
- **Resource Application**: Only applies user-specified resources, respects cluster defaults otherwise
- **GPU Support**: NVIDIA GPU resource allocation (`nvidia.com/gpu`)
- **Validation**: Minimal validation (negative GPU prevention), lets K8s handle format validation

**Resource Configuration Flow**:
1. User enters resource values in jupyter-scheduler UI
2. Values stored in `runtime_environment_parameters`
3. K8sExecutionManager extracts resources from runtime parameters
4. Resources applied to Job's pod template specification
5. Kubernetes scheduler attempts to place pod on suitable node
6. If no suitable node, pod remains Pending and error reported to UI

**Scheduling Error Detection**:
- Wait 30 seconds after job creation (avoid false positives during normal startup)
- Check if pod is still Pending → likely scheduling issue
- Extract error from pod conditions/events
- Update job status with user-friendly message
- Error appears in UI job detail view

### Future Development Roadmap
- **Job Management**: Stop/delete running K8s jobs from UI (`stop_job`, `delete_job` methods)
- **CRD Migration**: Custom Resource Definitions for optimized metadata storage
- **Job Archival**: Automated cleanup of old execution Jobs
- **K8s-native Scheduling**: CronJobs integration from UI
- **Usage Analytics**: Resource utilization tracking and recommendations
- **Cluster Integration**: Dynamic resource profiles based on cluster capabilities
- **PyPI Distribution**: Official package publishing


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

## S3 Implementation Details

**Key Implementation:**
- **AWS credentials passed at runtime**: K8sExecutionManager passes host AWS credentials to containers via environment variables
- **Auto pod debugging**: When jobs fail, automatically captures pod logs and container status for troubleshooting
- **AWS CLI for reliability**: Handles directory recursion, multipart uploads, retries automatically

## Architecture: Jobs-as-Records Implementation

### Current Approach
Execution Jobs serve as both computational workload AND database records:
- **Job Metadata**: Stored in labels/annotations on execution Jobs (`nb-job-*` pattern)
- **Job Persistence**: Execution Jobs remain after completion as permanent database records
- **Query Interface**: K8sSession/K8sQuery mimic SQLAlchemy patterns using K8s label selectors
- **Storage Location**: Job data in annotations, fast queries via labels

### Implementation Details
- **Execution Jobs** contain complete job data in `jupyter-scheduler.io/job-data` annotation
- **Label Selectors** enable efficient server-side filtering (`jupyter-scheduler.io/job-id`, etc.)
- **No Cleanup**: `_cleanup_job()` calls removed, Jobs persist indefinitely
- **Database Interface**: K8sDatabaseManager.commit() is now a no-op

### Storage Considerations & Future Enhancements

#### Resource Usage
- **Current**: Each Job ~1-2KB metadata + full K8s Job spec
- **Scale Impact**: 10,000 jobs ≈ 10-20MB etcd storage
- **Recommendation**: Archive Jobs older than 30-90 days for large deployments

#### Future: CRD-Based Database (Next Architecture Evolution)
**When to Migrate**: When query performance or storage optimization becomes critical

**CRD Benefits**:
- **Semantic Correctness**: Purpose-built API objects instead of abusing Jobs
- **Storage Efficiency**: ~1KB per record vs current Job overhead  
- **Query Performance**: Native indexing and custom controllers
- **API Integration**: First-class kubectl support (`kubectl get scheduledjobs`)

**Implementation Path**:
```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: scheduledjobs.jupyter-scheduler.io
spec:
  # ... CRD definition for ScheduledJob resource
```

#### Archival Strategy (Implementation Ready)
```python
# Example: Archive jobs older than retention period
def archive_old_jobs(retention_days=30):
    old_jobs = k8s_batch.list_namespaced_job(
        label_selector=f"jupyter-scheduler.io/created-before={cutoff_date}"
    )
    # Extract metadata to ConfigMap/S3, delete Job
```

## Meta-Learnings for Future Claude Code Instances

### Architectural Decision-Making Process
When questioning existing architecture:
1. **Challenge assumptions**: Don't accept "that's how it was built" - question if the current approach makes semantic sense
2. **Follow the data flow**: Trace what actually contains the valuable information (execution Jobs had all the context)
3. **Apply first principles**: Ask "what is the natural representation of this concept in the target system?"
4. **Consider resource efficiency**: Balance semantic correctness with resource usage

### Jobs-as-Records Decision Process
**Original questioning**: "Why use separate job abstractions for execution and record keeping? Wouldn't it make sense to use the same?"

**Analysis approach**:
- **Semantic consistency**: Execution Jobs ARE the work that was done - they should be the record
- **Information completeness**: Execution Jobs contain logs, resource usage, exact specs - far more valuable than metadata shadows
- **Kubernetes principles**: Jobs are designed to represent "work that was completed"
- **Resource analysis**: Busybox containers were wasteful for storing JSON data

**Implementation philosophy**: Make the architecture match the domain model - the execution IS the record.

### Technical Implementation Patterns
- **User counterpoints validation**: When users challenge technical decisions, investigate thoroughly - they often spot architectural inconsistencies
- **Security context evaluation**: In development environments, don't over-engineer security if the baseline (JupyterLab) already has broad access
- **Future-proofing balance**: Plan for scale (mention CRDs) but implement the simplest correct solution first

### Documentation Audience Separation  
- **README.md**: User and developer-facing, focus on features and usage
- **CLAUDE.md**: Internal development guidance, include decision reasoning and future Claude context
- **Avoid redundancy**: Don't repeat information between docs, reference when needed

## Code Quality Standards (Applied)

### Zen of Python Principles
- **No TODO comments**: Technical debt markers don't belong in production
- **DRY (Don't Repeat Yourself)**: Extract common code, avoid duplication
- **Document magic numbers**: Explain WHY 30 seconds, not just WHAT
- **Simple is better than complex**: Direct solutions over clever abstractions
- **Explicit is better than implicit**: Clear parameter passing, no hidden state

### Code Review Standards
- **Remove redundant code**: Delete unused imports, variables, functions
- **Consistent error handling**: Uniform patterns throughout codebase
- **Minimal defensive programming**: Trust K8s validation, don't over-validate
- **Clear variable names**: `k8s_cpu` not `cpu_val`, be specific

## Logging Standards (Simplified)

- **Emoji Usage**: Only for major phases (upload, download, success, failure)
  - ✅ Keep: `📤 Uploading files`, `❌ S3 upload failed`
  - ❌ Remove: Emojis from configuration logging
- **Configuration Logging**: Clean, minimal output
  - Simple: `Initializing K8sExecutionManager`
  - Not: `🔧 Initializing with environment configuration:`
- **Error Messages**: Clear, actionable, no patronizing language
  - Good: "Cannot schedule: No nodes with GPU available"
  - Bad: "Configured by your administrator" (patronizing)

## Documentation Standards

- **Placeholder Values**: Use angle brackets `<placeholder>` for user values
- **Comment Style**: Above the line, explain WHY not WHAT when obvious
- **Document Separation**:
  - **README.md**: User guide, troubleshooting, examples
  - **CLAUDE.md**: Architecture, decisions, future Claude context
  - **No duplication**: Each fact in one place only

## K8s Resource Architecture (Key Concepts)

### Job vs Pod Hierarchy
- **Job**: Contains pod template with resource specifications
- **Pod**: Created by Job, actually consumes resources
- **Scheduling**: Happens at Pod level, not Job level
- **Resource specs**: Defined on Job template, applied to Pod

### Watch API Pattern (Best Practice)
- **Not polling**: Uses K8s Watch API for real-time events
- **Event-driven**: Reacts to job status changes as they happen
- **Timeout**: Safety limit (10 min), not polling interval
- **Scheduling timeout**: Configurable wait for pod placement (default 5 min)

### 30-Second Delay Reasoning
- **Normal startup**: 1-5s scheduling + 10-20s image pull + startup
- **30s threshold**: Confident it's a real problem, not normal delay
- **Prevents false positives**: All pods start as Pending initially
