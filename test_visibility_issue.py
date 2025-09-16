#!/usr/bin/env python3
"""Debug why CronJob-spawned jobs aren't visible in UI"""

from kubernetes import client, config
from jupyter_scheduler_k8s.k8s_orm import K8sSession, K8sQuery

# Load config
try:
    config.load_incluster_config()
except:
    config.load_kube_config()

k8s_batch = client.BatchV1Api()

print("=== Current K8s Jobs ===")
jobs = k8s_batch.list_namespaced_job(namespace="default")

for job in jobs.items:
    labels = job.metadata.labels or {}
    has_job_id_label = "scheduler.jupyter.org/job-id" in labels
    is_cronjob_spawned = labels.get("scheduler.jupyter.org/cronjob-spawned") == "true"

    print(f"\n{job.metadata.name}:")
    print(f"  Has job-id label: {has_job_id_label}")
    print(f"  Is CronJob-spawned: {is_cronjob_spawned}")

    if has_job_id_label:
        print(f"  job-id label value: {labels['scheduler.jupyter.org/job-id']}")

    # Check annotation
    if job.metadata.annotations and "scheduler.jupyter.org/job-data" in job.metadata.annotations:
        import json
        job_data = json.loads(job.metadata.annotations["scheduler.jupyter.org/job-data"])
        print(f"  job_id in annotation: {job_data.get('job_id')}")

print("\n=== K8sQuery Results ===")
session = K8sSession()

# Try querying without any filters
query1 = K8sQuery(session, None)
all_jobs = query1.all()
print(f"\nquery.all() found {len(all_jobs)} jobs")

# Try querying with the problematic filter that UI might use
query2 = K8sQuery(session, None)
# The UI might be filtering by job-id label existence
label_query = session.k8s_batch.list_namespaced_job(
    namespace="default",
    label_selector="scheduler.jupyter.org/job-id"  # Only jobs WITH this label
)
print(f"\nJobs with job-id label: {len(label_query.items)}")

# What if we query for managed-by label (should get all)
managed_query = session.k8s_batch.list_namespaced_job(
    namespace="default",
    label_selector="app.kubernetes.io/managed-by=jupyter-scheduler-k8s"
)
print(f"Jobs managed by jupyter-scheduler-k8s: {len(managed_query.items)}")