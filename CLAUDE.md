# Jupyter Scheduler K8s - Development Guide

This project provides a Kubernetes backend for Jupyter Scheduler. This guide covers common development tasks.

## Project Structure

- `src/jupyter_scheduler_k8s/` - Main Python package (currently empty)
- `image/` - Docker image configuration with Pixi-based Python environment
- `local-dev/` - Local development configuration (Kind cluster)
- `Makefile` - Build and development automation

## Prerequisites

- **macOS**: Finch (for container management)
- **Kind**: For local Kubernetes clusters
- **Python 3.13+**: Required by pyproject.toml

## Common Development Tasks

### Initial Setup

```bash
# Initialize Finch VM and create Kind cluster
make setup
```

This command:
- Detects macOS and initializes/starts Finch VM
- Creates a Kind cluster named 'jupyter-scheduler-k8s'
- Sets up kubeconfig at `local-dev/kind/.kubeconfig`

### Build Docker Image

```bash
# Build the application Docker image
make build-image
```

Builds `jupyter-scheduler-k8s:latest` using:
- Base: `ghcr.io/prefix-dev/pixi:0.50.2-bookworm-slim`
- Pixi environment management
- Simple "Hello World" Python application

### Kubernetes Development

```bash
# Configure kubectl for Kind cluster
make kubectl-kind

# Test cluster connection
kubectl get nodes
```

### Cleanup

```bash
# Remove Kind cluster and stop Finch VM
make clean
```

## Dependencies

The project uses:
- `jupyter-scheduler>=2.11.0` - Core scheduler functionality
- `jupyterlab>=4.4.5` - Jupyter Lab integration  
- `kubernetes>=33.1.0` - Kubernetes API client
- `uv` for build system

## Image Configuration

The Docker image uses Pixi for environment management:
- Platforms: `osx-arm64` (configured in `image/pixi.toml`)
- Start command: Currently placeholder "Put your command to run a notebook as a job here"

## Development Status

This appears to be an early-stage project with:
- Basic project structure established
- Development environment setup complete
- Core implementation not yet started (empty `__init__.py`)
- Placeholder application code in `image/main.py`