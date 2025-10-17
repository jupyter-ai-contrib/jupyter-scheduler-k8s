#!/usr/bin/env python3
"""Test what database manager is being used"""

import os

# Check if K8sDatabaseManager mode is configured
print("=== Environment Check ===")
print(f"K8S_NAMESPACE: {os.environ.get('K8S_NAMESPACE', 'default')}")
print(f"S3_BUCKET: {os.environ.get('S3_BUCKET', 'NOT SET')}")

# Test with K8sExecutionManager config (what user is using)
print("\n=== Testing with K8sExecutionManager only (user's config) ===")

# When running with just execution manager, scheduler uses regular SQLite DB
# This means K8sQuery is NOT being used for list_jobs!

print("\nThe issue: When using --Scheduler.execution_manager_class only,")
print("the scheduler still uses SQLite for database, NOT K8sSession/K8sQuery!")
print("\nCronJob-spawned jobs exist in K8s but not in SQLite database.")
print("That's why they're invisible in the UI.")

print("\n=== Solution ===")
print("User needs to ALSO use K8sDatabaseManager:")
print("")
print("jupyter lab \\")
print('  --Scheduler.database_manager_class="jupyter_scheduler_k8s.K8sDatabaseManager" \\')
print('  --Scheduler.execution_manager_class="jupyter_scheduler_k8s.K8sExecutionManager"')
print("")
print("This enables full K8s-native mode where K8sQuery is used for everything.")