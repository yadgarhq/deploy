"""Every Secret-writing pipeline in the Makefile applies server-side (ledger 1225).

`make secrets` and `make bootstrap`'s `github-scm` line load four Secrets —
`yadgar-dev-ca`, `iam-keys`, `estate-runner-github` and `github-scm` —
through `kubectl create secret ... --dry-run=client -o yaml | kubectl apply
-f -`. CLIENT-SIDE apply stamps the result with the
`kubectl.kubernetes.io/last-applied-configuration` annotation, which holds a
byte-for-byte copy of the object it just wrote — for a Secret, that is the
plaintext payload sitting in the cluster a SECOND time, readable by anyone
who can read the Secret's metadata. Ledger 1222 found and manually stripped
that annotation from four live Secrets (`github-scm`, `yadgar-dev-ca`,
`estate-runner-github`, `iam-keys`); this ledger (1225) is the fix that
stops a `make secrets` / `make bootstrap` rerun from re-adding it.

THE FIX: every one of the four `kubectl create secret` pipelines now ends in
`$(SECRET_APPLY)`, a Makefile variable defined once as `kubectl apply
--server-side --field-manager=yadgar-deploy --force-conflicts -f -`.
`--force-conflicts` is NOT needed merely because every Secret this Makefile
writes already exists on a bootstrapped cluster, owned by a PRIOR,
non-server-side manager (`kubectl-create` for `yadgar-dev-ca`,
`kubectl-client-side-apply` for the other three, measured via
`managedFields` on kind-yadgar, 2026-10-02) — measured against a live
`iam-keys` Secret, re-applying the SAME value server-side with no
`--force-conflicts` succeeds and makes `yadgar-deploy` a co-owner alongside
`kubectl-client-side-apply`, no conflict. `--force-conflicts` is needed for
the ROTATION case `make secrets`'s own comment already documents as
intended behaviour: a CHANGED value 409s against the field the prior
manager still owns, and `--force-conflicts` is what lets the rotation win
(measured the same way, with a differing dummy value). Switching to
server-side apply does NOT retroactively strip a `last-applied-configuration`
annotation a Secret already carries — that one-time strip is ledger 1222.

THE GATE: every `kubectl ... create secret ...` pipeline's RESOLVED
destination (following `$(SECRET_APPLY)` back to its definition, so the
gate reads what actually runs, not the variable's name) must carry
`--server-side`. A destination that resolves to plain `kubectl apply -f -`
— client-side — reddens this gate, by content rather than by line number,
so a renamed target or a reordered recipe is still caught.

NON-SECRET applies — the two `kubectl create namespace` lines and the
`kubectl apply -f $(ARGOCD_REPO)/projects/root.yaml` line — are
DELIBERATELY left on client-side apply: they hold no secret data, and
widening `$(SECRET_APPLY)` to cover them is out of this gate's scope.
`test_non_secret_applies_still_client_side` locks that down.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

SECRET_APPLY_VAR = "SECRET_APPLY"


def _join_continuations(text: str) -> list[str]:
    """Collapse Makefile backslash-newline continuations into logical lines.

    A recipe command spanning several `\\`-terminated physical lines reaches
    the shell as one line; this reconstructs that so a `create secret`
    opening one physical line and its `| kubectl apply ...` destination
    several lines later are read as a single statement, the same way the
    shell sees them. Comment lines never end in a trailing `\\` here, so
    they pass through untouched.
    """
    logical: list[str] = []
    buffer = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n")
        if buffer:
            line = buffer + " " + line.strip()
            buffer = ""
        if line.endswith("\\"):
            buffer = line[:-1].rstrip()
            continue
        logical.append(line)
    if buffer:
        logical.append(buffer)
    return logical


def _code_lines(text: str) -> list[str]:
    """Logical lines with comments and Makefile variable assignments dropped.

    Both this repository's rationale comments and `SECRET_APPLY`'s own
    definition contain the substrings `create secret` / `kubectl apply` in
    prose — excluding `#`-led lines and `NAME := ...` assignments keeps the
    gate reading only what the recipes actually run.
    """
    lines = []
    for line in _join_continuations(text):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*(:=|\?=|=)", stripped):
            continue
        lines.append(line)
    return lines


_SECRET_APPLY_ASSIGNMENT_RE = re.compile(
    rf"^(?:override\s+)?{SECRET_APPLY_VAR}\s*(?:\+=|\?=|:=|=)\s*(.*)$",
    re.MULTILINE,
)


def _secret_apply_assignments(text: str) -> list[str]:
    """Every assignment to SECRET_APPLY, any operator, in file order."""
    return [m.group(1).strip() for m in _SECRET_APPLY_ASSIGNMENT_RE.finditer(text)]


def _secret_apply_variable_value(text: str) -> str | None:
    """The literal command `$(SECRET_APPLY)` expands to, or None if undeclared.

    GNU Make resolves a `$(...)` reference to the variable's LAST assignment
    in the file, regardless of which operator (`=`, `:=`, `?=`, `+=`, with or
    without `override`) that assignment used — a second `SECRET_APPLY := ...`
    appended later silently becomes the value `make` actually uses. Picking
    the first match (as a plain `re.search` would) can therefore resolve to
    a stale, already-overridden value while reporting it as the live one.
    This asserts there is exactly ONE assignment so a second one fails loudly
    here instead of resolving quietly to whatever Make would really use.
    """
    assignments = _secret_apply_assignments(text)
    if not assignments:
        return None
    assert len(assignments) == 1, (
        f"expected exactly one {SECRET_APPLY_VAR} assignment, found "
        f"{len(assignments)}: {assignments} — GNU Make resolves `$(...)` to "
        "the LAST assignment regardless of operator, so an extra one "
        "silently changes what this variable expands to without any of "
        "these pipelines' source lines changing"
    )
    return assignments[0]


def secret_apply_destinations(tree: Path) -> list[str]:
    """The resolved pipe destination of every `kubectl ... create secret ...` line."""
    text = (tree / "Makefile").read_text()
    secret_apply_value = _secret_apply_variable_value(text)
    destinations = []
    for line in _code_lines(text):
        if "create secret" not in line or "|" not in line:
            continue
        destination = line.rsplit("|", 1)[1].strip()
        if destination == f"$({SECRET_APPLY_VAR})" and secret_apply_value:
            destination = secret_apply_value
        destinations.append(destination)
    return destinations


_SECRET_KIND_RE = re.compile(r"^\s*kind:\s*Secret\b.*$", re.MULTILINE)


def secret_kind_literals(tree: Path) -> list[str]:
    """Every inline `kind: Secret` manifest literal anywhere in the Makefile.

    The four known Secret writes are all `kubectl create secret ...`
    imperative command lines — never a YAML literal — so
    `secret_apply_destinations`'s `"create secret" in line` check is blind
    by design to a `cat <<-'YAML' | kubectl apply -f -` heredoc (or an
    applied, referenced manifest file) whose body declares `kind: Secret`.
    That shape writes a Secret through a path the substring-based gate never
    looks at, and would pass `test_every_secret_write_is_server_side` even
    fully client-side. This catches that shape directly, by forbidding the
    literal outside the four pipelines already covered, rather than trying
    to parse heredoc bodies into the same destination-resolution logic.
    """
    text = (tree / "Makefile").read_text()
    return _SECRET_KIND_RE.findall(text)


def non_secret_apply_destinations(tree: Path) -> list[str]:
    """Every OTHER `kubectl apply` destination: the namespace creates and root.yaml.

    Anything that runs `kubectl apply` but does not create a Secret.
    """
    text = (tree / "Makefile").read_text()
    destinations = []
    for line in _code_lines(text):
        if "create secret" in line:
            continue
        # `kubectl(?:\s+--context\s+\S+)?\s+apply\b` tolerates the
        # `--context $(KUBE_CONTEXT)` pin every kubectl invocation in
        # `secrets`/`bootstrap` now carries (ledger 1228 follow-up) — this
        # gate is about the DESTINATION'S apply mode, not the flags beside it.
        if "|" in line and re.search(r"kubectl(?:\s+--context\s+\S+)?\s+apply\b", line.rsplit("|", 1)[1]):
            destinations.append(line.rsplit("|", 1)[1].strip())
        elif re.search(r"kubectl(?:\s+--context\s+\S+)?\s+apply\b", line):
            destinations.append(line.strip())
    return destinations


def a_copy_of_the_tree(tmp_path: Path) -> Path:
    """The repository, copied, so no red case can touch the working tree."""
    tree = tmp_path / "deploy"
    shutil.copytree(REPOSITORY, tree, ignore=shutil.ignore_patterns(".git"))
    return tree


@pytest.fixture(scope="module")
def working_tree() -> Path:
    """The repository itself. Read only; every red case copies it first."""
    return REPOSITORY


def test_every_secret_write_is_server_side(working_tree: Path) -> None:
    """No `kubectl create secret | kubectl apply` pipeline writes client-side."""
    destinations = secret_apply_destinations(working_tree)
    assert destinations, "found no `create secret` pipeline at all — update this gate's parser"
    offenders = [d for d in destinations if "--server-side" not in d]
    assert offenders == [], (
        f"{len(offenders)} Secret-writing pipeline(s) still apply client-side, which "
        f"re-adds `kubectl.kubernetes.io/last-applied-configuration` (ledger 1225): {offenders}"
    )


def test_four_secrets_are_covered(working_tree: Path) -> None:
    """Locks the count: yadgar-dev-ca, iam-keys, estate-runner-github, github-scm."""
    destinations = secret_apply_destinations(working_tree)
    assert len(destinations) == 4, (
        "expected 4 Secret-writing pipelines (yadgar-dev-ca, iam-keys, "
        f"estate-runner-github, github-scm), found {len(destinations)}: {destinations}"
    )


def test_non_secret_applies_still_client_side(working_tree: Path) -> None:
    """The two namespace creates and the root.yaml apply are untouched by this fix."""
    destinations = non_secret_apply_destinations(working_tree)
    assert destinations, "found no non-Secret `kubectl apply` at all — update this gate's parser"
    switched = [d for d in destinations if "--server-side" in d]
    assert switched == [], (
        f"non-Secret apply(s) unexpectedly switched to --server-side, out of this "
        f"fix's scope: {switched}"
    )


def test_a_reverted_secret_pipe_reddens(tmp_path: Path) -> None:
    """Mutation check: put one Secret pipeline back on client-side apply; the gate names it."""
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    mutated = text.replace("| $(SECRET_APPLY)", "| kubectl apply -f -", 1)
    assert mutated != text, "no `$(SECRET_APPLY)` destination found to mutate — update this test"
    makefile.write_text(mutated)
    offenders = [d for d in secret_apply_destinations(tree) if "--server-side" not in d]
    assert offenders == ["kubectl apply -f -"]


def test_secret_apply_var_undefined_reddens(tmp_path: Path) -> None:
    """Mutation check: delete `SECRET_APPLY`'s definition; every reference stops resolving to server-side."""
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    mutated = re.sub(rf"^{SECRET_APPLY_VAR}\s*:=.*\n", "", text, count=1, flags=re.MULTILINE)
    assert mutated != text, "the SECRET_APPLY definition line is gone — update the regex"
    makefile.write_text(mutated)
    offenders = [d for d in secret_apply_destinations(tree) if "--server-side" not in d]
    assert len(offenders) == 4, (
        "removing the SECRET_APPLY definition should un-resolve every "
        f"`$(SECRET_APPLY)` destination back to a literal, non-server-side string: {offenders}"
    )


