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
`--force-conflicts` matters here specifically: every Secret this Makefile
writes already exists on a bootstrapped cluster, previously applied
client-side, so the `kubectl-client-side-apply` field manager owns every
field already. Without `--force-conflicts` the first server-side apply
after this change 409s instead of taking over.

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


def _secret_apply_variable_value(text: str) -> str | None:
    """The literal command `$(SECRET_APPLY)` expands to, or None if undeclared."""
    match = re.search(rf"^{SECRET_APPLY_VAR}\s*:=\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


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


def non_secret_apply_destinations(tree: Path) -> list[str]:
    """Every OTHER `kubectl apply` destination: the namespace creates and root.yaml.

    Anything that runs `kubectl apply` but does not create a Secret.
    """
    text = (tree / "Makefile").read_text()
    destinations = []
    for line in _code_lines(text):
        if "create secret" in line:
            continue
        if "|" in line and re.search(r"kubectl\s+apply\b", line.rsplit("|", 1)[1]):
            destinations.append(line.rsplit("|", 1)[1].strip())
        elif re.search(r"kubectl\s+apply\b", line):
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
    print(f"[secrets server-side] {len(destinations)} Secret pipeline(s): {destinations}")
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
