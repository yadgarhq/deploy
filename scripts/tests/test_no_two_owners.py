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
keyed on `repoURL` never admitted `infra/nats.yaml`, whose chart came from
`nats-io.github.io` until step 6's third merge deleted it — and ADR-0786 named
that Application as one of the two the parent duplicated. Such a gate would have
been structurally blind to the exact class the ruling called out. Keying on the destination namespace also drops `infra/apps.yaml`
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

import re
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
# after step 3 deletes `infra/valkey/`'s two objects and `valkey-ingress`, 34
# after merge A of `plans/the-mariadb-crs-into-the-chart.md` deletes
# `infra/databases-app.yaml`. The ladder lives in the plan's count register.
#
# THE APPLICATION MOVES THIS NUMBER, NOT THE DIRECTORY, and the comment this
# replaces said the opposite — it told whoever lands step 4 to move the rung
# "in the same merge that deletes `infra/databases/`", which is merge B. D is
# derived from Applications rather than from directories, so the three MariaDB
# tuples leave D the moment this Application file goes, with `infra/databases/`
# still sitting on disk. Measured 2026-09-26 against the pinned parent 0.2.38:
# 37 before that deletion and 34 after it. Merge B then deletes the directory
# and moves this rung by ZERO, which is why 34 appears twice in the ladder.
#
# SO THE LINE MOVES IN MERGE A'S OWN COMMIT. Deleting the Application without
# moving it reddens C2 on the merge itself.
#
# 21 AFTER STEP 5, which deletes `infra/internal-tls-app.yaml` and its
# directory in ONE merge. That Application sourced 13 tuples — the internal
# CA's Certificate, its two Issuers and the ten leaves — and the parent at
# 0.2.38 with `platform.internalCA.create` and `platform.certificates.create`
# true renders exactly those 13 and nothing else. Measured 2026-09-27: 34
# before the merge and 21 after. Restoring the Application AND its directory
# reads 34 with 13 two-owner failures; restoring either one alone reads 21,
# because an Application whose path is absent sources nothing.
#
# 20 AFTER STEP 6's FIRST MERGE, which deletes
# `infra/network-policies/nats-ingress.yaml` and sets `platform.nats.create`
# true. The file is a DIRECTORY source of the surviving `network-policies`
# Application, so this rung falls by the one tuple it held while the
# Application stays. The toggle renders six objects — `nats-ingress` and five
# `yadgar-nats*` names — and only the first collides with anything `deploy`
# holds: `infra/nats.yaml` renders `nats`, `nats-headless` and so on under its
# own release name. Measured 2026-09-27 at the pinned 0.2.38: 21 before and 20
# after. Step 6's second merge deletes nothing, so 20 appears twice in the
# ladder; its third merge deletes `infra/nats.yaml` and moves this to 15.
#
# 15 AFTER STEP 6's THIRD MERGE, which deletes `infra/nats.yaml`. That
# Application was D's one CHART source, rendered at release `nats`: exactly
# five tuples — `StatefulSet`, `Service`, `PodDisruptionBudget` `nats`,
# `Service/nats-headless` and `ConfigMap/nats-config` — and the live
# Application tracks the same five. Measured 2026-09-27 at the pinned 0.2.38:
# 20 before and 15 after. D now holds no chart source at all, so this gate no
# longer reaches `nats-io.github.io`.
#
# STILL 15 AFTER B5 of `plans/the-one-application-install.md`, which bumps the
# parent to 0.2.42 and deletes the two `nats.url` lines. B5 changes P and
# touches no Application, so D does not move. Measured 2026-09-27: 15 at the
# pinned 0.2.38 before and 15 at the pinned 0.2.42 after, with 0 two-owner
# failures on both sides.
#
# 11 AFTER STEP 7, which deletes `infra/tls/{certificate,envoyproxy,
# gatewayclass,gateway}.yaml` and sets `platform.edgeTLS.create` and
# `platform.gatewayListener.create` true. `tls` is a SPLIT, not a retirement:
# `infra/tls-app.yaml`, `ca-preflight.yaml` and `clusterissuer.yaml` stay, and
# they are the four `yadgar-tls-preflight` objects plus
# `ClusterIssuer/yadgar-dev-ca`, none of which the parent renders. Measured
# 2026-09-27 at the pinned 0.3.7: 15 before and 11 after.
#
# 10 AFTER STEP 8, which deletes `infra/network-policies-app.yaml` and its
# directory — `gateway-ingress.yaml` was the one file left in it — and sets
# `gateway.networkPolicy.enabled` true. The Application sourced exactly one
# tuple. Measured 2026-09-27 at the pinned 0.3.7: 11 before and 10 after.
#
# 5 AFTER STEP 9's FIRST MERGE (A5 of `plans/the-one-application-install.md`,
# ADR-0810), which deletes `infra/bootstrap-app.yaml` and `infra/bootstrap/`.
# That Application sourced five tuples: `Job/bootstrap-secrets`,
# `Job/admin-bootstrap-token`, and `ServiceAccount`, `Role` and `RoleBinding`
# `bootstrap-secrets`. The 5 left are `tls`'s four `yadgar-tls-preflight`
# objects and `ClusterIssuer/yadgar-dev-ca`, which is register row C4's end
# state. Measured 2026-09-27 at the pinned 0.3.7: 10 before and 5 after.
EXPECTED_DEPLOY_TUPLES = 5

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
    """Set P: the pinned parent rendered at this organisation's values."""
    return tuples_of(parent_render(tree, overrides))


def parent_render(tree: Path, overrides: tuple[str, ...] = ()) -> list:
    """The documents of P, for a check that reads more than the tuples.

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
    return list(yaml.safe_load_all(rendered))


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


# STEP 8 RETIRED `network-policies` WHOLE, so the red cases that restore a
# policy file restore the Application that sourced it too — D is derived from
# Applications, and a file nothing sources is not an owner.
RESTORED_NETWORK_POLICIES_APPLICATION = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: network-policies
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/yadgarhq/deploy
    targetRevision: main
    path: infra/network-policies
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
"""


def restore_network_policies(tree: Path) -> None:
    """The retired `network-policies` Application and its (empty) directory."""
    (tree / "infra" / "network-policies").mkdir(exist_ok=True)
    (tree / "infra" / "network-policies-app.yaml").write_text(
        RESTORED_NETWORK_POLICIES_APPLICATION
    )


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
    restore_network_policies(tree)
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


