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

## Namespaces

Everything yadgar deploys lands in **`yadgar`** — its own services via the D54
ApplicationSet, and the infrastructure they depend on. Two things sit outside it,
and neither is a configuration choice:

- **CRDs.** `CustomResourceDefinition` is cluster-scoped and has no namespace at
  all, so "everything in the `yadgar` namespace" cannot be literally true while
  an operator is involved.
- **`local-path-provisioner`**, in `local-path-storage`. It ships with kind to
  provide a default StorageClass. It is not ours, and in a real deployment a
  proper CSI driver takes its place.

The `mariadb-operator` is scoped with `currentNamespaceOnly`. It defaults to
watching every namespace with cluster-wide RBAC to match, and moving it into the
application namespace without changing that would concentrate cluster-wide
privilege inside `yadgar` — worse than where it started.

## Where the third-party workloads come from

None of these is yadgar code, which is why no repository here corresponds to
them. Argo installs them from upstream charts.

| Workload | Chart | Source | What it is |
|---|---|---|---|
| `mariadb-operator` | `mariadb-operator` | helm.mariadb.com | Reconciles `MariaDB` resources into real instances — what makes D58's per-module databases declarative |
| `mariadb-operator-webhook` | same | same | Admission webhook validating `MariaDB` resources |
| `mariadb-operator-cert-controller` | same | same | Issues and rotates the webhook's certs, so cert-manager is not a dependency |
| `nats-0` | `nats` | nats-io.github.io | The JetStream server (D22) |
| `valkey-primary` + replicas | `valkey` | Bitnami | The one shared cache (D21) |
| `local-path-provisioner` | — | **kind itself** | Default StorageClass. Not deployed by this repo. |

`nats-box` is disabled. It is a shell with the NATS CLI in it — a debugging
convenience rather than part of the system, and `kubectl run` covers the same
need on the rare occasion it arises.

## What is here

| Path | |
|---|---|
| `infra/apps.yaml` | app-of-apps entry point for everything that is not Argo itself |
| `infra/mariadb-operator-crds.yaml` | the operator's CRDs, a separate chart at an earlier wave — without them the operator crash-loops |
| `infra/mariadb-operator.yaml` | per-module database instances, credentials and backups (D58) |
| `infra/valkey-app.yaml` + `infra/valkey/` | one shared cache (D21), hand-written rather than a chart |
| `infra/nats.yaml` | JetStream, asynchronous work only (D22) |

## Sync waves

| Wave | |
|---|---|
| -20 | Argo's own root |
| -15 | this app-of-apps |
| -12 | CRDs — they must exist before the controller that watches them |
| -10 | operators, cache, broker |
| 10 | module services, via the ApplicationSet |

Ordering is what an umbrella chart would have given for free, and paying for it
here is the accepted cost of D54.

## The Bitnami risk, resolved

Recorded because it was flagged as a theoretical risk and turned out to be a
live defect.

The Valkey chart was Bitnami's. Bitnami restricted free image distribution in
2025, and the consequence is concrete — verified 2026-08-30 with `skopeo`:

```
bitnami/valkey:9.1.1   NOT PULLABLE
bitnami/valkey:8.1     NOT PULLABLE
bitnami/valkey:latest  the only tag that resolves
```

So the chart pinned at 6.2.13 was running `:latest`. **Pinning was not possible
there, only unpinning** — a rebuild months later silently gets a different
Valkey. Overriding the image to the official one does not work either, because
the chart's entrypoint expects Bitnami's `/opt/bitnami` layout.

Replaced with hand-written manifests on the official `valkey/valkey:9.1.1`,
which pins cleanly. D21 asks for one shared deployment, so the chart was buying
configuration nobody needed at the cost of a supply chain nobody controls.

**No persistence and no replicas, deliberately.** Nothing in this cache survives
loss because nothing needs to: compute caches are content-addressed (D17), row
caches key on D8's version and query caches on an epoch, and D29 already
requires an evicted conversation token to degrade to a new conversation and say
so. Losing it costs a cold start, not data.

## Known risk

`mariadb-operator` runs with cluster-wide RBAC. `currentNamespaceOnly: true`
would narrow it to a `Role`, but it also renders the chart down to five
resources and silently drops the admission webhook — so a malformed `MariaDB`
resource would be accepted and fail later in the operator's logs instead of
being rejected at apply time. With ~19 such resources central to D58, and D27
making one deployment one organisation, validation is worth more than the
narrower role. Revisit if the deployment ever hosts anything else.
