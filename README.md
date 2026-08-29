# deploy — the substrate

The cluster and everything that runs under the services: the k3d definition, and
the infrastructure Argo CD keeps in sync.

Argo CD's own configuration lives in
[`yadgarhq/argocd`](https://github.com/yadgarhq/argocd). Decisions are recorded in
[`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D54 (deployment layout),
D55 (development environment), D58 (databases).

## Toolchain

`k3d`, `helm`, `kubectl`. That is all — there is no `argocd` CLI dependency
anywhere in the Makefile. The Argo server runs in the cluster and `kubectl` on
its CRDs does the same job.

Nix: `pkgs.k3d`.

## Bootstrap

```bash
make up         # k3d cluster: 1 server, 2 agents, local registry on :5001
make bootstrap  # install Argo CD, then hand control to git
```

Two phases, and the split is not cosmetic: **a cluster and a GitOps controller
cannot themselves arrive by GitOps.** Phase 1 is imperative and runs once. Phase
2 onward is `git push`.

**Two agents, deliberately.** A single-node cluster schedules every replica onto
one kubelet, which reproduces the single-process blind spot D55 exists to
remove — D18's cache coherence, D23's client-side balancing and D47's atomic
claim are all multi-replica behaviours, and proving them across nodes proves
considerably more than proving them across pods on one node.

## What is here

| Path | |
|---|---|
| `k3d/cluster.yaml` | the development cluster |
| `infra/apps.yaml` | app-of-apps entry point for everything that is not Argo itself |
| `infra/mariadb-operator.yaml` | per-module database instances, credentials and backups (D58) |
| `infra/valkey.yaml` | one shared cache, one key namespace per `-db` (D21) |
| `infra/nats.yaml` | JetStream, asynchronous work only (D22) |

## Sync waves

| Wave | |
|---|---|
| -20 | Argo's own root |
| -15 | this app-of-apps |
| -10 | operators, cache, broker |
| 10 | module services, via the ApplicationSet |

Ordering is what the umbrella chart would have given for free, and paying for it
here is the accepted cost of D54.

## Known risk

The Valkey chart is Bitnami's, and Bitnami restricted free image distribution in
2025 — the chart resolves, but its images may not stay pullable. The fallback is
the official Valkey image with a hand-written manifest. O10's standing
instruction to re-verify before depending on a dependency applies.
