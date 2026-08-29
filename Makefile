# THE CLUSTER IS NOT CREATED FROM HERE. Its lifecycle belongs to the nix repo
# (modules/nixos/kind.nix), which owns the machine and the cluster itself. This
# repo owns only what runs INSIDE the cluster.
#
# One writer, deliberately. A cluster created from here plus a nix-managed one
# are two writers of a single resource, and the failure mode is not an error but
# a half-built cluster — which is exactly what the earlier k3d attempt produced,
# a load balancer crash-looping on a config file that had not finished being
# written.
#
#   kubectl get nodes               # yadgar-control-plane, yadgar-worker, worker2
#   kubectl config current-context  # kind-yadgar
#
# kind with the podman provider, on the existing ROOTLESS podman session. No
# DOCKER_HOST, no docker socket, no rootful bridge — see README for why k3d was
# abandoned.
# /bin/bash does not exist on NixOS — /bin holds only sh. env resolves bash
# from PATH instead of assuming a filesystem layout.
SHELL := /usr/bin/env bash
ARGOCD_CHART_VERSION := 8.6.1
# Only for the one-time handover apply. Everything after arrives through git.
ARGOCD_REPO ?= ../argocd

.PHONY: bootstrap status ui password sync

# Argo CD is installed by hand exactly once, because a GitOps controller cannot
# arrive by GitOps. Everything after this is `git push`.
bootstrap: ## Install Argo CD into the running cluster, then hand control to git.
	@test -n "$$GITHUB_TOKEN" || { \
		echo "GITHUB_TOKEN is unset."; \
		echo "The D54 ApplicationSet enumerates the organisation through the"; \
		echo "GitHub API; unauthenticated is rate-limited hard enough to look"; \
		echo "like a broken generator. A read-only repo-scope token is enough."; \
		exit 1; }
	helm repo add argo https://argoproj.github.io/argo-helm >/dev/null
	helm repo update >/dev/null
	helm upgrade --install argocd argo/argo-cd \
		--version $(ARGOCD_CHART_VERSION) \
		--namespace argocd --create-namespace \
		--set configs.params."server\.insecure"=true \
		--set dex.enabled=false \
		--set notifications.enabled=false \
		--wait
	kubectl -n argocd create secret generic github-scm \
		--from-literal=token="$$GITHUB_TOKEN" \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "--- Argo CD up. Handing control to git. ---"
	kubectl apply -f $(ARGOCD_REPO)/projects/root.yaml
	kubectl apply -f infra/apps.yaml
	@echo "Argo now manages its own values from yadgarhq/argocd. make ui / make password."

## The initial install uses --set, not the values file in yadgarhq/argocd. That
## file is applied one sync later by the root Application, which is the point of
## Argo managing Argo: reaching across repos with a relative path works only
## because the two happen to be siblings on one machine, and fails in CI or a
## fresh clone. Only the handful of settings needed to REACH the server are set
## here; everything else arrives through git.

status:
	@kubectl config current-context 2>/dev/null | grep -q kind-yadgar \
		&& echo "context: kind-yadgar" || echo "context: NOT kind-yadgar"
	@kubectl get applications -n argocd 2>/dev/null || echo "argocd not installed yet"
	@echo "--- nodes ---"; kubectl get nodes -o wide 2>/dev/null || echo "cluster unreachable"

## No argocd CLI anywhere in this file, deliberately. The Argo server runs in
## the cluster; the CLI is an optional client and kubectl on the CRDs does the
## same job. helm and kubectl are the whole toolchain here — kind is needed only
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
