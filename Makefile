# Default target when just running 'make'
.DEFAULT_GOAL := help

CLUSTER_NAME := jupyter-scheduler-k8s
LOCAL_DEV_DIR := $(shell pwd)/local-dev
KUBECONFIG := $(LOCAL_DEV_DIR)/kind/.kubeconfig
IMAGE_NAME := jupyter-scheduler-k8s
IMAGE_TAG := latest

.PHONY: help
help:
	@echo "🚀 Jupyter Scheduler K8s - Development Commands"
	@echo "==============================================="
	@echo ""
	@echo "Setup & Environment:"
	@echo "  make setup         - Initialize Finch VM and Kind cluster"
	@echo "  make dev-env       - Build image and load into cluster (full setup)"
	@echo "  make status        - Check development environment status"
	@echo ""
	@echo "Image Management:"
	@echo "  make build-image   - Build container image"
	@echo "  make load-image    - Build and load image into Kind cluster"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean-k8s     - Delete all K8s jobs/cronjobs/pods (keep cluster)"
	@echo "  make clean         - Delete cluster and stop Finch VM"
	@echo ""
	@echo "Utilities:"
	@echo "  make kubectl-kind  - Configure kubectl for Kind cluster"
	@echo ""

.PHONY: setup
setup: setup-finch setup-kind

.PHONY: setup-finch
setup-finch:
	@echo "Checking platform and Finch VM status..."
	@if [ "$$(uname)" = "Darwin" ]; then \
		VM_STATUS=$$(finch vm status); \
		if echo "$$VM_STATUS" | grep -q "Nonexistent"; then \
			echo "Initializing new Finch VM..."; \
			finch vm init; \
			echo "Finch VM initialized and started."; \
		elif echo "$$VM_STATUS" | grep -q "Stopped"; then \
			echo "Finch VM is stopped, starting it..."; \
			finch vm start; \
			echo "Finch VM started."; \
		elif echo "$$VM_STATUS" | grep -q "Running"; then \
			echo "Finch VM is already running."; \
		else \
			echo "Unknown Finch VM status: $$VM_STATUS"; \
			exit 1; \
		fi; \
	else \
		echo "Not running on macOS, skipping Finch VM setup."; \
	fi

.PHONY: setup-kind
setup-kind:
	@echo "Setting up local Kind cluster..."
	mkdir -p $(LOCAL_DEV_DIR)/kind
	@if kind get clusters | grep -q "$(CLUSTER_NAME)"; then \
		echo "Kind cluster '$(CLUSTER_NAME)' already exists, skipping creation."; \
	else \
		echo "Creating Kind cluster '$(CLUSTER_NAME)'..."; \
		kind create cluster --name $(CLUSTER_NAME) --config=$(LOCAL_DEV_DIR)/kind/cluster.yaml --kubeconfig=$(KUBECONFIG); \
		echo "Kind cluster created with optimized disk space settings."; \
	fi
	@# Ensure kubeconfig is created even if cluster already existed
	kind get kubeconfig --name $(CLUSTER_NAME) > $(KUBECONFIG)

.PHONY: clean
clean: clean-cluster clean-finch

.PHONY: clean-k8s
clean-k8s:
	@echo "🧹 Cleaning up all jupyter-scheduler K8s resources..."
	@echo "Deleting CronJobs (job definitions)..."
	@kubectl delete cronjobs -l app.kubernetes.io/managed-by=jupyter-scheduler-k8s -n default --ignore-not-found=true
	@echo "Deleting Jobs..."
	@kubectl delete jobs -l app.kubernetes.io/managed-by=jupyter-scheduler-k8s -n default --ignore-not-found=true
	@echo "Deleting orphaned Pods..."
	@kubectl delete pods -l app.kubernetes.io/managed-by=jupyter-scheduler-k8s -n default --ignore-not-found=true
	@printf "Deleting completed Pods: "
	@kubectl delete pods --field-selector=status.phase==Succeeded -n default --ignore-not-found=true 2>&1 | grep -E "deleted|No resources found" || true
	@printf "Deleting failed Pods: "
	@kubectl delete pods --field-selector=status.phase==Failed -n default --ignore-not-found=true 2>&1 | grep -E "deleted|No resources found" || true
	@echo "✅ K8s resources cleaned up"

