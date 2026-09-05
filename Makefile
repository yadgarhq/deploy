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

.PHONY: bootstrap secrets status ui password sync

# THE TWO SECRETS GITOPS CANNOT CARRY, loaded before anything syncs.
#
# Both are DATA-BEARING and neither is auto-generated: `yadgar-dev-ca` is the
# development root (ADR-0518), and `iam-keys` is the pair `iam` encrypts identity
# with — lose it and every stored name is unreadable (ledger 452). They cannot
# arrive through git because this repository is PUBLIC, and they cannot be
# generated on demand because a fresh key decrypts nothing that came before.
#
# SO THEY ARE THE ONE THING A CLUSTER RECREATE USED TO NEED A HUMAN TO REMEMBER,
# and forgetting was invisible in different ways for each. The CA announces
# itself: `infra/tls/preflight.yaml` refuses, names the Secret and prints this
# command. `iam-keys` does not — the pods sit in `ContainerCreating` with the
# reason only in `kubectl describe`, and Argo reports the Application Healthy
# throughout. Both were missing after the 2026-09-05 recreate.
#
# IT RUNS BEFORE `bootstrap` INSTALLS ANYTHING, as a prerequisite rather than a
# step, so a missing 1Password item fails while the cluster is still empty
# rather than half-built.
#
# Idempotent: every write is `--dry-run=client | kubectl apply`, so re-running on
# a cluster that already holds them is a no-op rather than an error. The two
# namespaces are created here because the Secrets need somewhere to live before
# the Applications that own those namespaces have synced; Argo adopts them.
secrets: ## Load yadgar-dev-ca and iam-keys from 1Password. Idempotent.
	@command -v op >/dev/null || { 		echo "The 1Password CLI (op) is not on PATH."; 		echo "Both secrets are data-bearing and live only there — see"; 		echo "MIGRATION_NOTES.md, 'The identity encryption keys' and"; 		echo "'The development TLS edge'."; 		exit 1; }
	@op account list >/dev/null 2>&1 || { 		echo "The 1Password CLI is not signed in. Run 'op signin' first."; 		exit 1; }
	kubectl create namespace cert-manager \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl create namespace yadgar \
		--dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret tls yadgar-dev-ca \
		--namespace cert-manager \
		--cert <(op read "op://Private/yadgar-dev-ca/certificate") \
		--key  <(op read "op://Private/yadgar-dev-ca/private key") \
		--dry-run=client -o yaml | kubectl apply -f -
	@set -euo pipefail; \
	 d=$$(mktemp -d); trap 'shred -u "$$d"/*.key 2>/dev/null || true; rmdir "$$d"' EXIT; \
	 ( umask 077; \
	   op document get "yadgar iam — encryption key"  --out-file "$$d/encryption.key"; \
	   op document get "yadgar iam — blind index key" --out-file "$$d/blind-index.key" ); \
	 kubectl create secret generic iam-keys \
		--namespace yadgar \
		--from-file=encryption.key="$$d/encryption.key" \
		--from-file=blind-index.key="$$d/blind-index.key" \
		--dry-run=client -o yaml | kubectl apply -f -
	@echo "--- yadgar-dev-ca and iam-keys are loaded. ---"

# Argo CD is installed by hand exactly once, because a GitOps controller cannot
# arrive by GitOps. Everything after this is `git push`.
bootstrap: secrets ## Install Argo CD into the running cluster, then hand control to git.
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