# ── ZERO OWNERS, THE OTHER HALF OF THE ORDERING ──────────────────────────────
# C1 refuses TWO owners and is blind to NONE: step 5 with its deletion kept and
# its flip dropped reads 0 over 21 and passes, while the `yadgar` Application
# renders none of the 13 objects `internal-tls` owned — the live CA and its
# leaves would then have no healer at all. So every tuple a retired Application
# owned is asserted to be in P. Measured 2026-09-27 at the pinned 0.2.38.
RETIRED_TUPLES: dict[str, frozenset[tuple[str, str, str]]] = {
    "internal-tls": frozenset(
        {("cert-manager.io", "Issuer", "yadgar-internal-selfsign")}
        | {("cert-manager.io", "Issuer", "yadgar-internal-ca")}
        | {("cert-manager.io", "Certificate", "yadgar-internal-ca")}
        | {
            ("cert-manager.io", "Certificate", f"{name}-tls")
            for name in (
                "iam", "iam-db", "task", "task-db", "project", "project-db",
                "gateway-client", "iam-client", "task-client", "project-client",
            )
        }
    ),
    # STEP 6, FIRST MERGE. The `network-policies` Application SURVIVES this
    # merge, so the key names the file that left it rather than a retired
    # Application. Only this one tuple is listed here: `infra/nats.yaml`'s
    # five objects left at the third merge, when the parent at 0.2.38 rendered
    # them as `yadgar-nats*`. They have their own entry below, from B5.
    "network-policies/nats-ingress.yaml": frozenset(
        {("networking.k8s.io", "NetworkPolicy", "nats-ingress")}
    ),
    # STEP 6's THIRD MERGE DELETED `infra/nats.yaml`, AND B5 IS WHAT ADDS ITS
    # ENTRY. At the pinned 0.2.38 these five NAMES were not in P — the parent
    # rendered the broker as `yadgar-nats*` — so the third merge orphaned the
    # live objects rather than handing them over, and B4b of
    # `plans/the-one-application-install.md` deleted them by hand. From parent
    # 0.2.42, `platform` 0.1.17 carries `fullnameOverride: nats` (platform#16,
    # first released in 0.1.15, ADR-0805), so P renders exactly these five
    # names again: a delete-then-adopt, not a rename. Measured 2026-09-27: all
    # five in P at 0.2.42, none at 0.2.38.
    "nats.yaml": frozenset(
        {
            ("apps", "StatefulSet", "nats"),
            ("", "Service", "nats"),
            ("", "Service", "nats-headless"),
            ("", "ConfigMap", "nats-config"),
            ("policy", "PodDisruptionBudget", "nats"),
        }
    ),
    # STEP 7. The `tls` Application SURVIVES (it keeps the ClusterIssuer and
    # the preflight), so the key names its directory. The four objects are
    # handed over under the same names; `deploy#68` put `Prune=false` on each
    # source copy one merge ahead, so the handover is an adoption.
    "tls/{certificate,envoyproxy,gatewayclass,gateway}.yaml": frozenset(
        {
            ("cert-manager.io", "Certificate", "gateway-tls"),
            ("gateway.envoyproxy.io", "EnvoyProxy", "edge"),
            ("gateway.networking.k8s.io", "GatewayClass", "eg"),
            ("gateway.networking.k8s.io", "Gateway", "edge"),
        }
    ),
    # STEP 8. `network-policies` is retired whole: `gateway-ingress` was the
    # last object it sourced. `deploy#70` put `Prune=false` on the source copy
    # one merge ahead, so the handover is an adoption.
    "network-policies": frozenset(
        {("networking.k8s.io", "NetworkPolicy", "gateway-ingress")}
    ),
    # STEP 9 (A5) HAS NO ENTRY HERE, AND THAT IS NOT AN OMISSION. This dict
    # holds TRACKED objects the parent now owns. The parent renders the five
    # `infra/bootstrap/` names only as helm HOOKS (`pre-install,pre-upgrade`,
    # `before-hook-creation`), which Argo runs as `PreSync` and never tracks,
    # and `tuples_of` does not tell a hook from a tracked object. An entry here
    # would therefore pass at A5's second merge while calling the five
    # "owned" in the sense every other entry means. They are counted on their
    # own, below, as `BOOTSTRAP_HOOK_NAMES`.
}

# THE COUNT THE ZERO-OWNERS TEST ASSERTS, written down so that an entry dropped
# from the dict above reddens rather than shrinks what is examined. 13 from
# step 5, 1 from step 6's first merge, 5 from B5, 4 from step 7 and 1 from
# step 8.
EXPECTED_RETIRED_TUPLES = 24


def orphans(tree: Path, overrides: tuple[str, ...] = ()) -> list[tuple[str, tuple[str, str, str]]]:
    """Every retired tuple the parent render does NOT carry, with its old owner."""
    parent = parent_side(tree, overrides)
    return sorted(
        (owner, candidate)
        for owner, retired in RETIRED_TUPLES.items()
        for candidate in retired
        if candidate not in parent
    )


def test_every_retired_object_has_the_parent_as_its_owner(working_tree: Path) -> None:
    """No object a retired Application owned is left with NO owner."""
    examined = sum(len(retired) for retired in RETIRED_TUPLES.values())
    print(f"[zero-owners gate] {examined} retired tuple(s) examined")
    assert examined == EXPECTED_RETIRED_TUPLES
    missing = orphans(working_tree)
    assert not missing, f"{len(missing)} retired object(s) have no owner: {missing}"


def test_the_deletion_without_the_toggle_reddens(tmp_path: Path) -> None:
    """Red case: step 5's deletion kept, its flip dropped. All 13 are orphaned."""
    missing = orphans(
        a_copy_of_the_tree(tmp_path),
        overrides=(
            "--set",
            "platform.internalCA.create=false",
            "--set",
            "platform.certificates.create=false",
        ),
    )
    assert {candidate for _, candidate in missing} == RETIRED_TUPLES["internal-tls"], missing


# ── STEP 6, FIRST MERGE: THE TWO RED CASES ───────────────────────────────────
# Only what the gate reads, for the reason the valkey constants above give.
RESTORED_NATS_INGRESS = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: nats-ingress
  namespace: yadgar