.PHONY: clean-cluster
clean-cluster:
	@echo "Deleting Kind cluster..."
	@if kind get clusters | grep -q "$(CLUSTER_NAME)"; then \
		echo "Attempting to delete cluster via kind..."; \
		if ! kind delete cluster --name $(CLUSTER_NAME) --quiet; then \
			echo "Kind delete failed, trying alternative cleanup method..."; \
			echo "Stopping and removing containers directly..."; \
			finch stop $(CLUSTER_NAME)-control-plane || true; \
			finch rm $(CLUSTER_NAME)-control-plane || true; \
			if [ -d "$(HOME)/.kube/kind" ]; then \
				echo "Cleaning up kind configuration..."; \
				rm -rf "$(HOME)/.kube/kind/$(CLUSTER_NAME)" || true; \
			fi; \
			echo "Kind cluster manually cleaned up."; \
		else \
			echo "Kind cluster deleted successfully."; \
		fi; \
	else \
		echo "Kind cluster '$(CLUSTER_NAME)' does not exist. Nothing to do."; \
	fi

.PHONY: clean-finch
clean-finch:
	@echo "Checking platform and Finch VM status..."
	@if [ "$$(uname)" = "Darwin" ]; then \
		VM_STATUS=$$(finch vm status); \
		if echo "$$VM_STATUS" | grep -q "Running"; then \
			echo "Finch VM is running, stopping it..."; \
			finch vm stop; \
			echo "Finch VM stopped."; \
		elif echo "$$VM_STATUS" | grep -q "Stopped"; then \
			echo "Finch VM is already stopped."; \
		elif echo "$$VM_STATUS" | grep -q "Nonexistent"; then \
			echo "Finch VM doesn't exist, nothing to clean up."; \
		else \
			echo "Unknown Finch VM status: $$VM_STATUS"; \
			exit 1; \
		fi; \
	else \
		echo "Not running on macOS, skipping Finch VM cleanup."; \
	fi

.PHONY: kubectl-kind
kubectl-kind:
	@echo "Setting up kubectl to use Kind cluster..."
	@if [ -f "$(KUBECONFIG)" ]; then \
		{ \
			echo "Adding KinD context to kubectl config..."; \
			mkdir -p ~/.kube; \
			touch ~/.kube/config; \
			KUBECONFIG="$(HOME)/.kube/config:$(KUBECONFIG)" kubectl config view --flatten > "$(HOME)/.kube/merged_config"; \
			mv "$(HOME)/.kube/merged_config" "$(HOME)/.kube/config"; \
			echo "Switching to kind-$(CLUSTER_NAME) context..."; \
			kubectl config use-context kind-$(CLUSTER_NAME); \
			echo "✅ kubectl configured to use Kind cluster. Current context: $$(kubectl config current-context)"; \
		}; \
	else \
		echo "❌ KUBECONFIG file not found at $(KUBECONFIG). Try running 'make setup' first."; \
		exit 1; \
	fi
	@echo "\nTest your connection with: kubectl get nodes"

.PHONY: build-image
build-image:
	@echo "Building Docker image $(IMAGE_NAME):$(IMAGE_TAG)..."
	finch build -t $(IMAGE_NAME):$(IMAGE_TAG) ./image
	@echo "✅ Image $(IMAGE_NAME):$(IMAGE_TAG) built successfully"

.PHONY: load-image
load-image: build-image
	@echo "Loading image into Kind cluster..."
	@echo "Exporting image to tar..."
	@finch save $(IMAGE_NAME):$(IMAGE_TAG) -o ./$(IMAGE_NAME).tar
	@echo "Loading tar into Kind..."
	@KIND_EXPERIMENTAL_PROVIDER=finch kind load image-archive ./$(IMAGE_NAME).tar --name $(CLUSTER_NAME)
	@echo "Cleaning up tar file..."
	@rm -f ./$(IMAGE_NAME).tar
	@echo "✅ Image loaded into Kind cluster"

