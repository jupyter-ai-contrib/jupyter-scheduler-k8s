#!/usr/bin/env python3
"""Test K8s-native job definitions."""

import os
import uuid
from datetime import datetime

from jupyter_scheduler.models import CreateJobDefinition, UpdateJobDefinition, ListJobDefinitionsQuery, CreateJobFromDefinition
from jupyter_scheduler.environments import CondaEnvironmentManager
from jupyter_scheduler_k8s.scheduler import K8sScheduler
from jupyter_scheduler_k8s.database_manager import K8sDatabaseManager
from jupyter_scheduler_k8s.executors import K8sExecutionManager


def test_job_definitions():
    """Test creating, listing, and triggering job definitions."""

    # Setup environment
    os.environ['K8S_NAMESPACE'] = 'default'
    os.environ['K8S_IMAGE'] = 'jupyter-scheduler-k8s:latest'

    # Check for S3 bucket
    if not os.environ.get('S3_BUCKET'):
        print("⚠️  S3_BUCKET not set. Using test-bucket")
        os.environ['S3_BUCKET'] = 'test-bucket'

    # Create instances
    db_manager = K8sDatabaseManager()
    env_manager = CondaEnvironmentManager()
    env_manager.root_dir = '/tmp'

    scheduler = K8sScheduler(
        staging_path='/tmp/staging',
        db_url='k8s://default',
        database_manager=db_manager,
        database_manager_class=K8sDatabaseManager,
        root_dir='/tmp',
        environments_manager=env_manager
    )

    print("=" * 60)
    print("TESTING K8S-NATIVE JOB DEFINITIONS")
    print("=" * 60)

    # Test 1: Create a job definition
    print("\n1. Creating job definition...")

    # Create a simple test notebook file
    test_notebook = "/tmp/test_notebook.ipynb"
    with open(test_notebook, 'w') as f:
        f.write("""{
 "cells": [
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "print('Hello from scheduled job!')"
   ]
  }
 ],
 "metadata": {},
 "nbformat": 4,
 "nbformat_minor": 4
}""")

    job_def_model = CreateJobDefinition(
        name="test-scheduled-job",
        input_uri=test_notebook,
        input_filename="test_notebook.ipynb",
        schedule="0 * * * *",  # Every hour
        timezone="UTC",
        output_formats=["ipynb", "html"],
        parameters={},
        tags=["test"],
        runtime_environment_name="default",
        runtime_environment_parameters={"k8s_cpu": "1", "k8s_memory": "1Gi"}
    )

    try:
        job_def_id = scheduler.create_job_definition(job_def_model)
        print(f"✅ Created job definition: {job_def_id}")
    except Exception as e:
        print(f"❌ Failed to create job definition: {e}")
        return

    # Test 2: List job definitions
    print("\n2. Listing job definitions...")
    try:
        query = ListJobDefinitionsQuery()
        response = scheduler.list_job_definitions(query)
        print(f"✅ Found {response.total_count} job definitions")
        for job_def in response.job_definitions:
            print(f"   - {job_def.name} (ID: {job_def.job_definition_id})")
    except Exception as e:
        print(f"❌ Failed to list job definitions: {e}")

    # Test 3: Get specific job definition
    print("\n3. Getting job definition...")
    try:
        job_def = scheduler.get_job_definition(job_def_id)
        if job_def:
            print(f"✅ Retrieved job definition: {job_def.name}")
            print(f"   Schedule: {job_def.schedule} {job_def.timezone}")
            print(f"   Active: {job_def.active}")
        else:
            print("❌ Job definition not found")
    except Exception as e:
        print(f"❌ Failed to get job definition: {e}")

    # Test 4: Update job definition
    print("\n4. Updating job definition...")
    try:
        update_model = UpdateJobDefinition(
            schedule="*/30 * * * *",  # Every 30 minutes
            active=True,
            name="test-scheduled-job-updated"
        )
        scheduler.update_job_definition(job_def_id, update_model)
        print("✅ Updated job definition")
    except Exception as e:
        print(f"❌ Failed to update job definition: {e}")

    # Test 5: Manually trigger job from definition
    print("\n5. Manually triggering job from definition...")
    try:
        trigger_model = CreateJobFromDefinition(
            parameters={"test_param": "manual_trigger"}
        )
        job_id = scheduler.create_job_from_definition(job_def_id, trigger_model)
        print(f"✅ Created job from definition: {job_id}")
    except Exception as e:
        print(f"❌ Failed to create job from definition: {e}")

    # Test 6: Delete job definition
    print("\n6. Deleting job definition...")
    try:
        scheduler.delete_job_definition(job_def_id)
        print("✅ Deleted job definition")
    except Exception as e:
        print(f"❌ Failed to delete job definition: {e}")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    test_job_definitions()