"""


def test_restoring_the_nats_policy_reddens_the_gate(tmp_path: Path) -> None:
    """Register row C4's red case BEFORE step 6a: the toggle kept, the file back.

    EXACTLY ONE TUPLE, and that one number is the claim this merge rests on.
    `platform.nats.create` renders six objects and five of them are
    `yadgar-nats*`, which nothing in `deploy` holds — `infra/nats.yaml`,
    until the third merge deleted it, rendered `nats`, `nats-headless` and so
    on. A second failure here would mean the two brokers share a name, which
    is the state step 6a's `fullnameOverride` creates and the reason that bump
    waits for the third merge.
    """
    tree = a_copy_of_the_tree(tmp_path)
    restore_network_policies(tree)
    (tree / "infra" / "network-policies" / "nats-ingress.yaml").write_text(
        RESTORED_NATS_INGRESS
    )

    failures, examined = two_owners(tree)
    assert {failure.tuple for failure in failures} == {
        ("networking.k8s.io", "NetworkPolicy", "nats-ingress"),
    }, render(failures, examined)
    assert all(
        "network-policies" in failure.deploy_owner for failure in failures
    ), render(failures, examined)
    assert examined == EXPECTED_DEPLOY_TUPLES + 1, render(failures, examined)


def test_the_nats_deletion_without_the_toggle_reddens(tmp_path: Path) -> None:
    """Red case: step 6's deletions kept, the toggle dropped.

    Exactly `nats-ingress` and, from B5, the five `nats*` broker objects go
    unowned, and nothing from step 5, so the case isolates the broker. With
    no owner the broker's pods would have NO ingress policy at all — every
    source admitted on 4222, 8222 and 6222 — and no healer.
    """
    missing = orphans(
        a_copy_of_the_tree(tmp_path),
        overrides=("--set", "platform.nats.create=false"),
    )
    assert {candidate for _, candidate in missing} == (
        RETIRED_TUPLES["network-policies/nats-ingress.yaml"] | RETIRED_TUPLES["nats.yaml"]
    ), missing


# ── B5: K3's EXPECTATION, ENCODED ────────────────────────────────────────────
# K3 of `plans/the-one-application-install.md` is the render diff of this
# organisation's values between the old and new pins. Measured 2026-09-27
# (0.2.38 with `main`'s values against 0.2.42 with B5's): 70 objects on both
# sides, the five `yadgar-nats*` objects removed, the five `nats*` objects
# added with every other field equal, `NATS_URL` in `Deployment/iam` and
# `Deployment/gateway` moved from `nats://yadgar-nats:4222` to
# `nats://nats:4222`, and the other 63 objects byte-identical. The old pin's
# render needs the old values file, which a depth-1 CI checkout does not
# hold, so what is encoded here is the AFTER half: the zero-owners entry
# above says the five `nats*` names are in P, and this test says no
# `yadgar-nats*` name survives beside them. A parent pinned back below the
# `fullnameOverride` renders the old names and reddens here.
RETIRED_BROKER_PREFIX = "yadgar-nats"


def test_no_broker_object_keeps_the_release_prefixed_name(working_tree: Path) -> None:
    """After B5, P renders the broker as `nats*` and nothing as `yadgar-nats*`."""
    parent = parent_side(working_tree)
    stale = sorted(t for t in parent if t[2].startswith(RETIRED_BROKER_PREFIX))
    print(f"[K3] {len(parent)} tuple(s) in P, {len(stale)} named {RETIRED_BROKER_PREFIX}*")
    assert not stale, f"the parent still renders the release-prefixed broker: {stale}"


# ── EVERY BROKER URL NAMES A BROKER, FROM STEP 6's SECOND MERGE ──────────────
# The two-owners gate reads NAMES and is blind to a REFERENCE. Step 6's third
# merge deletes `infra/nats.yaml`, and a url still reading `nats://nats:4222`
# would then dial a Service nobody renders — C1 reads 0, C2 reads 15, and both
# clients have lost their broker. Nothing above can see that, because no object
# changed owner. So every `nats://<host>` string in P is resolved against the
# Services P and D render.
#
# TWO QUESTIONS, TWO SETS, and the pair of red cases below is what proves each
# check reads the set it names:
#
#   * RESOLVES — the host is a Service in P ∪ D. True at every merge of step 6;
#     it is what reddens at the third merge if a url line is forgotten.
#   * ON THE PARENT'S BROKER — the host is a Service in P alone. False before
#     step 6's second merge (both urls dialled `deploy`'s `nats`), true from it
#     on. It stays true after step 6a / B5, whose `fullnameOverride: nats` makes
#     P render `Service/nats` while the urls fall back to the module default.
#
# B5 IS THE OTHER HALF OF THE COUPLING. From parent 0.2.42 P renders no
# `Service/yadgar-nats`, so a url line kept on `nats://yadgar-nats:4222`
# beside the bump reddens both checks; and the url lines deleted WITHOUT the
# bump leave both clients on `nats`, which 0.2.38 does not render. The red
# cases below therefore dial `yadgar-nats`, the name no pin from 0.2.42 on
# renders, where before B5 they dialled `nats`.
#
# THE FLOOR IS 2: `iam` publishes and `gateway` consumes. A render with fewer
# urls has dropped a client, and "no url failed" over zero urls is no finding.
MINIMUM_BROKER_URLS = 2

# A HOST, NOT A URL. `user:password@` and the port are stripped; a url list
# (`nats://a:4222,nats://b:4222`) yields one match per entry.
BROKER_URL = re.compile(r"nats://(?:[^@/\s\"',]+@)?([^:/\s\"',]+)")


def strings_in(node):
    """Every string anywhere inside a parsed document."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings_in(value)


def service_name(host: str) -> str:
    """The Service a host resolves to in `yadgar`, or the host unchanged.

    `nats` and `nats.yadgar.svc.cluster.local` name the same Service. A dotted
    host in any OTHER namespace is returned whole, so it matches no Service and
    is reported rather than silently accepted.
    """
    labels = host.split(".")
    if len(labels) == 1 or labels[1] == TARGET_NAMESPACE:
        return labels[0]
    return host


def broker_urls(documents) -> list[tuple[str, str]]:
    """`(Kind/name, host)` for every `nats://` url in a rendered stream."""
    found = []
    for document in documents:
        if not isinstance(document, dict) or not document.get("kind"):
            continue
        owner = f"{document['kind']}/{(document.get('metadata') or {}).get('name')}"
        for text in strings_in(document):
            found += [(owner, host) for host in BROKER_URL.findall(text)]
    return found


def services(tuples) -> set[str]:
    """The names of the core `Service` objects in a tuple set."""
    return {name for group, kind, name in tuples if (group, kind) == ("", "Service")}


def unresolved_brokers(
    tree: Path, overrides: tuple[str, ...] = (), parent_only: bool = False
) -> tuple[list[tuple[str, str]], int]:
    """Every url whose host is not a rendered Service, and how many were read."""
    documents = parent_render(tree, overrides)
    known = services(tuples_of(documents))
    if not parent_only:
        known |= services(deploy_side(tree))
    urls = broker_urls(documents)
    failures = [(owner, host) for owner, host in urls if service_name(host) not in known]
    return failures, len(urls)


def test_every_broker_url_names_a_rendered_service(working_tree: Path) -> None:
    """RESOLVES: every `nats://` host in P is a Service in P ∪ D."""
    failures, examined = unresolved_brokers(working_tree)
    print(f"[broker-url gate] {examined} nats:// url(s) examined against P ∪ D")
    assert examined >= MINIMUM_BROKER_URLS, (
        f"{examined} nats:// url(s) in the parent render; iam and gateway carry one each"
    )
    assert not failures, f"url(s) naming no rendered Service: {failures}"


def test_every_client_dials_the_parents_broker(working_tree: Path) -> None:
    """ON THE PARENT'S BROKER: step 6's second merge moved both clients."""
    failures, examined = unresolved_brokers(working_tree, parent_only=True)
    print(f"[broker-url gate] {examined} nats:// url(s) examined against P")
    assert examined >= MINIMUM_BROKER_URLS
    assert not failures, (
        f"url(s) dialling a broker the parent does not render: {failures}. "
        f"Step 6's third merge deletes `infra/nats.yaml`; these would dial nothing."
    )