def test_secret_apply_assigned_twice_reddens(tmp_path: Path) -> None:
    """Mutation check: a second SECRET_APPLY assignment must fail loudly, not resolve silently.

    GNU Make resolves `$(SECRET_APPLY)` to the LAST `:=`/`=`/`?=`/`+=`
    assignment in the file — confirmed against a real `make` run, appending
    a client-side `SECRET_APPLY := kubectl apply -f -` after the real
    definition makes `make` print the client-side command, not the
    server-side one. Picking the first match instead (as a plain
    `re.search` would) would keep resolving every pipeline to the original,
    now-overridden, server-side value and report the tree as clean while a
    real `make secrets` run would write every Secret client-side.
    """
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    mutated = text + f"\n{SECRET_APPLY_VAR} := kubectl apply -f -\n"
    assert mutated != text
    makefile.write_text(mutated)
    with pytest.raises(AssertionError, match="exactly one"):
        secret_apply_destinations(tree)


def test_no_secret_manifest_literal_in_makefile(working_tree: Path) -> None:
    """No inline `kind: Secret` manifest exists anywhere in the Makefile outside the four known pipelines."""
    matches = secret_kind_literals(working_tree)
    assert matches == [], (
        f"found {len(matches)} inline `kind: Secret` manifest literal(s) in the Makefile, "
        "outside the four `kubectl create secret` pipelines this gate already checks — "
        f"route it through $(SECRET_APPLY) and extend this gate instead: {matches}"
    )


