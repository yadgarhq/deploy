"""The five children `infra/apps.yaml` declared beside itself are handed over.

MERGE 2 OF RETIRING `infra` (ADR-0824, K4 13 -> 12). Merge 1 (`deploy#79`) put
`argocd.argoproj.io/sync-options: Prune=false` on each child Application's OWN
metadata, in `scripts/tests/test_no_two_owners.py`
(`test_every_infra_child_application_carries_prune_false`). This merge deletes
the five manifests that annotation guarded — `infra/arc.yaml`,
`infra/estate-front-app.yaml`, `infra/estate-front-runner.yaml`,
`infra/tls-app.yaml` and `infra/yadgar-app.yaml` — so that `yadgarhq/argocd`'s
`root` can adopt the five live `Application` objects by name (the E2/E3
pattern of `deploy#77` and `argocd#42`). `infra` itself stays, declaring only
itself, until it is deleted by hand (plan step M4, NEEDS-MAX).

THE GATE FLIPS FROM "GUARDED" TO "HANDED OVER", exactly as E2 (`deploy#77`)
turned E1's own prune-false guard into
`test_no_operator_application_is_declared_here_after_the_handover`. Before
this merge the failure was a child Application here WITHOUT the annotation.
From this merge the failure is ANY `INFRA_CHILDREN_HANDOVER` name declared
here at all: a restored copy would make `infra` and argocd's `root` both
declare the same object, and after `argocd` adopts them the two would
overwrite each other's tracking-id on every sync. The name, not the file, is
what is read, so a copy restored under a different filename is still caught.

`test_no_two_owners.py` ITSELF LEFT IN THIS SAME MERGE, which is why this is a
new file rather than an edit in place — ADR-0824's `deploy`-side two-owners
gate had exactly two subjects, `infra/tls-app.yaml` (its D side) and
`infra/yadgar-app.yaml` (its P side, the pinned parent), and both leave here.
`yadgarhq/argocd` carries that whole gate forward, pointed at
`applications/*.yaml`; the five-children handover guard does not move there,
because `infra` and its hand delete are a `deploy`-only concept.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]

SYNC_OPTIONS = "argocd.argoproj.io/sync-options"

# THE FIVE NAMES `deploy#79` GUARDED AND THIS MERGE HANDS OVER.
INFRA_CHILDREN_HANDOVER: tuple[str, ...] = (
    "arc",
    "estate-front",
    "estate-front-runner",
    "tls",
    "yadgar",
)

# `infra/apps.yaml` DECLARES EXACTLY ONE APPLICATION NOW: ITSELF. The six
# operators left at E2, the five children leave here; nothing under `infra/`
# declares an Application but `infra` until M4 deletes that one too by hand.
EXPECTED_INFRA_APPLICATIONS = frozenset({"infra"})


def applications(tree: Path):
    """Every Argo `Application` manifest directly under `infra/`.

    NOT `rglob`. `infra/<name>/` holds the manifests an Application SOURCES,
    and reading one of those as an Application would double-count it.

    THREE EXTENSIONS, NOT ONE. Argo CD's own directory-source matcher is
    `^.*\\.(yaml|yml|json|jsonnet)$` (`reposerver/repository/repository.go`,
    `findManifests` -> `getPotentiallyValidManifestFile`, verified at tag
    v3.1.8 — the version this estate runs), so `root` adopts an Application
    declared as `.yml` or `.json` exactly as it adopts a `.yaml` one. A gate
    that globbed `*.yaml` alone would miss a restored copy under either other
    extension, silently. `.jsonnet` is Argo's to evaluate, not a plain
    manifest this gate can `yaml.safe_load`, and nothing under `infra/` uses
    it — the two red cases below prove the two extensions this gate adds, not
    a claim about jsonnet.
    """
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


def infra_children_still_here(tree: Path) -> list[str]:
    """Every `INFRA_CHILDREN_HANDOVER` name `infra/*.yaml` still declares, in handover order."""
    names = set(infra_application_names(tree))
    return [name for name in INFRA_CHILDREN_HANDOVER if name in names]


def a_copy_of_the_tree(tmp_path: Path) -> Path:
    """The repository, copied, so no red case can touch the working tree.

    `.git` is excluded because it is large and no red case reads it — which is
    also what makes these cases run identically on a depth-1 CI checkout.
    """
    tree = tmp_path / "deploy"
    shutil.copytree(REPOSITORY, tree, ignore=shutil.ignore_patterns(".git"))
    return tree


@pytest.fixture(scope="module")
def working_tree() -> Path:
    """The repository itself. Read only; every red case copies it first."""
    return REPOSITORY


def test_no_infra_child_application_is_declared_here_after_the_handover(working_tree: Path) -> None:
    """None of the five children `deploy#79` guarded is left under `infra/`."""
    left = infra_children_still_here(working_tree)
    print(f"[infra retire] {len(left)} of {len(INFRA_CHILDREN_HANDOVER)} child Application(s) still declared")
    assert left == [], f"handed to yadgarhq/argocd, still declared under infra/: {left}"


def test_infra_declares_exactly_the_applications_left_after_the_handover(working_tree: Path) -> None:
    """Census: `infra/*.yaml` declares `infra` alone, and no other Application."""
    names = infra_application_names(working_tree)
    print(f"[infra retire] {len(names)} Application(s) under infra/: {sorted(names)}")
    assert len(names) == len(set(names)), f"a name is declared twice: {sorted(names)}"
    assert set(names) == EXPECTED_INFRA_APPLICATIONS, sorted(set(names) ^ EXPECTED_INFRA_APPLICATIONS)


def test_the_infra_root_does_not_carry_prune_false(working_tree: Path) -> None:
    """`infra` still carries NO `sync-options` annotation at all.

    `infra` leaves by a hand delete (M4), never by a prune, so it was never a
    `Prune=false` subject. Any `sync-options` value on it fails this test, not
    only `Prune=false`.
    """
    for _, application in applications(working_tree):
        metadata = application.get("metadata") or {}
        if metadata.get("name") == "infra":
            assert SYNC_OPTIONS not in (metadata.get("annotations") or {})
            return
    pytest.fail("infra/apps.yaml declares no `infra` Application")


# THE SMALLEST APPLICATION THE GATE MUST STILL NAME. Only `metadata.name` is
# read, so the red case carries nothing more than an Application needs — the
# same shape `RESTORED_OPERATOR_APPLICATION` used in `test_no_two_owners.py`.
RESTORED_CHILD_APPLICATION = """\
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