def test_a_url_left_on_the_deleted_broker_reddens(tmp_path: Path) -> None:
    """B5's red case: the bump taken and ONE url line kept on `yadgar-nats`.

    ONE url, so the gate is shown to judge each url rather than the render.
    Both checks redden, because from 0.2.42 no side renders `yadgar-nats`.
    """
    tree = a_copy_of_the_tree(tmp_path)
    overrides = ("--set-string", "iam.nats.url=nats://yadgar-nats:4222")
    failures, examined = unresolved_brokers(tree, overrides)
    assert failures == [("Deployment/iam", "yadgar-nats")], (failures, examined)
    on_parent, _ = unresolved_brokers(tree, overrides, parent_only=True)
    assert on_parent == [("Deployment/iam", "yadgar-nats")], on_parent


# THE PIN B5 MOVES AWAY FROM, and the last one whose `platform` renders the
# broker as `yadgar-nats*`. Written here rather than read out of git, for the
# depth-1 reason the valkey constants above give.
PIN_BEFORE_B5 = "0.2.38"


def test_the_url_deletion_without_the_bump_reddens(tmp_path: Path) -> None:
    """The coupling's other half: B5's url deletion kept, its bump dropped.

    Both clients fall back to `nats://nats:4222` while the parent at 0.2.38
    renders only `Service/yadgar-nats`, so BOTH urls fail, by name.
    """
    tree = a_copy_of_the_tree(tmp_path)
    application = tree / "infra" / "yadgar-app.yaml"
    text = application.read_text()
    pinned = re.findall(r"^\s*targetRevision: (\d+\.\d+\.\d+)\s*$", text, re.MULTILINE)
    assert len(pinned) == 1, pinned
    application.write_text(text.replace(f"targetRevision: {pinned[0]}", f"targetRevision: {PIN_BEFORE_B5}"))
    failures, _ = unresolved_brokers(tree, parent_only=True)
    assert sorted(failures) == [("Deployment/gateway", "nats"), ("Deployment/iam", "nats")], failures


# D's `Service/yadgar-nats`, CONSTRUCTED rather than read off any file, so the
# pair below means the same thing at every pin. From B5 it is the one broker
# name P does NOT render, which is what lets D alone resolve it. A directory
# source, because that is the cheapest shape D reads, and only what the gate
# reads, for the reason the valkey constants above give.
DEPLOY_BROKER_APPLICATION = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: deploy-broker
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/yadgarhq/deploy
    targetRevision: main
    path: infra/deploy-broker
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
"""

DEPLOY_BROKER_SERVICE = """
apiVersion: v1
kind: Service
metadata:
  name: yadgar-nats
"""


def test_a_url_on_deploys_broker_resolves_but_is_not_the_parents(tmp_path: Path) -> None:
    """The discriminating pair: the same kind of url with D holding the Service.

    RESOLVES stays green, because D renders `Service/yadgar-nats`. ON THE
    PARENT'S BROKER reddens on exactly that url. A RESOLVES check that ignored
    D would redden here too, and one that ignored P would pass the case above.
    """
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "deploy-broker").mkdir()
    (tree / "infra" / "deploy-broker" / "service.yaml").write_text(DEPLOY_BROKER_SERVICE)
    (tree / "infra" / "deploy-broker-app.yaml").write_text(DEPLOY_BROKER_APPLICATION)
    overrides = ("--set-string", "gateway.nats.url=nats://yadgar-nats:4222")
    resolves, _ = unresolved_brokers(tree, overrides)
    assert resolves == [], resolves
    on_parent, _ = unresolved_brokers(tree, overrides, parent_only=True)
    assert on_parent == [("Deployment/gateway", "yadgar-nats")], on_parent


# ── THE NEXT HANDOVER IS AN ADOPTION, NOT A DELETE-THEN-CREATE ───────────────
# A same-name object handed from one Argo Application to another needs
# `argocd.argoproj.io/sync-options: Prune=false` on the SOURCE copy, live one
# merge ahead of the handover (`plans/the-one-application-install.md`, its
# standing rule). Argo prunes the losing Application's unprotected copy before
# the gaining Application adopts it: measured at step 3, where `valkey-ingress`
# came back with a new uid, and at step 6, where `deploy#64` kept
# `nats-ingress`'s uid by carrying the annotation first.
#
# THE TOGGLES OF THE NEXT HANDOVER, NOT THE CURRENT VALUES FILE. The set of
# guarded objects is MEASURED, never listed: it is D ∩ P with P rendered at the
# toggles the next step flips. Step 7's four `infra/tls/` objects were guarded
# by `deploy#68`, and step 8's `gateway-ingress` by `deploy#70`; each left with
# its step's merge.
#
# STEP 9 WAS THE LAST HANDOVER THIS LADDER HAS, so there is no next one. Its
# first merge (A5, ADR-0810) deleted `infra/bootstrap/`, the last `deploy`
# source in namespace `yadgar` the parent also renders. What `deploy` still
# holds there is `tls`'s preflight and `ClusterIssuer/yadgar-dev-ca`, which no
# parent toggle renders (register row C4). So `NEXT_HANDOVER` is EMPTY: the
# guard renders P at this organisation's own values, and both sets it counts
# are empty. The red cases below keep each branch of the guard exercised,
# because an empty expectation over an empty examination proves nothing.
#
# STEP 9's FIVE NAMES WERE HOOKS, NOT HANDOVERS. Measured 2026-09-27 at the
# pinned 0.3.7, the parent renders `Job/bootstrap-secrets`,
# `Job/admin-bootstrap-token`, and `ServiceAccount`, `Role` and `RoleBinding`
# `bootstrap-secrets` as helm hooks (`pre-install,pre-upgrade`, which Argo runs
# as `PreSync`). Argo does not track a hook, so `Prune=false` could not make
# step 9 an adoption. ADR-0810 rules the handover instead: this merge orphans
# the five live objects (the Application has no finalizer), hand step A5b in
# MIGRATION_NOTES.md deletes them, and step 9's second merge turns
# `platform.bootstrap.create` on so the hooks recreate them.
NEXT_HANDOVER: tuple[str, ...] = ()
EXPECTED_HANDOVERS = 0
EXPECTED_HOOK_HANDOVERS: frozenset[tuple[str, str, str]] = frozenset()

# THE FIVE NAMES STEP 9 RETIRES FROM `deploy`, as `(apiGroup, kind, name)`.
BOOTSTRAP_HOOK_NAMES = frozenset(
    {
        ("batch", "Job", "bootstrap-secrets"),
        ("batch", "Job", "admin-bootstrap-token"),
        ("", "ServiceAccount", "bootstrap-secrets"),
        ("rbac.authorization.k8s.io", "Role", "bootstrap-secrets"),
        ("rbac.authorization.k8s.io", "RoleBinding", "bootstrap-secrets"),
    }
)
HOOK_ANNOTATION = "helm.sh/hook"
PRUNE_FALSE = "Prune=false"


def unguarded_handovers(
    tree: Path, overrides: tuple[str, ...] = NEXT_HANDOVER
) -> tuple[list[tuple[str, tuple[str, str, str]]], int, set[tuple[str, str, str]]]:
    """Every D document the parent renders, TRACKED, at `overrides` without `Prune=false`.

    READS THE DOCUMENTS, NOT THE TUPLES, because the annotation lives on the
    source copy. RETURNS the count examined alongside, so an empty answer over
    zero handovers cannot pass as a guarded one, and the D tuples the parent
    renders only as HOOKS, which no annotation turns into an adoption.
    """
    tracked: set[tuple[str, str, str]] = set()
    hooks: set[tuple[str, str, str]] = set()
    for document in parent_render(tree, overrides):
        if not isinstance(document, dict):
            continue
        annotations = (document.get("metadata") or {}).get("annotations") or {}
        (hooks if HOOK_ANNOTATION in annotations else tracked).update(tuples_of([document]))
    missing: list[tuple[str, tuple[str, str, str]]] = []
    hooked: set[tuple[str, str, str]] = set()
    examined = 0
    for _, application in applications(tree):
        if (application.get("metadata") or {}).get("name") == PARENT_APPLICATION:
            continue
        path = ((application.get("spec") or {}).get("source") or {}).get("path")
        if not path:
            continue
        for manifest in sorted((tree / path).glob("*.yaml")):
            for document in yaml.safe_load_all(manifest.read_text()):
                found = tuples_of([document])
                if found and found <= hooks:
                    hooked |= found
                if not found or not found <= tracked:
                    continue
                examined += 1
                annotations = (document.get("metadata") or {}).get("annotations") or {}
                options = str(annotations.get("argocd.argoproj.io/sync-options", ""))
                if PRUNE_FALSE not in [option.strip() for option in options.split(",")]:
                    missing.append((str(manifest.relative_to(tree)), next(iter(found))))
    return sorted(missing), examined, hooked


def test_every_object_the_next_step_hands_over_is_never_pruned(working_tree: Path) -> None:
    """Every TRACKED object the next step hands over carries `Prune=false`."""
    missing, examined, _ = unguarded_handovers(working_tree)
    print(f"[handover guard] {examined} tracked object(s) the next step hands over")
    assert examined == EXPECTED_HANDOVERS, (
        f"the next step hands over {examined} tracked object(s); the measured "
        f"count is {EXPECTED_HANDOVERS}"
    )
    assert not missing, f"{len(missing)} handover(s) without {PRUNE_FALSE}: {missing}"


def test_the_next_step_hands_over_its_hooks_by_name(working_tree: Path) -> None:
    """The next step's hook-rendered names, named, not counted away. None after step 9."""
    _, _, hooked = unguarded_handovers(working_tree)
    print(f"[handover guard] {len(hooked)} object(s) the parent renders only as hooks")
    assert hooked == EXPECTED_HOOK_HANDOVERS, sorted(hooked ^ EXPECTED_HOOK_HANDOVERS)