.PHONY: dev-env
dev-env: load-image
	@echo "Setting kubectl context for Kind cluster..."
	@kind export kubeconfig --name $(CLUSTER_NAME)
	@echo ""
	@echo "🚀 Development environment ready!"
	@echo ""
	@echo "Run this to configure your shell:"
	@echo "export K8S_IMAGE=\"$(IMAGE_NAME):$(IMAGE_TAG)\""
	@echo "export S3_BUCKET=\"<your-bucket-name>\""
	@echo ""
	@echo "Then start jupyter-scheduler:"
	@echo "jupyter lab --Scheduler.execution_manager_class=\"jupyter_scheduler_k8s.K8sExecutionManager\""

.PHONY: status
status:
	@echo "📊 Development Environment Status"
	@echo "================================="
	@echo ""
	@echo "Finch VM:"
	@if [ "$$(uname)" = "Darwin" ]; then \
		if command -v finch >/dev/null 2>&1; then \
			VM_STATUS=$$(finch vm status 2>/dev/null || echo "Unknown"); \
			if echo "$$VM_STATUS" | grep -q "Running"; then \
				echo "✅ Running"; \
			elif echo "$$VM_STATUS" | grep -q "Stopped"; then \
				echo "⚠️  Stopped (run 'finch vm start')"; \
			else \
				echo "❌ $$VM_STATUS"; \
			fi; \
		else \
			echo "❌ Finch not installed"; \
		fi; \
	else \
		echo "⚠️  Not on macOS, Finch VM not applicable"; \
	fi
	@echo ""
	@echo "Kind cluster '$(CLUSTER_NAME)':"
	@if kind get clusters 2>/dev/null | grep -q "$(CLUSTER_NAME)"; then \
		echo "✅ Cluster exists"; \
		echo "Nodes:"; \
		kubectl get nodes --kubeconfig=$(KUBECONFIG) 2>/dev/null || echo "⚠️  Cannot connect to cluster"; \
	else \
		echo "❌ Cluster does not exist (run 'make setup')"; \
	fi
	@echo ""
	@echo "Container image '$(IMAGE_NAME):$(IMAGE_TAG)':"
	@if finch images | grep -q "$(IMAGE_NAME).*$(IMAGE_TAG)"; then \
		echo "✅ Image exists locally"; \
	else \
		echo "❌ Image not found (run 'make build-image')"; \
	fi
	@echo ""
	@echo "Image in Kind cluster:"
	@if kind get clusters 2>/dev/null | grep -q "$(CLUSTER_NAME)"; then \
		if finch exec $(CLUSTER_NAME)-control-plane crictl images 2>/dev/null | grep -q "$(IMAGE_NAME).*$(IMAGE_TAG)"; then \
			echo "✅ Image loaded in Kind cluster"; \
		else \
			echo "⚠️  Image not in Kind cluster (run 'make load-image')"; \
		fi \
	else \
		echo "⚠️  Cluster not running"; \
	fi
	@echo ""
	@echo "S3 Storage:"
	@if command -v aws >/dev/null 2>&1; then \
		echo "✅ AWS CLI installed"; \
		if [ -n "$$S3_BUCKET" ]; then \
			if aws s3 ls "s3://$$S3_BUCKET" >/dev/null 2>&1; then \
				echo "✅ S3 bucket '$$S3_BUCKET' accessible"; \
			else \
				echo "❌ S3 bucket '$$S3_BUCKET' not accessible (check AWS auth)"; \
			fi; \
		else \
			echo "⚠️  S3_BUCKET environment variable not set"; \
		fi; \
	else \
		echo "❌ AWS CLI not installed"; \
	fi
	@echo ""
	@echo "AWS Credentials (for container access):"
	@if [ -n "$$AWS_ACCESS_KEY_ID" ]; then \
		echo "✅ AWS_ACCESS_KEY_ID set"; \
	else \
		echo "❌ AWS_ACCESS_KEY_ID not set (required for container S3 access)"; \
	fi
	@if [ -n "$$AWS_SECRET_ACCESS_KEY" ]; then \
		echo "✅ AWS_SECRET_ACCESS_KEY set"; \
	else \
		echo "❌ AWS_SECRET_ACCESS_KEY not set (required for container S3 access)"; \
	fi
	@if [ -n "$$AWS_SESSION_TOKEN" ]; then \
		echo "✅ AWS_SESSION_TOKEN set (temporary credentials)"; \
	else \
		echo "ℹ️  AWS_SESSION_TOKEN not set (not required for permanent credentials)"; \
	fi