# deploy — the substrate

The cluster and everything that runs under the services: the k3d definition, and
the infrastructure Argo CD keeps in sync.

Argo CD's own configuration lives in
[`yadgarhq/argocd`](https://github.com/yadgarhq/argocd). Decisions are recorded in
[`yadgarhq/docs`](https://github.com/yadgarhq/docs) — D54 (deployment layout),
D55 (development environment), D58 (databases).

## Toolchain

`helm` and `kubectl`. `k3d` is needed only by the nix unit that creates the
cluster, not by anything in this repo. That is all — there is no `argocd` CLI dependency
anywhere in the Makefile. The Argo server runs in the cluster and `kubectl` on
its CRDs does the same job.

Nix: `pkgs.k3d`.

## The cluster is not created from this repo

Its lifecycle belongs to the **nix repo** — `modules/nixos/k3d.nix`, systemd unit
`k3d-yadgar-cluster`, which is idempotent and runs as `max` with the `podman`
group so the kubeconfig lands somewhere usable. This repo owns only what runs
**inside** the cluster.

**One writer, and that is the point.** A `k3d cluster create` from here plus a
systemd unit that also creates it are two writers of one resource, and the
failure mode is not an error but a half-built cluster: the first attempt left a
load balancer crash-looping on `stat /etc/confd/values.yaml: no such file or
directory`, because a `systemctl restart podman.socket` landed between the
container being created and k3d writing its config into it. Port 6443 was then
unreachable and `kubectl` could not talk to the cluster at all.

```bash
systemctl status k3d-yadgar-cluster          # is it up
k3d cluster delete yadgar && \
  sudo systemctl restart k3d-yadgar-cluster  # full reset
```

`DOCKER_HOST` stays **unset**: k3d's default `/var/run/docker.sock` resolves
through the podman docker-compat symlink to rootful podman.

**A fresh login is required after first setup.** Group membership applies at
login, so a shell started before `max` joined `podman` gets
`connect: permission denied` on the socket even though the group is correct on
disk. `sg podman -c '...'` bridges an existing session; re-login is the fix.

**No managed registry, and there is no fix for it** — only avoidance. k3d
attaches a managed registry's node to a network literally named `bridge`, which
podman refuses to create because `bridge` is a reserved network *mode* name.
Image delivery is `k3d image import`.

## Bootstrap

```bash
make bootstrap   # Argo CD into the running cluster, then git owns everything
```

Argo CD is installed once by hand because a GitOps controller cannot arrive by
GitOps. Everything after that is `git push`.

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

Ordering is what the umbrella chart would have given for free, and paying for it
here is the accepted cost of D54.

## Known risk

The Valkey chart is Bitnami's, and Bitnami restricted free image distribution in
2025 — the chart resolves, but its images may not stay pullable. The fallback is
the official Valkey image with a hand-written manifest. O10's standing
instruction to re-verify before depending on a dependency applies.
