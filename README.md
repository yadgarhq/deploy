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

## Blocked on the container runtime (2026-08-29)

`make up` does not currently complete on this machine. Recorded here rather than
in a commit message, because the next person to try it will hit it in the first
minute.

**k3d's tools node hardcodes a bind mount of `/var/run/docker.sock`**, which
rootless podman has neither got nor can create:

```
failed to create container 'k3d-yadgar-tools': make cli opts():
  making volume mountpoint for volume /var/run/docker.sock:
  mkdir /var/run/docker.sock: permission denied
```

Every cluster node — server, both agents, the load balancer — creates fine.
Only the tools node fails, and it fails the whole create.

The declarative fix is tracked in the nix repo. Three candidates:
`virtualisation.podman.dockerSocket.enable` (rootful podman socket at
`/run/docker.sock`), real Docker, or **kind**, which supports rootless podman
natively and which D55 already permits — it says "k3d *or an equivalent local
k8s*", so a switch would be a choice rather than a workaround.

**A second podman constraint is already worked around**, noted so nobody undoes
it: k3d attaches its managed registry to a network literally named `bridge`,
which podman refuses to create because `bridge` is a reserved network *mode*
name. `registries.create` is therefore absent from the cluster config and image
delivery is `k3d image import`.

Verified on k3d 5.9.0 / podman 5.8.4, with cgroup v2 and `cpu io memory pids`
all delegated — the prerequisites are fine, the socket path is not.

## Bootstrap

```bash
make up         # k3d cluster: 1 server, 2 agents  (see blocker above)
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
