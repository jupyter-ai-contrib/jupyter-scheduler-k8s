CLUSTER_NAME := jupyter-scheduler-k8s
LOCAL_DEV_DIR := $(shell pwd)/local-dev
KUBECONFIG := $(LOCAL_DEV_DIR)/kind/.kubeconfig
IMAGE_NAME := jupyter-scheduler-k8s
IMAGE_TAG := latest


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