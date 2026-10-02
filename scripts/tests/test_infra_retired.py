"""`infra` itself is retired (ADR-0828, M5 of the `infra` retirement).

MERGE M5, THE LAST OF THE `infra` RETIREMENT LADDER. M1 (`deploy#79`) guarded
`infra`'s five remaining children — `arc`, `estate-front`,
`estate-front-runner`, `tls` and `yadgar` — from `infra`'s own prune. M2
(`deploy#80`) deleted the five manifests that annotation guarded, replacing
`scripts/tests/test_no_two_owners.py` with
`test_infra_children_handed_over.py` (THIS file's predecessor), whose gate was
"no `INFRA_CHILDREN_HANDOVER` name declared under `infra/`" — `infra` itself
stayed, declaring only itself, pending a hand delete. M3 (`argocd#54`) adopted
the five live objects into `yadgarhq/argocd`'s `root` by name. M4 was the hand
delete of the live `infra` Application object itself (`kubectl --context
kind-yadgar -n argocd delete application infra`, 2026-10-01, NEEDS-MAX, no
PR — the object was never git-managed by anything after M2).

THIS MERGE DELETES WHAT `infra` LEFT BEHIND IN GIT: `infra/apps.yaml` (the
`infra` Application manifest itself — the live object is already gone by M4,
so this is deleting a manifest for an object that no longer exists, not a
decision that deletes one), `infra/tls/`, `infra/estate-front/` and
`infra/yadgar/values.yaml` (the manifests the `tls`, `estate-front` and
`yadgar` Applications sourced here — `yadgarhq/argocd` carries byte-identical
copies at `manifests/tls/`, `manifests/estate-front/` and an inlined
`valuesObject`, adopted at M3), and `scripts/policy_sources_named.py` with its
pre-commit hook (ported to `yadgarhq/argocd` at M3, which carries the gate
forward over its own `manifests/`). `make bootstrap`'s `kubectl apply -f
infra/apps.yaml` line is deleted too — otherwise a fresh `make bootstrap`
would recreate the very Application M4 deleted by hand.

THE GATE FLIPS AGAIN, from "HANDED OVER" (no `INFRA_CHILDREN_HANDOVER` name
declared under `infra/`) to "RETIRED" (`infra/` declares NO Application
manifest of ANY name, because `infra` itself is now one of the retired
names). The predecessor's `EXPECTED_INFRA_APPLICATIONS = frozenset({"infra"})`
is gone; this gate's equivalent is the empty set. A restored copy of ANY of
the six retired manifests — the five children or `infra` itself — reddens
this gate, by name rather than by filename, for the same reason the
predecessor gate did: `yadgarhq/argocd`'s `root` already declares the same six
names, and a restored copy here would give each a second owner.

`infra/` MAY NOT EXIST AT ALL AFTER THIS MERGE — `infra/apps.yaml`,
`infra/tls/`, `infra/estate-front/` and `infra/yadgar/` (now empty once
`values.yaml` leaves) are everything it held. `applications()` below does not
require the directory to exist: an absent `infra/` glob-matches nothing,
which is the same "found none" result a present-but-empty `infra/` would
give, and both are the retired state this gate accepts.

LATER: `deploy#85` deleted the Makefile `test_makefile_no_longer_applies_infra_apps_yaml`
and `test_a_restored_bootstrap_apply_line_reddens` guarded, once `make
secrets`/`make bootstrap` moved to `yadgarhq/argocd` (`argocd#59`, ADR-0803)
and this repository owned no Application to recreate via `make bootstrap`
any more. Both tests, and `makefile_applies_infra_apps` with the constants
only they used, went with it in that same PR; `_APPLY_INFRA_APPS_RE` and
its two standalone tests (`test_a_flag_value_of_apply_is_not_mistaken_for_the_subcommand`,
`test_the_gate_handles_an_adversarial_string_fast`) were kept a round
longer on the theory that the regex's matching and ReDoS-safety were worth
testing on their own — they are not: a regex with no consumer guards
nothing, so `deploy#85`'s review fixes deleted all three together. The
`infra/` half of this file — the gate that no Application manifest is
declared under `infra/` — is unaffected and still reads `infra/` directly,
not the Makefile.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]


def applications(tree: Path):
    """Every Argo `Application` manifest directly under `infra/`, if `infra/` exists at all.

    NOT `rglob`, for the reason the predecessor gate gave: `infra/<name>/` held
    the manifests an Application SOURCED, and reading one of those as an
    Application would double-count it. Moot once `infra/` is empty, kept so a
    partial revert (one of the four paths restored without the others) is
    still read correctly.

    THREE EXTENSIONS, NOT ONE — carried from the predecessor gate. Argo CD's
    directory-source matcher is `^.*\\.(yaml|yml|json|jsonnet)$`
    (`reposerver/repository/repository.go`, `findManifests` ->
    `getPotentiallyValidManifestFile`, v3.1.8, the version this estate runs),
    so `root` adopts a restored Application under `.yml` or `.json` exactly as
    it would under `.yaml`.
    """
    if not (tree / "infra").is_dir():
        return
    paths: list[Path] = []
    for pattern in ("*.yaml", "*.yml", "*.json"):
        paths += (tree / "infra").glob(pattern)
    for path in sorted(paths):
        for document in yaml.safe_load_all(path.read_text()):
            if isinstance(document, dict) and document.get("kind") == "Application":
                yield path, document


def infra_application_names(tree: Path) -> list[str]:
    """Every Application name declared directly under `infra/`, in file order."""
    return [
        str((application.get("metadata") or {}).get("name"))
        for _, application in applications(tree)
    ]


def a_copy_of_the_tree(tmp_path: Path) -> Path:
    """The repository, copied, so no red case can touch the working tree.

    `.git` is excluded because it is large and no red case reads it.
    """
    tree = tmp_path / "deploy"
    shutil.copytree(REPOSITORY, tree, ignore=shutil.ignore_patterns(".git"))
    return tree


@pytest.fixture(scope="module")
def working_tree() -> Path:
    """The repository itself. Read only; every red case copies it first."""
    return REPOSITORY


def test_infra_declares_no_application_manifest_at_all(working_tree: Path) -> None:
    """`infra/` — if it exists at all — declares no Application, not even itself."""
    names = infra_application_names(working_tree)
    print(f"[infra retire] {len(names)} Application(s) still declared under infra/: {sorted(names)}")
    assert names == [], f"infra retired at M4, still declared under infra/: {sorted(names)}"


# THE SMALLEST APPLICATION THE GATE MUST STILL NAME. Only `metadata.name` is
# read, so the red case carries nothing more than an Application needs — the
# same shape the predecessor gate and `test_no_two_owners.py` both used.
RESTORED_APPLICATION = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {name}
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/yadgarhq/deploy
    targetRevision: main
    path: infra/{name}
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
"""