def test_a_restored_infra_child_application_reddens(tmp_path: Path) -> None:
    """Mutation check: restore one of the five deleted files; the gate names it and only it."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "yadgar-app.yaml").write_text(RESTORED_CHILD_APPLICATION.format(name="yadgar"))
    assert infra_children_still_here(tree) == ["yadgar"]
    assert set(infra_application_names(tree)) - EXPECTED_INFRA_APPLICATIONS == {"yadgar"}


def test_a_restored_infra_child_application_under_another_filename_reddens(tmp_path: Path) -> None:
    """Red case: the gate keys on the Application's name, so the filename does not hide it."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "operators-arc.yaml").write_text(RESTORED_CHILD_APPLICATION.format(name="arc"))
    assert infra_children_still_here(tree) == ["arc"]


def test_a_restored_infra_child_application_as_yml_reddens(tmp_path: Path) -> None:
    """Red case: Argo's directory source adopts `.yml` exactly as `.yaml`, so this gate must too."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "tls-app.yml").write_text(RESTORED_CHILD_APPLICATION.format(name="tls"))
    assert infra_children_still_here(tree) == ["tls"]


def test_a_restored_infra_child_application_as_json_reddens(tmp_path: Path) -> None:
    """Red case: Argo's directory source adopts `.json` exactly as `.yaml`, so this gate must too."""
    tree = a_copy_of_the_tree(tmp_path)
    application = yaml.safe_load(RESTORED_CHILD_APPLICATION.format(name="estate-front"))
    (tree / "infra" / "estate-front-app.json").write_text(json.dumps(application))
    assert infra_children_still_here(tree) == ["estate-front"]
