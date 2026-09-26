"""No object in namespace `yadgar` has two owners.

THE GATE `plans/retiring-the-deploy-copies.md` ASKS FOR, AND THE ONLY REPOSITORY
THAT CAN RUN IT. It needs both sides of the retirement at once: the parent chart's
render at THIS organisation's values, and what every surviving `deploy`
Application still sources. `yadgarhq/chart`'s `scripts/tests/test_parent_chart.py`
renders the parent and has never seen `deploy`. This repository holds
`infra/yadgar-app.yaml` (the pinned parent version), `infra/yadgar/values.yaml`
(this organisation's values) AND the surviving Applications, so it is the only one
that can compare them.

WHAT IS ASSERTED, in the register's own terms:

  C1  `len(D ∩ P)` is 0 after every step.
  C2  `len(D)` is printed AND asserted, so C1 cannot pass over an empty left side.
  C5  the likeliest operator error — flip the toggle, forget the deletion — is a
      RED case built from values alone, and it must read exactly three tuples.

D is the `(apiGroup, kind, name)` tuples every surviving `deploy` Application
whose `destination.namespace` is `yadgar` sources, other than the `yadgar`
Application itself. P is `helm template` of the pinned parent at
`infra/yadgar/values.yaml`.

`destination.namespace` RATHER THAN `repoURL`, and it is not a convenience. A gate
keyed on `repoURL` never admits `infra/nats.yaml`, whose chart comes from
`nats-io.github.io` — and ADR-0786 names that Application as one of the two the
parent duplicates. Such a gate would be structurally blind to the exact class the
ruling calls out. Keying on the destination namespace also drops `infra/apps.yaml`
(it installs into `argocd`) and `estate-front-app` (into `estate-front`) with no
exception written for either.

WITH ONE GAP THE DISCRIMINATOR CANNOT CLOSE: a namespace says nothing about a
CLUSTER-SCOPED object. `ClusterIssuer/yadgar-dev-ca` and `GatewayClass/eg` are in D
today only because `tls-app`'s `destination.namespace` happens to read `yadgar`,
which is an accident of where that Application puts its NAMESPACED objects. So
cluster-scoped kinds are admitted regardless of the Application's namespace, by the
written-down list below — this gate has no cluster to ask.

THE APIGROUP OF A CORE KIND IS THE EMPTY STRING ON BOTH SIDES. The plan's own text
is inconsistent here — its FAIL example writes `/Service/valkey` and register row
C5 writes `v1|Service|valkey`. Either convention works; MIXING them is what does
not, because every core-kind collision would then be invisible, and
`Service/valkey` is one of the three tuples this gate exists to catch. The empty
form is taken, applied to D and P alike, and `test_the_toggle_without_the_deletion_reddens`
is what proves the two sides agree.

DERIVING D FROM THE APPLICATIONS RATHER THAN FROM A DIRECTORY LIST IS THE POINT. A
gate handed a hard-coded list of directories keeps passing after a step deletes an
Application and leaves its directory behind, and that is a state this plan's steps
pass through. Ownership is a property of the Application, so the Application is
what is read.

ONE PROPERTY THIS GATE DOES NOT HAVE, stated here rather than discovered later: it
compares what the Applications SOURCE, not what the cluster HOLDS. An object
created by an Application that was deleted without pruning is invisible to it. The
cutover plan's live object-set equality covers that side; this is its render-time
counterpart, not its replacement.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
INFRA = REPOSITORY / "infra"

# THE APPLICATION THIS GATE IS ABOUT, EXCLUDED FROM D BECAUSE IT IS P.
PARENT_APPLICATION = "yadgar"

# THE NAMESPACE THE PARENT INSTALLS INTO. An Application pointed anywhere else
# cannot collide with it on a namespaced object.
TARGET_NAMESPACE = "yadgar"

# C2's RUNG FOR THIS STEP. One rung per MERGE, not per step. 40 at step 0, 37
# after step 3 deletes `infra/valkey/`'s two objects and `valkey-ingress`.
# Whoever lands step 4 moves this to 34 in the same merge that deletes
# `infra/databases/`, and the ladder lives in the plan's count register.
EXPECTED_DEPLOY_TUPLES = 37

# THE `--api-versions` FLAGS, AND EACH NEEDS ITS OWN FLAG. The `-db` charts call
# `fail` when `database.create` is true and `k8s.mariadb.com/v1alpha1` is absent,
# and `helm template` populates `.Capabilities.APIVersions` with CRD-backed groups
# only from a live cluster.
API_VERSIONS = (
    "k8s.mariadb.com/v1alpha1",
    "cert-manager.io/v1",
    "keda.sh/v1alpha1",
    "gateway.networking.k8s.io/v1",
    "gateway.envoyproxy.io/v1alpha1",
    "monitoring.coreos.com/v1",
)

# CLUSTER-SCOPED KINDS, WRITTEN DOWN BECAUSE THERE IS NO CLUSTER TO ASK. A
# cluster-scoped object is visible to every namespace whatever an Application's
# destination says, so it is admitted into D regardless of that destination. Only
# the kinds this estate actually renders are listed; a kind arriving later that is
# cluster-scoped and absent here is admitted only if its Application happens to
# target `yadgar`, which is the latent gap the module docstring names.
CLUSTER_SCOPED: frozenset[tuple[str, str]] = frozenset(
    {
        ("cert-manager.io", "ClusterIssuer"),
        ("gateway.networking.k8s.io", "GatewayClass"),
        ("rbac.authorization.k8s.io", "ClusterRole"),
        ("rbac.authorization.k8s.io", "ClusterRoleBinding"),
        ("apiextensions.k8s.io", "CustomResourceDefinition"),
        ("", "Namespace"),
        ("storage.k8s.io", "StorageClass"),
    }
)


@dataclass(frozen=True)
class Failure:
    """One object with two owners, and both owners named."""

    tuple: tuple[str, str, str]
    deploy_owner: str
    parent_owner: str


def api_group(api_version: str) -> str:
    """The group half of an `apiVersion`, EMPTY for a core kind.

    `v1` is the core group and carries no slash, so it answers `""` rather than
    `"v1"`. See the module docstring for why mixing the two forms is the one
    mistake this helper exists to prevent.
    """
    return api_version.split("/")[0] if "/" in api_version else ""


def tuples_of(documents) -> set[tuple[str, str, str]]:
    """`(apiGroup, kind, name)` for every real document in a manifest stream.

    A `None` document — what an empty file or a trailing `---` yields — is skipped
    rather than counted. A document with no `kind` is not an object.
    """
    found = set()
    for document in documents:
        if not isinstance(document, dict) or not document.get("kind"):
            continue
        metadata = document.get("metadata") or {}
        name = metadata.get("name")
        if not name:
            continue
        found.add((api_group(document.get("apiVersion", "")), document["kind"], name))
    return found


def helm(*arguments: str) -> str:
    """`helm` with its stderr folded into the failure, never swallowed."""
    finished = subprocess.run(
        ["helm", *arguments], capture_output=True, text=True, check=False
    )
    if finished.returncode != 0:
        raise AssertionError(
            f"`helm {' '.join(arguments)}` exited {finished.returncode}:\n"
            f"{finished.stderr}"
        )
    return finished.stdout


def api_version_flags() -> list[str]:
    """One `--api-versions` flag per group, never one flag carrying six values."""
    flags: list[str] = []
    for group in API_VERSIONS:
        flags += ["--api-versions", group]
    return flags


def applications(tree: Path):
    """Every Argo `Application` manifest directly under `infra/`.

    NOT `rglob`. `infra/<name>/` holds the manifests an Application SOURCES, and
    reading one of those as an Application would double-count it.
    """
    for path in sorted((tree / "infra").glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text()):
            if isinstance(document, dict) and document.get("kind") == "Application":
                yield path, document


def deploy_side(tree: Path) -> dict[tuple[str, str, str], str]:
    """Set D, each tuple mapped to the Application that owns it.

    A DIRECTORY source contributes every document under `spec.source.path`. A
    CHART source is rendered with `helm template` at that Application's own
    `spec.source.helm.values`, under the Application's name as the release name —
    the release name is what prefixes the objects an upstream chart renders, so
    getting it wrong changes every tuple.

    A CHART SOURCE OUTSIDE `yadgar` IS NOT RENDERED, AND THAT IS A STATED LIMIT
    RATHER THAN AN OVERSIGHT. Eight Applications here install operators into their
    own namespaces from six registries — `arc`, `cert-manager`, `envoy-gateway`,
    `estate-front-runner`, `keda`, both `mariadb-operator` charts and
    `prometheus`. Rendering them would make this gate depend on six registries
    being reachable to answer a question about one namespace, and it would buy
    only the CLUSTER-SCOPED half of their output. The plan states as a non-finding
    that none of them renders a cluster-scoped name either side also holds — the
    envoy-gateway chart at 1.9.1 creates no GatewayClass, which is measured and is
    why `infra/tls/gatewayclass.yaml` exists at all. So this is a latent gap in the
    gate's reach, not a live defect, and the day an operator chart starts rendering
    such a name it is this paragraph that has to change rather than a number.

    DIRECTORY SOURCES ARE ALWAYS READ, wherever they install, because reading a
    file costs nothing and `estate-front` is a directory source in another
    namespace. Only its cluster-scoped objects could be admitted, and it has none.
    """
    owners: dict[tuple[str, str, str], str] = {}
    for path, application in applications(tree):
        name = (application.get("metadata") or {}).get("name", path.name)
        if name == PARENT_APPLICATION:
            continue
        specification = application.get("spec") or {}
        source = specification.get("source") or {}
        namespace = (specification.get("destination") or {}).get("namespace")

        if source.get("path"):
            documents = []
            for manifest in sorted((tree / source["path"]).glob("*.yaml")):
                documents += list(yaml.safe_load_all(manifest.read_text()))
            candidates = tuples_of(documents)
            origin = f"deploy Application `{name}` ({source['path']})"
        elif source.get("chart") and namespace == TARGET_NAMESPACE:
            with tempfile.TemporaryDirectory() as scratch:
                values = Path(scratch) / "values.yaml"
                values.write_text((source.get("helm") or {}).get("values", "") or "{}")
                rendered = helm(
                    "template",
                    name,
                    source["chart"],
                    "--repo",
                    source["repoURL"],
                    "--version",
                    source["targetRevision"],
                    "--namespace",
                    namespace or TARGET_NAMESPACE,
                    "--values",
                    str(values),
                    *api_version_flags(),
                )
            candidates = tuples_of(yaml.safe_load_all(rendered))
            origin = f"deploy Application `{name}` ({source['repoURL']} {source['chart']})"
        else:
            continue

        for candidate in candidates:
            group, kind, _ = candidate
            # A NAMESPACED object only competes with the parent where the
            # Application installs into the parent's namespace. A CLUSTER-SCOPED
            # one competes everywhere, which is why its admission ignores the
            # destination — see the module docstring.
            if namespace == TARGET_NAMESPACE or (group, kind) in CLUSTER_SCOPED:
                owners.setdefault(candidate, origin)
    return owners


def parent_side(tree: Path, overrides: tuple[str, ...] = ()) -> set[tuple[str, str, str]]:
    """Set P: the pinned parent rendered at this organisation's values.

    THE VERSION IS READ OFF `infra/yadgar-app.yaml` RATHER THAN WRITTEN HERE, so a
    parent bump moves the gate's subject with it and never leaves the gate
    measuring a version the cluster stopped running.
    """
    application = next(
        document
        for _, document in applications(tree)
        if (document.get("metadata") or {}).get("name") == PARENT_APPLICATION
    )
    chart = next(
        source
        for source in (application["spec"].get("sources") or [application["spec"]["source"]])
        if source.get("chart")
    )
    with tempfile.TemporaryDirectory() as scratch:
        helm(
            "pull",
            f"oci://{chart['repoURL']}/{chart['chart']}",
            "--version",
            chart["targetRevision"],
            "--untar",
            "--untardir",
            scratch,
        )
        rendered = helm(
            "template",
            PARENT_APPLICATION,
            str(Path(scratch) / chart["chart"]),
            "--namespace",
            TARGET_NAMESPACE,
            "--values",
            str(tree / "infra" / "yadgar" / "values.yaml"),
            *api_version_flags(),
            *overrides,
        )
    return tuples_of(yaml.safe_load_all(rendered))


def two_owners(tree: Path, overrides: tuple[str, ...] = ()) -> tuple[list[Failure], int]:
    """Every object both sides claim, and the size of the left side.

    RETURNS `len(D)` ALONGSIDE, because C1 over an empty D passes having examined
    nothing. The caller asserts both.
    """
    owners = deploy_side(tree)
    parent = parent_side(tree, overrides)
    failures = [
        Failure(candidate, owners[candidate], "the parent render")
        for candidate in sorted(owners.keys() & parent)
    ]
    return failures, len(owners)


def render(failures: list[Failure], examined: int) -> str:
    """The failure message, with BOTH owners named for every tuple."""
    lines = [f"FAIL: {len(failures)} object(s) have two owners."]
    for failure in failures:
        group, kind, name = failure.tuple
        lines.append(
            f"  {group}/{kind}/{name}  — {failure.deploy_owner} AND {failure.parent_owner}"
        )
    lines.append(f"  examined {examined} tuple(s) on the deploy side")
    return "\n".join(lines)


# ── THE RED CASES' OWN SUBJECTS ───────────────────────────────────────────────
# WRITTEN HERE RATHER THAN READ BACK OUT OF GIT OR OUT OF A FIXTURE FILE, and
# both alternatives were rejected for a measured reason.
#
#   * `git show origin/main:…` needs a full checkout. CI clones at depth 1, so the
#     red case would ERROR on the runner while passing locally — a red case that
#     cannot run is worse than none.
#   * A `.yaml` fixture on disk is walked by `scripts/policy_sources_named.py`,
#     which parses EVERY `*.yaml` under the repository root. A fixture copy of
#     `valkey-ingress` would be found by that gate and demanded back into its
#     `EXPECTED_ALLOW_ALL_PORTS`, so deleting the policy and keeping a fixture of
#     it would leave the policy gate asserting the deletion never happened.
#
# THEY CARRY ONLY WHAT THE GATE READS — apiVersion, kind, name, and for the
# Application its source and destination. A red case needs the TUPLES, not the
# objects, and a full copy would rot against a file that no longer exists.
RESTORED_VALKEY_APPLICATION = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: valkey
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/yadgarhq/deploy
    targetRevision: main
    path: infra/valkey
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
  syncPolicy:
    automated: { prune: true, selfHeal: true }
"""

