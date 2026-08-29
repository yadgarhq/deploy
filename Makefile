# THE CLUSTER IS NOT CREATED FROM HERE. Its lifecycle belongs to the nix repo
# (modules/nixos/k3d.nix, systemd unit k3d-yadgar-cluster), which owns the
# machine, the container runtime and the cluster itself. This repo owns only
# what runs INSIDE the cluster.
#
# One writer, deliberately. A `k3d cluster create` from here plus a systemd unit
# that also creates it are two writers of one resource, and the failure mode is
# not an error but a half-built cluster — which is exactly how the first attempt
# left a load balancer crash-looping on a config file k3d had not finished
# writing.
#
#   check the unit:  systemctl status k3d-yadgar-cluster
#   full reset:      k3d cluster delete yadgar
#                    sudo systemctl restart k3d-yadgar-cluster
#
# DOCKER_HOST stays unset: k3d's default /var/run/docker.sock resolves through
# the podman docker-compat symlink to rootful podman.
SHELL := /bin/bash
ARGOCD_CHART_VERSION := 8.6.1

.PHONY: bootstrap status ui password sync

# Argo CD is installed by hand exactly once, because a GitOps controller cannot
# arrive by GitOps. Everything after this is `git push`.
bootstrap: ## Install Argo CD into the running cluster, then hand control to git.
	helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
	helm repo update >/dev/null
	helm upgrade --install argocd argo/argo-cd \
		--version $(ARGOCD_CHART_VERSION) \
		--namespace argocd --create-namespace \
		--values ../argocd/install/values.yaml \
		--wait
	@echo "--- Argo CD up. Handing control to git. ---"
	kubectl apply -f ../argocd/projects/root.yaml
	kubectl apply -f infra/apps.yaml

status:
	@systemctl is-active k3d-yadgar-cluster >/dev/null 2>&1 \
		&& echo "cluster unit: active" || echo "cluster unit: NOT active"
	@kubectl get applications -n argocd 2>/dev/null || echo "argocd not installed yet"
	@echo "--- nodes ---"; kubectl get nodes -o wide 2>/dev/null || echo "cluster unreachable"

## No argocd CLI anywhere in this file, deliberately. The Argo server runs in
## the cluster; the CLI is an optional client and kubectl on the CRDs does the
## same job. helm and kubectl are the whole toolchain here — k3d is needed only
## by the nix unit that creates the cluster.

ui: ## http://localhost:8081 — admin / `make password`
	kubectl -n argocd port-forward svc/argocd-server 8081:80

password:
	@kubectl -n argocd get secret argocd-initial-admin-secret \
		-o jsonpath='{.data.password}' | base64 -d; echo

sync: ## Force a refresh without waiting for the reconciliation interval.
	@test -n "$(APP)" || (echo "usage: make sync APP=<application-name>"; exit 1)
	kubectl -n argocd annotate application $(APP) \
		argocd.argoproj.io/refresh=hard --overwrite
