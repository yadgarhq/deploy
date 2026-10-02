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

`MIGRATION_NOTES.md` stays, as history — except the sections
`yadgarhq/argocd` still cites, which stay the live runbook a human reads
to operate the cluster (its own preamble says which). The `Makefile` it
once documented is deleted, along with the two test files that guarded it.
Three of its sections ("The identity encryption keys", "The development
TLS edge", "The `estate-front` runner") were copied, byte-faithfully, into
`yadgarhq/argocd`'s own `MIGRATION_NOTES.md` (`argocd#59`), because that
repository's `Makefile` cites them by name. `yadgarhq/argocd` carries the
one live, runnable copy of `make secrets` and `make bootstrap`; point any
future change there.

This repository is archived, read-only, once this merges.
