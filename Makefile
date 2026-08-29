# Bootstrap is deliberately two phases. Phase 1 is imperative and run once:
# a cluster and a GitOps controller cannot themselves arrive by GitOps. Phase 2
# is everything else, and it arrives the same way production does (D54).
SHELL := /bin/bash
CLUSTER := yadgar
ARGOCD_CHART_VERSION := 8.6.1

# k3d speaks the Docker API. On this machine that is podman, so point it at the
# socket. Rootless works for `cluster list` but NOT for `cluster create` — see
# the blocker in README.md; the declarative fix is tracked in the nix repo.
export DOCKER_HOST ?= unix:///run/user/$(shell id -u)/podman/podman.sock

.PHONY: up down bootstrap status ui password sync clean

## Phase 1 — imperative, once.
up:
	k3d cluster create --config k3d/cluster.yaml
	kubectl wait --for=condition=Ready nodes --all --timeout=180s

bootstrap: ## Install Argo CD, then hand control to git.
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

## Phase 2 and onward is `git push`.

status:
	@kubectl get applications -n argocd 2>/dev/null || echo "argocd not installed yet"
	@echo "--- nodes ---"; kubectl get nodes -o wide

## No argocd CLI anywhere in this file, deliberately. The Argo server runs in
## the cluster; the CLI is an optional client and kubectl on the CRDs does the
## same job. k3d, helm and kubectl are the whole toolchain.

ui: ## http://localhost:8081 — admin / `make password`
	kubectl -n argocd port-forward svc/argocd-server 8081:80

password:
	@kubectl -n argocd get secret argocd-initial-admin-secret \
		-o jsonpath='{.data.password}' | base64 -d; echo

sync: ## Force a refresh without waiting for the reconciliation interval.
	@test -n "$(APP)" || (echo "usage: make sync APP=<application-name>"; exit 1)
	kubectl -n argocd annotate application $(APP) \
		argocd.argoproj.io/refresh=hard --overwrite

down:
	k3d cluster delete $(CLUSTER)

clean: down
	k3d registry delete yadgar-registry 2>/dev/null || true