RESTORED_GATEWAY_INGRESS = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-ingress
  namespace: yadgar
"""


def test_a_tracked_handover_without_the_annotation_reddens(tmp_path: Path) -> None:
    """Red case: a same-name TRACKED object in a surviving source, unannotated, is named."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "tls" / "gateway-ingress.yaml").write_text(RESTORED_GATEWAY_INGRESS)
    missing, examined, _ = unguarded_handovers(tree)
    assert examined == EXPECTED_HANDOVERS + 1
    assert missing == [
        (
            "infra/tls/gateway-ingress.yaml",
            ("networking.k8s.io", "NetworkPolicy", "gateway-ingress"),
        )
    ], missing


# ── STEP 9: THE RETIRED `bootstrap` APPLICATION, FOR THE RED CASES ───────────
# Only what the gate reads, for the reason the valkey constants above give.
RESTORED_BOOTSTRAP_APPLICATION = """
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: bootstrap
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/yadgarhq/deploy
    targetRevision: main
    path: infra/bootstrap
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
"""

RESTORED_BOOTSTRAP_JOB = """
apiVersion: batch/v1
kind: Job
metadata:
  name: bootstrap-secrets
"""

RESTORED_BOOTSTRAP_RBAC = """
apiVersion: v1
kind: ServiceAccount
metadata:
  name: bootstrap-secrets
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: bootstrap-secrets
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: bootstrap-secrets
---
apiVersion: batch/v1
kind: Job
metadata:
  name: admin-bootstrap-token
"""


def restore_bootstrap(tree: Path, whole: bool = True) -> None:
    """The retired `bootstrap` Application and its directory, or one Job of it."""
    (tree / "infra" / "bootstrap").mkdir()
    (tree / "infra" / "bootstrap" / "secrets.yaml").write_text(RESTORED_BOOTSTRAP_JOB)
    if whole:
        (tree / "infra" / "bootstrap" / "rbac.yaml").write_text(RESTORED_BOOTSTRAP_RBAC)
    (tree / "infra" / "bootstrap-app.yaml").write_text(RESTORED_BOOTSTRAP_APPLICATION)


def bootstrap_owners(
    tree: Path, overrides: tuple[str, ...] = ()
) -> tuple[set[tuple[str, str, str]], set[tuple[str, str, str]]]:
    """Which of step 9's five names D sources, and which P renders."""
    return (
        set(deploy_side(tree)) & BOOTSTRAP_HOOK_NAMES,
        parent_side(tree, overrides) & BOOTSTRAP_HOOK_NAMES,
    )


# ── STEP 9, SECOND MERGE: THE PARENT MINTS THE BOOTSTRAP SECRETS ─────────────
# ADR-0810: this organisation sets `platform.bootstrap.create` true and
# `platform.bootstrap.iamKeys.create` false, EXPLICITLY. The five names come
# back as the parent's hooks, never as tracked objects, so they are asserted
# here and not in `RETIRED_TUPLES` (see the comment there).
EXPECTED_BOOTSTRAP_HOOKS = 5
HOOK_PHASES = "pre-install,pre-upgrade"
HOOK_DELETE_POLICY = "before-hook-creation"

# WHAT EACH HOOK JOB MINTS, read off the `create <name> <<JSON` lines of its
# script. Measured 2026-09-27 at the pinned 0.3.7. `iam-keys` is absent on
# purpose: live iam 0.8.45 logs its key identity UNVERIFIED (ledger 1155), so
# the wrong-key refusal the parent's default `iamKeys.create: true` relies on
# is not armed here. `iam-keys` stays with `make secrets` and 1Password.
EXPECTED_MINTED = {
    "bootstrap-secrets": ("valkey-password", "nats-auth", "nats-auth-gateway"),
    "admin-bootstrap-token": ("admin-bootstrap-token",),
}
NEVER_MINTED = "iam-keys"
IAM_KEYS_KEY = "platform.bootstrap.iamKeys.create"
MINT_LINE = re.compile(r"^\s*create (\S+) <<JSON", re.MULTILINE)

# STEP 9's FLIP, REVERTED with `--set`, for K3. The flip is the ONLY value
# this merge moves (`adminToken.secretName` restates the parent default).
STEP_9_REVERTED: tuple[str, ...] = ("--set", "platform.bootstrap.create=false")