def test_a_secret_heredoc_reddens(tmp_path: Path) -> None:
    """Mutation check: a client-side Secret heredoc is invisible to the substring gate, but not to this one.

    `secret_apply_destinations` only looks at lines containing the literal
    substring `create secret`, so a Makefile target writing a Secret through
    `cat <<-'YAML' | kubectl apply -f -` with a `kind: Secret` body passes
    `test_every_secret_write_is_server_side` untouched — this confirms that
    blind spot still exists, then confirms `secret_kind_literals` catches
    the same heredoc directly.
    """
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    heredoc_target = (
        "\nbad-secret:\n"
        "\tcat <<-'YAML' | kubectl apply -f -\n"
        "\tapiVersion: v1\n"
        "\tkind: Secret\n"
        "\tmetadata:\n"
        "\t  name: sneaky\n"
        "\tYAML\n"
    )
    makefile.write_text(text + heredoc_target)
    # The old, substring-based gate stays blind: still exactly 4 pipelines,
    # none flagged, the heredoc never counted at all.
    destinations = secret_apply_destinations(tree)
    assert len(destinations) == 4
    offenders = [d for d in destinations if "--server-side" not in d]
    assert offenders == [], "if this fails, the substring gate started seeing the heredoc on its own"
    # The new gate catches it directly.
    matches = secret_kind_literals(tree)
    assert matches, "secret_kind_literals failed to catch the client-side Secret heredoc"