RESTORED_VALKEY_OBJECTS = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: valkey
---
apiVersion: v1
kind: Service
metadata:
  name: valkey
"""

RESTORED_VALKEY_INGRESS = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: valkey-ingress
  namespace: yadgar
"""


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


def test_the_deploy_side_is_the_size_the_register_says(working_tree: Path) -> None:
    """C2. Printed AND asserted, so C1 cannot pass over an empty left side.

    A merge that deletes one file more than it meant to falls below this rung and
    fails HERE, before the intersection is reached and before a reader can read an
    empty D as a clean estate.
    """
    _, examined = two_owners(working_tree)
    print(f"[two-owners gate] len(D) = {examined}")
    assert examined == EXPECTED_DEPLOY_TUPLES, (
        f"the deploy side holds {examined} tuple(s) and the count register's rung "
        f"for this step is {EXPECTED_DEPLOY_TUPLES}. Either a file was deleted that "
        f"this step does not delete, or the rung was not moved with the merge."
    )


def test_nothing_in_the_namespace_has_two_owners(working_tree: Path) -> None:
    """C1. The invariant every step of the retirement must hold."""
    failures, examined = two_owners(working_tree)
    assert not failures, render(failures, examined)


def test_restoring_a_deleted_copy_reddens_the_gate(tmp_path: Path) -> None:
    """The D-side red case: what step 3 deleted, put back.

    BOTH HALVES COME BACK — the Application, because D is derived from
    Applications, and the directory, because that is what it sources.

    A TEMPLATE MUTATION CANNOT CONSTRUCT THIS. Ownership is an Argo property and
    not a render property, so the only way to give an object a second owner is to
    give it a second Application.
    """
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "valkey").mkdir()
    (tree / "infra" / "valkey" / "valkey.yaml").write_text(RESTORED_VALKEY_OBJECTS)
    (tree / "infra" / "valkey-app.yaml").write_text(RESTORED_VALKEY_APPLICATION)

    failures, examined = two_owners(tree)
    assert failures, (
        "infra/valkey came back under its own Application and the gate reported "
        f"nothing over {examined} tuple(s)"
    )
    assert {failure.tuple for failure in failures} == {
        ("apps", "Deployment", "valkey"),
        ("", "Service", "valkey"),
    }, render(failures, examined)
    assert all(
        "valkey" in failure.deploy_owner and "parent" in failure.parent_owner
        for failure in failures
    ), render(failures, examined)


