from kubernetes import client, config
from jupyter_scheduler.managers import DatabaseManager

from .k8s_orm import K8sSession


class K8sDatabaseManager(DatabaseManager):
    """Database manager that uses Kubernetes Jobs for storage."""
    
    def create_session(self, db_url: str):
        """Create K8s session factory."""
        if not db_url.startswith("k8s://"):
            raise ValueError(f"K8sDatabaseManager only supports k8s:// URLs, got: {db_url}")
            
        namespace = db_url[6:] or "default"
        
        def session_factory():
            return K8sSession(namespace=namespace)
        return session_factory
    
    def create_tables(self, db_url: str, drop_tables: bool = False, Base=None):
        """Ensure K8s namespace exists. Base parameter is ignored for K8s."""
        if not db_url.startswith("k8s://"):
            return
            
        namespace = db_url[6:] or "default"
        
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        
        v1 = client.CoreV1Api()
        
        try:
            v1.read_namespace(name=namespace)
        except client.ApiException as e:
            if e.status == 404:
                namespace_body = client.V1Namespace(
                    metadata=client.V1ObjectMeta(name=namespace)
                )
                v1.create_namespace(body=namespace_body)