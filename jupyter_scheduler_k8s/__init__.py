"""Kubernetes backend for jupyter-scheduler."""

from .executors import K8sExecutionManager
from .database_manager import K8sDatabaseManager

__version__ = "0.1.0"
__all__ = ["K8sExecutionManager", "K8sDatabaseManager"]


def _jupyter_labextension_paths():
    """Return the paths to the labextension for installation."""
    return [{"src": "labextension", "dest": "jupyter-scheduler-k8s"}]
