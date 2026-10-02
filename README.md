# deploy — retired

Retired 2026-10-02, per ADR-0803 ("`yadgarhq/deploy` is archived once it
owns nothing") and ADR-0828 (the `infra` retirement, M1–M5). This
repository declares no Argo CD `Application` any more — `infra/` is gone —
and the Makefile that loaded secrets and bootstrapped Argo CD by hand moved
out from under it too. The table below says where each former
responsibility lives now.

| Used to be here                                                                                                                                                                       | Now lives in                                                                                                                       |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| The `infra` Application and the five it released (`arc`, `estate-front`, `estate-front-runner`, `tls`, `yadgar`), plus the six operator Applications                                  | [`yadgarhq/argocd`](https://github.com/yadgarhq/argocd), under `applications/` and `root`                                          |
| `make secrets` / `make bootstrap`, the data-bearing secrets GitOps cannot carry, and the one-time Argo CD install                                                                     | [`yadgarhq/argocd`](https://github.com/yadgarhq/argocd)'s own `Makefile` ([argocd#59](https://github.com/yadgarhq/argocd/pull/59)) |
| The whole-estate parent chart an adopter installs                                                                                                                                     | [`yadgarhq/chart`](https://github.com/yadgarhq/chart)                                                                              |
| The platform layer this repository used to declare by hand — the internal CA and the leaves it issues, the databases, the broker, the cache, the Gateway listener, the bootstrap Jobs | [`yadgarhq/platform`](https://github.com/yadgarhq/platform)                                                                        |
| Decisions (D-numbers, ADRs)                                                                                                                                                           | [`yadgarhq/docs`](https://github.com/yadgarhq/docs)                                                                                |
| The kind cluster itself                                                                                                                                                               | the `nix` repository (`modules/nixos/kind.nix`) — unchanged; it was never declared here                                            |

`MIGRATION_NOTES.md` and the `Makefile` stay in this repository as the
historical record of the commands it actually ran, but they are frozen:
`yadgarhq/argocd` carries the live, maintained copy of `make secrets` and
`make bootstrap` (`argocd#59`). Point any future change at `argocd`, not
here.

This repository is archived, read-only, once this merges.
