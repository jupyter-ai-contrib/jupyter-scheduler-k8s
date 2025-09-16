#!/usr/bin/env python3
"""Test how K8sQuery finds jobs"""

from jupyter_scheduler_k8s.k8s_orm import K8sSession, K8sQuery
from jupyter_scheduler.models import ListJobsQuery

# Test 1: Raw K8sQuery
print("=== Test 1: Raw K8sQuery.all() ===")
session = K8sSession()
query = K8sQuery(session, None)
all_jobs = query.all()

print(f"Found {len(all_jobs)} jobs via query.all()")
for job in all_jobs:
    print(f"  - job_id: {job.job_id}, name: {job.name}")

# Test 2: Query by job_definition_id
print("\n=== Test 2: Query by job_definition_id ===")
query2 = K8sQuery(session, None)
query2.filter_by(job_definition_id="0bd9a6f1-6b91-41d5-909e-4d18e21977b3")
def_jobs = query2.all()
print(f"Found {len(def_jobs)} jobs for this definition")

# Test 3: Simulate what scheduler.list_jobs does
print("\n=== Test 3: What scheduler.list_jobs might do ===")
from jupyter_scheduler_k8s.scheduler import K8sScheduler
import tempfile
from jupyter_scheduler.environments import DefaultEnvironmentManager

class SimpleEnvManager(DefaultEnvironmentManager):
    def list_environments(self):
        return []
    def output_formats_mapping(self):
        return {}
    def manage_environments_command(self):
        return ""

with tempfile.TemporaryDirectory() as tmpdir:
    scheduler = K8sScheduler(
        root_dir=tmpdir,
        environments_manager=SimpleEnvManager(),
        db_url="sqlite:///:memory:"
    )

    list_query = ListJobsQuery()
    result = scheduler.list_jobs(list_query)
    print(f"scheduler.list_jobs found {len(result.jobs)} jobs")
    for job in result.jobs:
        print(f"  - job_id: {job.job_id}, name: {job.name}")