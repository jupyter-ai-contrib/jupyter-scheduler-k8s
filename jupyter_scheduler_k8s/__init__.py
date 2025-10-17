"""Kubernetes backend for jupyter-scheduler."""

import logging
import sys

from .executors import K8sExecutionManager
from .database_manager import K8sDatabaseManager
from .scheduler import K8sScheduler

__version__ = "0.1.0"
__all__ = ["K8sExecutionManager", "K8sDatabaseManager", "K8sScheduler"]

# Dev logging setup
def _setup_logging():
    root_logger = logging.getLogger('jupyter_scheduler_k8s')
    if not root_logger.handlers:
        root_logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(levelname)s %(asctime)s.%(msecs)03d K8sScheduler] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.propagate = False

_setup_logging()


def _jupyter_labextension_paths():
    """Return the paths to the labextension for installation."""
    return [{"src": "labextension", "dest": "jupyter-scheduler-k8s"}]
