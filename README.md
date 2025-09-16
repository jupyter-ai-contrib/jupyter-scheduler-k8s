# jupyter-scheduler-k8s

Kubernetes backend for [jupyter-scheduler](https://github.com/jupyter-server/jupyter-scheduler) - execute notebook jobs in containers instead of local processes.

## How It Works

1. Schedule notebook jobs through JupyterLab UI
2. Optionally configure CPU, memory, GPU resources
3. Files uploaded to S3 bucket for storage
4. Kubernetes Job created with resource specifications
5. Pod downloads files, executes notebook in isolated container
6. Results uploaded back to S3, then downloaded to JupyterLab
7. **Job persists as database record** - history and debugging info preserved

**Key features:**
- **Resource configuration** - CPU, memory, GPU allocation through UI
- **Native scheduling** - Uses K8s CronJobs with timezone support (no polling)
- **S3 storage** - files survive cluster or server failures
- **Jobs-as-records** - execution Jobs serve as both workload AND database
- Works with any Kubernetes cluster (Kind, minikube, EKS, GKE, AKS)

## Requirements

- Kubernetes cluster v1.27+ (for native timezone support in CronJobs)
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

# For full K8s-native mode (K8s database + K8s execution + CronJob scheduling):
jupyter lab \
  --SchedulerApp.scheduler_class="jupyter_scheduler_k8s.K8sScheduler" \
  --SchedulerApp.database_manager_class="jupyter_scheduler_k8s.K8sDatabaseManager" \
  --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
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

# For full K8s-native mode (K8s database + K8s execution + CronJob scheduling):
jupyter lab \
  --SchedulerApp.scheduler_class="jupyter_scheduler_k8s.K8sScheduler" \
  --SchedulerApp.database_manager_class="jupyter_scheduler_k8s.K8sDatabaseManager" \
  --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"
```

## Resource Configuration

### Through JupyterLab UI

When creating a job, expand "Advanced Options" to configure resources:

- **CPU**: Number of cores or millicores
  - Examples: `2` (2 cores), `500m` (0.5 cores)
  - Leave empty for cluster default
- **Memory**: RAM with units
  - Examples: `4Gi` (4 gigabytes), `512Mi` (512 megabytes)
  - Leave empty for cluster default
- **GPU**: Number of GPUs (whole numbers only)
  - Examples: `1`, `2`
  - Leave empty for no GPU

### Custom Environment Variables

Set custom environment variables for notebook execution via the Advanced Options UI. Variables are available in notebooks via `os.environ`.

### Backend Configuration

**K8s Environment Variables**:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `K8S_NAMESPACE` | No | `default` | Kubernetes namespace |
| `K8S_IMAGE` | No | `jupyter-scheduler-k8s:latest` | Container image |
| `K8S_IMAGE_PULL_POLICY` | No | Auto-detected | `Never` for local, `Always` for cloud |
| `K8S_SCHEDULING_TIMEOUT` | No | `300` | Maximum seconds to wait before reporting scheduling failure |

**S3 Storage Configuration** (required):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `S3_BUCKET` | Yes | - | S3 bucket name for file storage |
| `S3_ENDPOINT_URL` | No | - | Custom S3 endpoint (for MinIO, GCS S3 API, etc.) |

**AWS Credentials** (when using S3):
- **IAM roles** (recommended for EC2/EKS): Automatic
- **Credentials file**: `~/.aws/credentials` 
- **Environment**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`


## Troubleshooting

### Scheduling Errors

If your job fails with "Cannot schedule" errors:

**"Cannot schedule: No nodes with GPU available"**
- Your cluster doesn't have GPU nodes
- Solution: Remove GPU request or use a GPU-enabled cluster

**"Cannot schedule: Insufficient memory available"**
- Requested memory exceeds available cluster resources
- Solution: Reduce memory request or add nodes to cluster

**"Cannot schedule: Insufficient CPU available"**
- Requested CPU exceeds available cluster resources
- Solution: Reduce CPU request or add nodes to cluster

### Viewing Job Details with k9s

Use [k9s](https://k9scli.io/) to inspect jobs and pods:

```bash
# Install k9s
brew install k9s  # macOS

# Launch k9s
k9s

# Navigate:
# :jobs     - View all jobs
# :pods     - View all pods
# Enter     - See details
# d         - Describe (shows events)
# y         - View YAML (shows resource specs)
```

**Identifying resource-configured jobs:**
- Jobs WITH resources: Have `resources:` section in container spec
- Jobs WITHOUT: No `resources:` section (using cluster defaults)

### Common Issues

**Extensions not appearing:**
```bash
jupyter server extension list  # Check if installed
jupyter labextension list      # Check frontend
```

**S3 access denied:**
- Verify credentials: `aws s3 ls s3://$S3_BUCKET`
- Check IAM permissions for bucket access

**Pod stuck in Pending:**
- The system waits 30 seconds before checking (allows normal startup)
- After 30 seconds, scheduling errors will be detected and reported
- If still pending after 5 minutes (configurable via `K8S_SCHEDULING_TIMEOUT`), job fails
- Check pod events: `kubectl describe pod <pod-name>`
- Common causes: Insufficient resources, no nodes with requested GPU, image pull errors

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
- **K8s Execution**: Notebooks run in isolated Kubernetes pods
- **Resource Configuration**: CPU, memory, GPU allocation through UI
- **Smart Scheduling Error Detection**:
  - 30-second grace period prevents false positives during normal startup
  - Clear messages for resource issues (GPU, memory, CPU unavailable)
  - Configurable timeout for autoscaling clusters
- **S3 Storage**: Files persist beyond cluster or server failures
- **Jobs-as-Records**: Execution Jobs serve as database (no SQL needed)
- **K8s-Native Scheduling**: CronJobs for scheduled job definitions (timezone support)
- **Watch API Monitoring**: Real-time job status updates (no polling)
- **Parameter Injection**: Dynamic notebook customization
- **Multiple Output Formats**: HTML, PDF via nbconvert
- **File Handling**: Any notebook size with S3 operations

### Planned 🚧
- **Job Management**: Stop/delete running jobs from UI
- **Job Archival**: Automated cleanup of old execution Jobs
- **PyPI Publishing**: Official package distribution