def hooks_of(documents: list) -> dict[tuple[str, str, str], dict]:
    """Every hook-annotated document, by `(apiGroup, kind, name)`, with its annotations."""
    found = {}
    for document in documents:
        if not isinstance(document, dict):
            continue
        annotations = (document.get("metadata") or {}).get("annotations") or {}
        if HOOK_ANNOTATION in annotations:
            for candidate in tuples_of([document]):
                found[candidate] = annotations
    return found


def minted(documents: list) -> dict[str, list[str]]:
    """The Secret names each Job's script creates, by Job name."""
    result: dict[str, list[str]] = {}
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != "Job":
            continue
        for container in document["spec"]["template"]["spec"]["containers"]:
            script = "\n".join(container.get("command") or []) + "\n" + "\n".join(
                container.get("args") or []
            )
            result.setdefault(document["metadata"]["name"], []).extend(MINT_LINE.findall(script))
    return result


def iam_keys_violations(tree: Path, overrides: tuple[str, ...] = ()) -> list[str]:
    """Why this tree would mint `iam-keys`, or nothing. Each reason names the key."""
    values = yaml.safe_load((tree / "infra" / "yadgar" / "values.yaml").read_text())
    stated = ((values.get("platform") or {}).get("bootstrap") or {}).get("iamKeys") or {}
    problems = []
    if "create" not in stated:
        problems.append(
            f"{IAM_KEYS_KEY} is not stated in infra/yadgar/values.yaml; the parent default is true"
        )
    elif stated["create"] is not False:
        problems.append(f"{IAM_KEYS_KEY} is {stated['create']!r}; ADR-0810 requires false")
    for job, names in minted(parent_render(tree, overrides)).items():
        if NEVER_MINTED in names:
            problems.append(f"Job/{job} mints {NEVER_MINTED} ({IAM_KEYS_KEY})")
    return problems


def test_the_parent_renders_every_bootstrap_name_as_a_hook(working_tree: Path) -> None:
    """The five names have ONE owner again: the parent, as PreSync hooks."""
    hooks = hooks_of(parent_render(working_tree))
    found = {t: hooks[t] for t in BOOTSTRAP_HOOK_NAMES if t in hooks}
    print(f"[step 9] {len(found)} of {EXPECTED_BOOTSTRAP_HOOKS} bootstrap name(s) are parent hooks")
    assert len(BOOTSTRAP_HOOK_NAMES) == EXPECTED_BOOTSTRAP_HOOKS
    assert set(found) == BOOTSTRAP_HOOK_NAMES, sorted(BOOTSTRAP_HOOK_NAMES - set(found))
    wrong = {
        t: a
        for t, a in found.items()
        if a.get(HOOK_ANNOTATION) != HOOK_PHASES
        or a.get("helm.sh/hook-delete-policy") != HOOK_DELETE_POLICY
    }
    assert not wrong, wrong
    in_deploy, _ = bootstrap_owners(working_tree)
    assert not in_deploy, sorted(in_deploy)


def test_the_flip_dropped_leaves_the_bootstrap_names_unowned(working_tree: Path) -> None:
    """Red case: step 9's deletion kept, its flip dropped. All five are unowned."""
    hooks = hooks_of(parent_render(working_tree, STEP_9_REVERTED))
    assert not (BOOTSTRAP_HOOK_NAMES & set(hooks)), sorted(BOOTSTRAP_HOOK_NAMES & set(hooks))


def test_step_9_adds_the_five_hooks_and_changes_nothing_else(working_tree: Path) -> None:
    """K3: the flip adds exactly the five hook objects and changes 0 others."""
    before = parent_render(working_tree, STEP_9_REVERTED)
    after = parent_render(working_tree)
    changes = field_changes(before, after)
    added = {(kind, name) for _, kind, name in BOOTSTRAP_HOOK_NAMES}
    print(f"[K3 step 9] {len(before)} -> {len(after)} object(s), {len(changes)} change(s)")
    assert changes == {(*o, "<object added or removed>") for o in added}, sorted(changes)


def test_the_hooks_mint_every_bootstrap_secret_but_iam_keys(working_tree: Path) -> None:
    """Each Job mints exactly its names; `iam-keys` is minted by none."""
    found = minted(parent_render(working_tree))
    print(f"[step 9] minted: {found}")
    assert {job: tuple(names) for job, names in found.items()} == EXPECTED_MINTED, found
    assert not iam_keys_violations(working_tree), iam_keys_violations(working_tree)


def test_iam_keys_create_true_mints_iam_keys(working_tree: Path) -> None:
    """Red case: `iamKeys.create=true` adds `iam-keys` to `bootstrap-secrets`' mint."""
    found = minted(parent_render(working_tree, ("--set", f"{IAM_KEYS_KEY}=true")))
    assert found["bootstrap-secrets"] == [*EXPECTED_MINTED["bootstrap-secrets"], NEVER_MINTED], found


def test_deleting_the_iam_keys_block_reddens_and_names_the_key(tmp_path: Path) -> None:
    """Red case: the explicit false removed, so the parent default (true) applies.

    The WHOLE `iamKeys` block is removed. Removing only its `create` line leaves
    `iamKeys:` null, and a null in values deletes the parent default rather
    than restating it, which is a different case.
    """
    tree = a_copy_of_the_tree(tmp_path)
    path = tree / "infra" / "yadgar" / "values.yaml"
    values = yaml.safe_load(path.read_text())
    del values["platform"]["bootstrap"]["iamKeys"]
    path.write_text(yaml.safe_dump(values))
    problems = iam_keys_violations(tree)
    assert len(problems) == 2 and all(IAM_KEYS_KEY in p for p in problems), problems
    assert any("Job/bootstrap-secrets mints iam-keys" in p for p in problems), problems


def admin_token_names(tree: Path, overrides: tuple[str, ...] = ()) -> dict[str, set[str]]:
    """Every place the admin bootstrap token's Secret is named, by where."""
    values = yaml.safe_load((tree / "infra" / "yadgar" / "values.yaml").read_text())
    documents = parent_render(tree, overrides)
    gateway = next(
        d for d in documents
        if isinstance(d, dict) and (d.get("kind"), d["metadata"]["name"]) == ("Deployment", "gateway")
    )
    mounted = {
        (volume.get("secret") or {}).get("secretName")
        for volume in gateway["spec"]["template"]["spec"].get("volumes") or []
        if volume.get("name") == "admin-bootstrap-token"
    }
    return {
        "values gateway.adminBootstrap.tokenSecret": {values["gateway"]["adminBootstrap"]["tokenSecret"]},
        "Job/admin-bootstrap-token mints": set(minted(documents)["admin-bootstrap-token"]),
        "Deployment/gateway mounts": mounted,
    }


def test_the_minted_admin_token_is_the_one_gateway_reads(working_tree: Path) -> None:
    """The hook mints the Secret named by `gateway.adminBootstrap.tokenSecret`."""
    names = admin_token_names(working_tree)
    print(f"[step 9] admin token: {names}")
    assert all(n == {"admin-bootstrap-token"} for n in names.values()), names


