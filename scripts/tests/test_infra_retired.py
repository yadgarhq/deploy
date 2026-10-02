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
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]

# `make bootstrap`'s one-time handover apply. Its continued presence would
# recreate the `infra` Application M4 deleted by hand on every fresh cluster.
APPLY_INFRA_APPS = "kubectl apply -f infra/apps.yaml"


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


_APPLY_INFRA_APPS_RE = re.compile(
    r"kubectl(?:\s+--context(?:=|\s+)\S+)?\s+apply\s+-f\s+infra/apps\.yaml\b"
)


def makefile_applies_infra_apps(tree: Path) -> bool:
    """Whether any Makefile target still runs `kubectl apply -f infra/apps.yaml`.

    A regex, not a plain substring search — pinned or not, the failure this
    gate exists for is the LINE coming back under a renamed or different
    target, not only under `bootstrap`, and not only in its original
    unpinned form. `(?:\\s+--context(?:=|\\s+)\\S+)?` tolerates the
    `--context $(KUBE_CONTEXT)` pin every kubectl in this Makefile now
    carries (ledger 1228 follow-up) landing between `kubectl` and `apply`.
    """
    makefile = tree / "Makefile"
    if not makefile.is_file():
        return False
    return bool(_APPLY_INFRA_APPS_RE.search(makefile.read_text()))


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


def test_makefile_no_longer_applies_infra_apps_yaml(working_tree: Path) -> None:
    """`make bootstrap` cannot recreate the `infra` Application M4 deleted by hand."""
    assert not makefile_applies_infra_apps(working_tree), (
        f"Makefile still runs `{APPLY_INFRA_APPS}` — a fresh `make bootstrap` "
        "would recreate the `infra` Application M4 deleted by hand."
    )


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


PINNED_APPLY_INFRA_APPS = "kubectl --context $(KUBE_CONTEXT) apply -f infra/apps.yaml"


@pytest.mark.parametrize(
    "restored_line",
    [APPLY_INFRA_APPS, PINNED_APPLY_INFRA_APPS],
    ids=["unpinned", "pinned"],
)
def test_a_restored_bootstrap_apply_line_reddens(tmp_path: Path, restored_line: str) -> None:
    """Mutation check: restore the Makefile's apply line, pinned or not; the gate catches it.

    `APPLY_INFRA_APPS` is a literal substring, so a restoration that carries
    the `--context $(KUBE_CONTEXT)` pin every kubectl in this Makefile now
    gets (ledger 1228 follow-up) is NOT a substring match even though it is
    exactly the hazard this gate exists to catch — a fresh `make bootstrap`
    would still recreate the `infra` Application M4 deleted by hand, just
    pointed at a pinned context instead of the ambient one.
    """
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    # Re-insert the line under `bootstrap:`, the same spot it was deleted from.
    # `kubectl(?: --context \S+)?` tolerates the `--context $(KUBE_CONTEXT)`
    # pin every kubectl in this target now carries (ledger 1228 follow-up) —
    # the anchor is the ARGOCD_REPO apply, not the exact flags beside it.
    mutated = re.sub(
        r"(\n\tkubectl(?: --context \S+)? apply -f \$\(ARGOCD_REPO\)/projects/root\.yaml\n)",
        r"\1\t" + restored_line + "\n",
        text,
        count=1,
    )
    assert mutated != text, "the anchor line this mutation inserts after is gone — update the regex"
    makefile.write_text(mutated)
    assert makefile_applies_infra_apps(tree) is True