@pytest.mark.parametrize("name", ["infra", "arc", "estate-front", "estate-front-runner", "tls", "yadgar"])
def test_a_restored_application_reddens(tmp_path: Path, name: str) -> None:
    """Mutation check: restore any one of the six retired manifests; the gate names it."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra").mkdir(exist_ok=True)
    (tree / "infra" / f"{name}.yaml").write_text(RESTORED_APPLICATION.format(name=name))
    assert infra_application_names(tree) == [name]


def test_a_restored_application_under_another_filename_reddens(tmp_path: Path) -> None:
    """Red case: the gate keys on the Application's name, so the filename does not hide it."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra").mkdir(exist_ok=True)
    (tree / "infra" / "not-infra-apps.yaml").write_text(RESTORED_APPLICATION.format(name="infra"))
    assert infra_application_names(tree) == ["infra"]


def test_a_restored_application_as_yml_reddens(tmp_path: Path) -> None:
    """Red case: Argo's directory source adopts `.yml` exactly as `.yaml`, so this gate must too."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra").mkdir(exist_ok=True)
    (tree / "infra" / "tls.yml").write_text(RESTORED_APPLICATION.format(name="tls"))
    assert infra_application_names(tree) == ["tls"]


def test_a_restored_application_as_json_reddens(tmp_path: Path) -> None:
    """Red case: Argo's directory source adopts `.json` exactly as `.yaml`, so this gate must too."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra").mkdir(exist_ok=True)
    application = yaml.safe_load(RESTORED_APPLICATION.format(name="yadgar"))
    (tree / "infra" / "yadgar.json").write_text(json.dumps(application))
    assert infra_application_names(tree) == ["yadgar"]