def test_a_renamed_admin_token_reddens(working_tree: Path) -> None:
    """Red case: the Job's name moved alone. The parent itself refuses to render.

    Measured at the pinned 0.3.7: `yadgar/templates/validate.yaml` refuses two
    different names while `platform.bootstrap.create` is true, and names both
    keys. So a mismatch cannot reach the cluster through this values file.
    """
    with pytest.raises(AssertionError, match=r"gateway\.adminBootstrap\.tokenSecret"):
        admin_token_names(
            working_tree, ("--set", "platform.bootstrap.adminToken.secretName=renamed")
        )


def test_restoring_the_bootstrap_application_reddens_the_rung(tmp_path: Path) -> None:
    """C2's red case for step 9: the Application and its directory put back.

    `len(D)` rises by exactly the five names, so C2 fails before C1 is read.
    """
    tree = a_copy_of_the_tree(tmp_path)
    restore_bootstrap(tree)
    in_deploy, _ = bootstrap_owners(tree)
    _, examined = two_owners(tree)
    assert in_deploy == BOOTSTRAP_HOOK_NAMES, sorted(in_deploy ^ BOOTSTRAP_HOOK_NAMES)
    assert examined == EXPECTED_DEPLOY_TUPLES + len(BOOTSTRAP_HOOK_NAMES), examined


def test_restoring_the_bootstrap_application_reddens_the_gate(tmp_path: Path) -> None:
    """C1's red case for step 9: with the flip in, a restored copy is two owners.

    All five names, each named with both owners. This is the state a revert of
    the first merge alone would create, and why that revert is valid only
    before this merge.
    """
    tree = a_copy_of_the_tree(tmp_path)
    restore_bootstrap(tree)
    failures, examined = two_owners(tree)
    assert {failure.tuple for failure in failures} == BOOTSTRAP_HOOK_NAMES, render(failures, examined)
    assert all("bootstrap" in failure.deploy_owner for failure in failures), render(failures, examined)


def test_a_hook_rendered_copy_is_named_by_the_guard(tmp_path: Path) -> None:
    """Red case for the guard's HOOK branch, which `NEXT_HANDOVER = ()` leaves idle.

    One Job file restored under its Application, with the parent rendering the
    bootstrap hooks: the guard names that Job as hook-rendered and counts it as
    no tracked handover.
    """
    tree = a_copy_of_the_tree(tmp_path)
    restore_bootstrap(tree, whole=False)
    missing, examined, hooked = unguarded_handovers(
        tree, ("--set", "platform.bootstrap.create=true")
    )
    assert hooked == {("batch", "Job", "bootstrap-secrets")}, sorted(hooked)
    assert (missing, examined) == ([], EXPECTED_HANDOVERS), (missing, examined)


# ── K3 OF STEP 7, ENCODED ────────────────────────────────────────────────────
# Two halves, and both are what `prune: true` and `selfHeal: true` would apply
# unattended.
#
# FIRST, THE VALUES MOVE ADDS THE FOUR EDGE OBJECTS AND CHANGES NOTHING ELSE.
# Step 7's keys, reverted with `--set`, against the current values: exactly
# the four objects differ, and all other objects are byte-identical. That
# includes `global.hostname`, and only because `iam.enrolment.gateway` is
# stated: with the hostname set and that key empty, iam derives its enrolment
# URL with NO port and `ENROLMENT_GATEWAY` loses `:18443` — the red case below.
# The reverted side unsets all four of step 7's value moves, the enrolment URL
# included, so it is the real pre-step values: the chart's no-hostname fallback
# then renders `:18443`, the value live `iam` carries. Measured 2026-09-27 at
# the pinned 0.3.7: 70 objects reverted, 74 now, 70 identical, 4 added. (The re-pin to
# 0.3.7 was the previous merge, gated on its own K3: two image digests.)
#
# SECOND, THE FOUR RENDERED OBJECTS EQUAL THE DELETED COPIES FIELD FOR FIELD,
# ignoring `metadata.namespace` (Argo supplies the destination) and
# `metadata.annotations` (`Prune=false` was on the copies only). A difference
# here is a live change on handover: the EnvoyProxy's placement or NodePort, the
# Gateway's listener, or the Certificate's issuer, which would reissue the edge
# leaf from another root. The copies are written out below rather than read from
# git, for the reason the red cases' subjects give: CI clones at depth 1.
STEP_7_REVERTED: tuple[str, ...] = (
    "--set",
    "platform.edgeTLS.create=false",
    "--set",
    "platform.gatewayListener.create=false",
    "--set",
    "global.hostname=",
    "--set",
    "iam.enrolment.gateway=",
)
STEP_7_OBJECTS = frozenset(
    {
        ("Certificate", "gateway-tls"),
        ("EnvoyProxy", "edge"),
        ("GatewayClass", "eg"),
        ("Gateway", "edge"),
    }
)
DELETED_EDGE_COPIES = """
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: gateway-tls
spec:
  secretName: gateway-tls
  dnsNames:
    - gateway.yadgar.internal
  commonName: gateway.yadgar.internal
  duration: 2160h
  renewBefore: 720h
  privateKey:
    algorithm: ECDSA
    size: 256
    rotationPolicy: Always
  usages:
    - server auth
  issuerRef:
    name: yadgar-dev-ca
    kind: ClusterIssuer
    group: cert-manager.io
---
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: EnvoyProxy
metadata:
  name: edge
spec:
  provider:
    type: Kubernetes
    kubernetes:
      envoyDeployment:
        replicas: 2
        pod:
          nodeSelector:
            node-role.kubernetes.io/control-plane: ""
          tolerations:
            - key: node-role.kubernetes.io/control-plane
              operator: Exists
              effect: NoSchedule
      envoyService:
        type: NodePort
        patch:
          type: StrategicMerge
          value:
            spec:
              ports:
                - port: 443
                  nodePort: 30443
---
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass
metadata:
  name: eg
spec:
  controllerName: gateway.envoyproxy.io/gatewayclass-controller
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: edge
spec:
  gatewayClassName: eg
  infrastructure:
    parametersRef:
      group: gateway.envoyproxy.io
      kind: EnvoyProxy
      name: edge
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      hostname: gateway.yadgar.internal
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: gateway-tls
      allowedRoutes:
        namespaces:
          from: Same
"""


def field_changes(before: list, after: list) -> set[tuple[str, str, str]]:
    """`(kind, name, field path)` for every field that differs, plus whole objects."""
    def index(documents):
        return {
            (d["kind"], d["metadata"]["name"]): d
            for d in documents
            if isinstance(d, dict) and d.get("kind")
        }

    changes: set[tuple[str, str, str]] = set()

    def walk(kind: str, name: str, old, new, path: str) -> None:
        if isinstance(old, dict) and isinstance(new, dict):
            for key in set(old) | set(new):
                walk(kind, name, old.get(key), new.get(key), f"{path}.{key}".lstrip("."))
        elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
            for position, (a, b) in enumerate(zip(old, new)):
                walk(kind, name, a, b, f"{path}[{position}]")
        elif old != new:
            changes.add((kind, name, path))

    old, new = index(before), index(after)
    for key in old.keys() | new.keys():
        if key not in old or key not in new:
            changes.add((*key, "<object added or removed>"))
        else:
            walk(*key, old[key], new[key], "")
    return changes