def test_an_orphaned_directory_stays_green(tmp_path: Path) -> None:
    """Restoring the DIRECTORY and not the Application must NOT redden.

    A directory nothing sources is untidy, not double-owned. A gate that reddened
    on it would fail every step of this plan mid-flight, because each step's merge
    is reviewed in exactly that shape while the Application is already gone.
    """
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "valkey").mkdir()
    (tree / "infra" / "valkey" / "valkey.yaml").write_text(RESTORED_VALKEY_OBJECTS)

    failures, examined = two_owners(tree)
    assert not failures, render(failures, examined)
    assert examined == EXPECTED_DEPLOY_TUPLES, (
        f"an orphaned directory moved len(D) to {examined}, so the gate is reading "
        f"directories rather than Applications"
    )


def test_the_toggle_without_the_deletion_reddens(tmp_path: Path) -> None:
    """Register row C5: the P-side red case, and the likeliest operator error.

    THE TWO CASES ABOVE BOTH MOVE THE LEFT SIDE. This one moves the other: the two
    `--set` flags are step 3's flip, passed explicitly so the case constructs
    itself out of VALUES rather than out of whatever `infra/yadgar/values.yaml`
    happens to say on the day. Every step of this plan carries its deletion and its
    flip in one merge, so dropping half of one is a plausible slip rather than a
    contrived one — and the plan's ordering paragraph says which half is dangerous:
    a toggle true beside a live copy is two automated Applications pruning each
    other.

    The register writes this case against STEP 0's tree, where all three files are
    still present. After step 3 they are not, so they are restored — the state
    being constructed is the same one, and the flags are what make it independent
    of the values file.

    THREE TUPLES BY NAME, NOT A COUNT. The NetworkPolicy is in it because
    `platform.valkey.create` gates the `valkey-ingress` half of `platform`'s
    `ingress-policies.yaml`, which is the same fact that forces
    `shared-infrastructure.yaml` to be split at this step. Asserting the names
    rather than the number is also what proves the two sides agree on the core-kind
    apiGroup form: `Service/valkey` carries the empty group on BOTH sides, or it is
    not found at all and this case silently drops to two.
    """
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "valkey").mkdir()
    (tree / "infra" / "valkey" / "valkey.yaml").write_text(RESTORED_VALKEY_OBJECTS)
    (tree / "infra" / "valkey-app.yaml").write_text(RESTORED_VALKEY_APPLICATION)
    (tree / "infra" / "network-policies" / "valkey-ingress.yaml").write_text(
        RESTORED_VALKEY_INGRESS
    )

    failures, examined = two_owners(
        tree,
        overrides=(
            "--set",
            "platform.enabled=true",
            "--set",
            "platform.valkey.create=true",
        ),
    )
    assert {failure.tuple for failure in failures} == {
        ("apps", "Deployment", "valkey"),
        ("", "Service", "valkey"),
        ("networking.k8s.io", "NetworkPolicy", "valkey-ingress"),
    }, render(failures, examined)
