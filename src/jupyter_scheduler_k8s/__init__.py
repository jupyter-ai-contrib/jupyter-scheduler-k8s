"""Kubernetes backend for jupyter-scheduler."""

from .executors import K8sExecutionManager

__version__ = "0.1.0"
__all__ = ["K8sExecutionManager"]