def edge_differences(rendered: list) -> set[tuple[str, str, str]]:
    """Fields where the rendered edge objects differ from the deleted copies."""
    def comparable(document: dict) -> dict:
        metadata = dict(document.get("metadata") or {})
        metadata.pop("namespace", None)
        metadata.pop("annotations", None)
        return {**document, "metadata": metadata}

    ours = [
        comparable(d)
        for d in rendered
        if isinstance(d, dict) and (d.get("kind"), (d.get("metadata") or {}).get("name"))
        in STEP_7_OBJECTS
    ]
    copies = [comparable(d) for d in yaml.safe_load_all(DELETED_EDGE_COPIES) if d]
    return field_changes(copies, ours)


def test_step_7_adds_the_four_edge_objects_and_changes_nothing_else(
    working_tree: Path,
) -> None:
    """K3, first half: the values move is exactly four added objects."""
    before = parent_render(working_tree, STEP_7_REVERTED)
    after = parent_render(working_tree)
    changes = field_changes(before, after)
    print(f"[K3 step 7] {len(before)} -> {len(after)} object(s), {len(changes)} change(s)")
    assert changes == {(*o, "<object added or removed>") for o in STEP_7_OBJECTS}, sorted(
        changes
    )


def test_the_four_edge_objects_equal_the_deleted_copies(working_tree: Path) -> None:
    """K3, second half: 0 differing fields over the four handed-over objects."""
    differences = edge_differences(parent_render(working_tree))
    print(f"[K3 step 7] {len(STEP_7_OBJECTS)} object(s) compared, {len(differences)} field(s) differ")
    assert not differences, sorted(differences)


def test_an_unpinned_nodeport_reddens_the_edge_comparison(working_tree: Path) -> None:
    """Red case: the NodePort pin dropped from values is named, and nothing else."""
    differences = edge_differences(
        parent_render(working_tree, ("--set", "platform.gatewayListener.envoyProxy.httpsNodePort=null"))
    )
    assert {kind for kind, _, _ in differences} == {"EnvoyProxy"}, differences
    assert any("patch" in field for _, _, field in differences), differences


def test_the_hostname_without_the_enrolment_port_reddens(working_tree: Path) -> None:
    """Red case: `global.hostname` alone moves iam's enrolment URL off `:18443`."""
    before = parent_render(working_tree, STEP_7_REVERTED)
    after = parent_render(working_tree, ("--set", "iam.enrolment.gateway="))
    unexpected = field_changes(before, after) - {
        (*o, "<object added or removed>") for o in STEP_7_OBJECTS
    }
    assert len(unexpected) == 1, unexpected
    ((kind, name, field),) = unexpected
    assert (kind, name) == ("Deployment", "iam") and ".env[" in field, unexpected


def test_restoring_a_deleted_edge_copy_reddens_the_gate(tmp_path: Path) -> None:
    """C1's red case for step 7: `tls` survives, so a restored file is sourced."""
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "tls" / "gatewayclass.yaml").write_text(
        "apiVersion: gateway.networking.k8s.io/v1\nkind: GatewayClass\n"
        "metadata:\n  name: eg\n"
    )
    failures, examined = two_owners(tree)
    assert {failure.tuple for failure in failures} == {
        ("gateway.networking.k8s.io", "GatewayClass", "eg"),
    }, render(failures, examined)
    assert examined == EXPECTED_DEPLOY_TUPLES + 1, render(failures, examined)


# ── K3 OF STEP 8, ENCODED ────────────────────────────────────────────────────
# FIRST, the values move adds `NetworkPolicy/gateway-ingress` and changes
# nothing else: 74 objects reverted, 75 now, 74 identical, measured 2026-09-27
# at the pinned 0.3.7. SECOND, the rendered policy's SPEC equals the deleted
# copy's field for field. Its metadata differs by one label the gateway chart
# stamps (`app: gateway`), which selects nothing and admits nothing; namespace
# and annotations are ignored for the reasons step 7's comparison gives.
STEP_8_REVERTED: tuple[str, ...] = ("--set", "gateway.networkPolicy.enabled=false")
DELETED_GATEWAY_INGRESS = """
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-ingress
spec:
  podSelector:
    matchLabels: { app: gateway }
  policyTypes: [Ingress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: envoy-gateway-system
          podSelector:
            matchLabels:
              app.kubernetes.io/name: envoy
              gateway.envoyproxy.io/owning-gateway-name: edge
              gateway.envoyproxy.io/owning-gateway-namespace: yadgar
      ports:
        - protocol: TCP
          port: 8080
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: observability
      ports:
        - protocol: TCP
          port: 9090
"""
EXPECTED_GATEWAY_INGRESS_METADATA_CHANGES = frozenset(
    {("NetworkPolicy", "gateway-ingress", "metadata.labels")}
)


def gateway_ingress_differences(rendered: list) -> set[tuple[str, str, str]]:
    """Fields where the rendered `gateway-ingress` differs from the deleted copy."""
    def comparable(document: dict) -> dict:
        metadata = dict(document.get("metadata") or {})
        metadata.pop("namespace", None)
        metadata.pop("annotations", None)
        return {**document, "metadata": metadata}

    ours = [
        comparable(d)
        for d in rendered
        if isinstance(d, dict)
        and d.get("kind") == "NetworkPolicy"
        and (d.get("metadata") or {}).get("name") == "gateway-ingress"
    ]
    return field_changes([comparable(yaml.safe_load(DELETED_GATEWAY_INGRESS))], ours)


def test_step_8_adds_the_policy_and_changes_nothing_else(working_tree: Path) -> None:
    """K3, first half: the values move is exactly one added object."""
    before = parent_render(working_tree, STEP_8_REVERTED)
    after = parent_render(working_tree)
    changes = field_changes(before, after)
    print(f"[K3 step 8] {len(before)} -> {len(after)} object(s), {len(changes)} change(s)")
    assert changes == {("NetworkPolicy", "gateway-ingress", "<object added or removed>")}, sorted(
        changes
    )


def test_the_rendered_policy_equals_the_deleted_copy(working_tree: Path) -> None:
    """K3, second half: the spec is field-equal; only the chart's label differs."""
    differences = gateway_ingress_differences(parent_render(working_tree))
    print(f"[K3 step 8] 1 policy compared, {len(differences)} field(s) differ: {sorted(differences)}")
    assert differences == EXPECTED_GATEWAY_INGRESS_METADATA_CHANGES, sorted(differences)


def test_a_narrowed_peer_reddens_the_policy_comparison(working_tree: Path) -> None:
    """Red case: a scrape namespace moved in values is named as a spec field."""
    differences = gateway_ingress_differences(
        parent_render(working_tree, ("--set", "gateway.networkPolicy.scrapeFrom.namespace=monitoring"))
    )
    extra = differences - EXPECTED_GATEWAY_INGRESS_METADATA_CHANGES
    assert extra and all(field.startswith("spec.ingress") for _, _, field in extra), differences
