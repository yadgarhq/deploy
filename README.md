# deploy — the substrate

The infrastructure Argo CD keeps in sync, and the Argo bootstrap. **Not the
cluster** — see below.

Argo CD's own configuration lives in
[`yadgarhq/argocd`](https://github.com/yadgarhq/argocd). Decisions are recorded in
[`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D54 (deployment layout),
D55 (development environment), D58 (databases).

## Toolchain

`helm` and `kubectl`. That is all. `kind` is needed only by the nix unit that
creates the cluster, and there is no `argocd` CLI dependency anywhere here — the
Argo server runs in the cluster and `kubectl` on its CRDs does the same job.

## The cluster is not created from this repo

Its lifecycle belongs to the **nix repo** — `modules/nixos/kind.nix`, with the
cluster definition inline there rather than in a file this repo owns. This repo
owns only what runs **inside** the cluster.

**One writer, and that is the point.** A cluster created from here plus a
nix-managed one are two writers of a single resource, and the failure mode is not
an error but a half-built cluster. That already happened once during the k3d
attempt: a `systemctl restart podman.socket` landed between the load balancer
container being created and its config being written, leaving it crash-looping on
`stat /etc/confd/values.yaml: no such file or directory` with port 6443
unreachable, so `kubectl` could not reach the cluster at all.

```bash
kubectl get nodes                 # yadgar-control-plane, yadgar-worker, yadgar-worker2
kubectl config current-context    # kind-yadgar
```

## What it is

**kind with the podman provider** (`KIND_EXPERIMENTAL_PROVIDER=podman`), running
on the existing **rootless** podman session.

| | |
|---|---|
| Kubernetes | **v1.36.1, upstream** — kubeadm, containerd 2.3.1. Not k3s. |
| Nodes | `yadgar-control-plane`, `yadgar-worker`, `yadgar-worker2` |
| Ports | 18080→80, 18443→443, on the control-plane node (kind has no separate load balancer node) |
| kubeconfig | `~/.kube/config`, written directly as `max`. No copy or chown. |
| Ingress | **none bundled.** kind ships no ingress controller, so Argo CD's ingress story is a clean slate. |

**Three nodes, deliberately.** A single-node cluster schedules every replica onto
one kubelet, which reproduces the single-process blind spot D55 exists to
remove — D18's cache coherence, D23's client-side balancing and D47's atomic
claim are all multi-replica behaviours.

## Image delivery

```bash
kind load docker-image <image> --name yadgar
```

There is no in-cluster registry, by choice. D55's dev loop — one module's tag
overridden while everything else stays GitOps — works the same either way.

## Why not k3d

Recorded because it cost a day and the failure is not obvious.

k3d on this host died in two stages. First, its tools node hardcodes a bind mount
of `/var/run/docker.sock`, which rootless podman has not got and cannot create.
Pointing k3d at a *rootful* podman socket cleared that — and was **necessary but
not sufficient**. Underneath sat a rootful-podman bridge networking failure:
agents could never register, because containers on that bridge could not reach
even their own gateway (100% packet loss to `10.89.0.1` from inside a node, while
the host queried it fine). That is below the firewall layer and was not worth
digging further.

kind shells out to `podman` directly and uses the existing rootless session, so
none of that class of problem exists — no docker socket, no rootful bridge, no
network that has to be named `bridge`. Full detail in the yadgar wiki,
`k3d-rootless-podman-blockers`.

## Bootstrap

```bash
make bootstrap   # Argo CD into the running cluster, then git owns everything
```

Argo CD is installed by hand exactly once, because a GitOps controller cannot
arrive by GitOps. Everything after that is `git push`.

## What is here

| Path | |
|---|---|
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

Ordering is what an umbrella chart would have given for free, and paying for it
here is the accepted cost of D54.

## Known risk

The Valkey chart is Bitnami's, and Bitnami restricted free image distribution in
2025 — the chart resolves, but its images may not stay pullable. The fallback is
the official Valkey image with a hand-written manifest. O10's standing
instruction to re-verify before depending on a dependency applies.
