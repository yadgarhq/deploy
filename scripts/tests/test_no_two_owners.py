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
EXPECTED_DEPLOY_TUPLES = 15

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
    # Application. Only this one tuple is listed: `infra/nats.yaml`'s five
    # objects leave at the third merge, and the parent at 0.2.38 renders them
    # as `yadgar-nats*`, so they are successors under other names rather than
    # the same tuples, and no entry here could say they are in P.
    "network-policies/nats-ingress.yaml": frozenset(
        {("networking.k8s.io", "NetworkPolicy", "nats-ingress")}
    ),
    # STEP 6, THIRD MERGE, AND WHY IT ADDS NO ENTRY. `infra/nats.yaml`'s five
    # tuples — `StatefulSet`, `Service`, `PodDisruptionBudget` `nats`,
    # `Service/nats-headless`, `ConfigMap/nats-config` — are NOT in P: the
    # parent at 0.2.38 renders the broker as `yadgar-nats*`. An entry here
    # would redden on every one of them, correctly, because nothing takes those
    # NAMES over; the live objects are orphaned, not handed over, and B4b of
    # `plans/the-one-application-install.md` deletes them by hand.
    #
    # WHAT THIS DICT GUARDS FOR OTHER STEPS, THE BROKER-URL GATE BELOW GUARDS
    # HERE: the question that matters is not "does P render `nats`" but "does
    # any client still dial it". Every `nats://` host in P must be a Service P
    # renders, so a url left on `nats` reddens this merge rather than leaving
    # a client with no broker.
}

# THE COUNT THE ZERO-OWNERS TEST ASSERTS, written down so that an entry dropped
# from the dict above reddens rather than shrinks what is examined. 13 from
# step 5 and 1 from step 6's first merge.
EXPECTED_RETIRED_TUPLES = 14


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
    """Red case: this merge's deletion kept, its flip dropped.

    Exactly `nats-ingress` goes unowned, and nothing from step 5, so the case
    isolates this merge. With no owner the broker's pods would have NO ingress
    policy at all — every source admitted on 4222, 8222 and 6222.
    """
    missing = orphans(
        a_copy_of_the_tree(tmp_path),
        overrides=("--set", "platform.nats.create=false"),
    )
    assert {candidate for _, candidate in missing} == RETIRED_TUPLES[
        "network-policies/nats-ingress.yaml"
    ], missing


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
    """Red case for RESOLVES: step 6's third merge with one url forgotten.

    ONE url, so the gate is shown to judge each url rather than the render.
    """
    tree = a_copy_of_the_tree(tmp_path)
    # `missing_ok`, so the case still runs after the third merge deletes it.
    (tree / "infra" / "nats.yaml").unlink(missing_ok=True)
    failures, examined = unresolved_brokers(
        tree, overrides=("--set-string", "iam.nats.url=nats://nats:4222")
    )
    assert failures == [("Deployment/iam", "nats")], (failures, examined)


# D's `Service/nats`, CONSTRUCTED rather than read off `infra/nats.yaml`, so
# the pair below means the same thing before and after the third merge deletes
# that file. A directory source, because that is the cheapest shape D reads, and
# only what the gate reads, for the reason the valkey constants above give.
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
  name: nats
"""


def test_a_url_on_deploys_broker_resolves_but_is_not_the_parents(tmp_path: Path) -> None:
    """The discriminating pair: the same kind of url with D holding `Service/nats`.

    RESOLVES stays green, because D renders `Service/nats`. ON THE PARENT'S
    BROKER reddens on exactly that url. A RESOLVES check that ignored D would
    redden here too, and one that ignored P would pass the case above.
    """
    tree = a_copy_of_the_tree(tmp_path)
    (tree / "infra" / "deploy-broker").mkdir()
    (tree / "infra" / "deploy-broker" / "service.yaml").write_text(DEPLOY_BROKER_SERVICE)
    (tree / "infra" / "deploy-broker-app.yaml").write_text(DEPLOY_BROKER_APPLICATION)
    overrides = ("--set-string", "gateway.nats.url=nats://nats:4222")
    resolves, _ = unresolved_brokers(tree, overrides)
    assert resolves == [], resolves
    on_parent, _ = unresolved_brokers(tree, overrides, parent_only=True)
    assert on_parent == [("Deployment/gateway", "nats")], on_parent
