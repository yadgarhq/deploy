"""Every `kubectl`/`helm` in the ambient-context-sensitive targets is pinned.

`SECRET_APPLY` and every `kubectl create secret` / `kubectl create namespace`
/ `kubectl apply` / `helm upgrade --install` line in `secrets`, `bootstrap`,
`ui`, `password` and `sync` relied on whatever context the operator's shell
happened to have current. None of these commands refuses on a context
mismatch, and a Secret, a namespace, the root Application or an Argo CD
install that lands in production reads exactly like one that landed in
`kind-yadgar`.

THE FIX: a single `KUBE_CONTEXT := kind-yadgar` variable, referenced as
`--context $(KUBE_CONTEXT)` on every `kubectl` invocation and
`--kube-context $(KUBE_CONTEXT)` on every `helm` invocation in those five
targets.

`:=` RATHER THAN `?=`, DELIBERATELY. `?=` only assigns when the variable has
NO value yet, and GNU Make treats an inherited environment variable as
already having a value before a single line of the Makefile runs — so
`KUBE_CONTEXT=prod-x make secrets` with `?=` silently retargets every pinned
command at `prod-x`, measured: `KUBE_CONTEXT=prod-x make -n secrets` put
`prod-x` in all twelve `kubectl`/`helm` invocations. `:=` does not read the
environment at all, so only a COMMAND-LINE override
(`make KUBE_CONTEXT=<ctx> secrets`, which Make always lets win over any
in-file assignment) can retarget it — an exported shell variable cannot.

THE GATE: every logical `kubectl`/`helm` invocation inside the five pinned
targets' recipe bodies must carry its context flag, and the gate resolves
`$(SECRET_APPLY)` back to its own definition first — a bare
`$(SECRET_APPLY)` token carries no literal `kubectl` for the naive per-line
scan to see, so dropping the flag from `SECRET_APPLY`'s definition alone
would otherwise pass unnoticed. Scoped to the five named targets
deliberately — `status` reads the AMBIENT context ON PURPOSE (it prints
whether it is `kind-yadgar`), and widening this gate to the whole file would
make `status` itself a false positive.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

PINNED_TARGETS = ("secrets", "bootstrap", "ui", "password", "sync")

# Shared with test_secrets_server_side_apply.py's own constant — both read
# the same Makefile variable name.
SECRET_APPLY_VAR = "SECRET_APPLY"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_secrets_server_side_apply import (  # noqa: E402  (path set above)
    _secret_apply_variable_value,
)


def _join_continuations(text: str) -> list[str]:
    """Collapse Makefile backslash-newline continuations into logical lines.

    Same reconstruction `test_secrets_server_side_apply.py` uses: a recipe
    command spanning several `\\`-terminated physical lines reaches the
    shell as one line, so a `kubectl` opening one physical line and its
    `--context` several lines later (or nowhere) are read as one statement.
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


_TARGET_HEADER_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:(?!=)")


def _target_bodies(text: str, names: tuple[str, ...]) -> list[str]:
    """Logical lines belonging to the named targets' recipes, comments dropped.

    A target's recipe is every line following its `name:` header up to (but
    not including) the next line that starts a new target — found by the
    same `name:` shape, excluding `:=`/`?=`/`+=` assignments, which this
    regex's negative lookahead already excludes.
    """
    lines = _join_continuations(text)
    collected: list[str] = []
    current: str | None = None
    for line in lines:
        header = _TARGET_HEADER_RE.match(line)
        if header:
            current = header.group(1)
            continue
        if current in names:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                collected.append(line)
    return collected


# Each entry: the binary's own word-boundary pattern, and the flag its
# invocations must carry. Order matters only for the error message; the
# segment split below finds BOTH binaries in one pass.
#
# `helm` ALONE is too wide: `helm repo add`/`helm repo update` manage the
# local repo cache file and touch no cluster, so `--kube-context` on them is
# meaningless and would be a false positive. The lookahead scopes `helm` in
# only when followed (optionally through its own `--kube-context` flag, so a
# ALREADY-pinned line still matches its start) by a subcommand that actually
# talks to a cluster — `upgrade`/`install`, the two this Makefile runs.
_BINARY_FLAGS = {
    "kubectl": "--context",
    "helm": "--kube-context",
}
_INVOCATION_RE = re.compile(
    r"\b(kubectl|helm(?=(?:\s+--kube-context\s+\S+)?\s+(?:upgrade|install)\b))\b"
)


def _expand_secret_apply(line: str, tree: Path) -> str:
    """Replace a literal `$(SECRET_APPLY)` token with its resolved value.

    A bare `$(SECRET_APPLY)` reference carries no literal `kubectl`, so the
    segment scan below would walk straight past a `SECRET_APPLY` definition
    that dropped its own `--context` flag. Expanding the token first is what
    makes that definition reachable by the same per-segment check every
    other invocation gets, rather than needing a second, parallel check.
    """
    if f"$({SECRET_APPLY_VAR})" not in line:
        return line
    text = (tree / "Makefile").read_text()
    value = _secret_apply_variable_value(text)
    if not value:
        return line
    return line.replace(f"$({SECRET_APPLY_VAR})", value)


def invocations_missing_context(tree: Path) -> list[str]:
    """Every `kubectl`/`helm` invocation in the five pinned targets with no context flag.

    A line can carry more than one invocation (a `create ... | kubectl apply
    ...` pipeline, or a `$(SECRET_APPLY)` destination once expanded), so this
    checks each binary-led segment independently rather than the logical
    line as a whole — a pipeline with the flag on only one side must still
    be named.
    """
    text = (tree / "Makefile").read_text()
    offenders = []
    for raw_line in _target_bodies(text, PINNED_TARGETS):
        line = _expand_secret_apply(raw_line, tree)
        starts = [(m.start(), m.group(1)) for m in _INVOCATION_RE.finditer(line)]
        if not starts:
            continue
        for i, (start, binary) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(line)
            segment = line[start:end]
            if _BINARY_FLAGS[binary] not in segment:
                offenders.append(segment.strip())
    return offenders


def a_copy_of_the_tree(tmp_path: Path) -> Path:
    """The repository, copied, so no red case can touch the working tree."""
    tree = tmp_path / "deploy"
    shutil.copytree(REPOSITORY, tree, ignore=shutil.ignore_patterns(".git"))
    return tree


@pytest.fixture(scope="module")
def working_tree() -> Path:
    """The repository itself. Read only; every red case copies it first."""
    return REPOSITORY


def test_every_invocation_in_pinned_targets_carries_context(working_tree: Path) -> None:
    """No `kubectl`/`helm` in the five pinned targets relies on the ambient context."""
    offenders = invocations_missing_context(working_tree)
    assert offenders == [], (
        f"{len(offenders)} invocation(s) in {PINNED_TARGETS} carry no context flag, "
        f"so they run against whatever cluster the shell's ambient context or "
        f"kubeconfig current-context names rather than a pinned one: {offenders}"
    )


def test_a_dropped_context_flag_reddens(tmp_path: Path) -> None:
    """Mutation check: strip one --context flag; the gate names the exposed line.

    Targets the `iam-keys` create line specifically, inside `secrets:` — the
    FIRST `--context $(KUBE_CONTEXT)` in the file is `SECRET_APPLY`'s own
    definition, outside any target body, which the per-line scan does not
    reach directly (it reaches it only via `_expand_secret_apply`).
    """
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    anchor = "create secret generic iam-keys"
    assert anchor in text, "the iam-keys create line moved — update this test"
    mutated = text.replace(
        f"kubectl --context $(KUBE_CONTEXT) {anchor}",
        f"kubectl {anchor}",
        1,
    )
    assert mutated != text, "no `--context $(KUBE_CONTEXT)` found to strip — update this test"
    makefile.write_text(mutated)
    offenders = invocations_missing_context(tree)
    assert offenders, "stripping one --context flag did not redden the gate"


def test_a_dropped_helm_kube_context_reddens(tmp_path: Path) -> None:
    """Mutation check: strip `--kube-context` from the argocd helm install."""
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    anchor = "helm --kube-context $(KUBE_CONTEXT) upgrade --install argocd argo/argo-cd"
    assert anchor in text, "the argocd helm install line moved — update this test"
    mutated = text.replace(anchor, "helm upgrade --install argocd argo/argo-cd", 1)
    assert mutated != text
    makefile.write_text(mutated)
    offenders = invocations_missing_context(tree)
    assert any("helm" in o for o in offenders), (
        f"stripping --kube-context from the argocd install did not redden the gate: {offenders}"
    )


def test_a_dropped_secret_apply_context_reddens(tmp_path: Path) -> None:
    """Mutation check: strip `--context` from SECRET_APPLY's own definition.

    `$(SECRET_APPLY)` carries no literal `kubectl` at its call sites, so this
    proves the gate actually resolves the variable rather than only ever
    seeing a harmless bare token.
    """
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    anchor = f"{SECRET_APPLY_VAR} := kubectl --context $(KUBE_CONTEXT) apply"
    assert anchor in text, "the SECRET_APPLY definition moved — update this test"
    mutated = text.replace(anchor, f"{SECRET_APPLY_VAR} := kubectl apply", 1)
    assert mutated != text
    makefile.write_text(mutated)
    offenders = invocations_missing_context(tree)
    assert offenders, (
        "dropping --context from SECRET_APPLY's own definition did not redden the "
        "gate — a bare $(SECRET_APPLY) token must still resolve to this check"
    )


def test_kube_context_is_immediate_not_conditional(working_tree: Path) -> None:
    """KUBE_CONTEXT is declared with `:=`, not `?=`.

    `?=` reads as "already set" the moment an environment variable of the
    same name exists, so an EXPORTED `KUBE_CONTEXT` would silently retarget
    every pinned command — `:=` ignores the environment entirely, leaving
    only an explicit `make KUBE_CONTEXT=<ctx> ...` command-line override,
    which GNU Make always lets win regardless of the in-file operator.
    """
    text = (working_tree / "Makefile").read_text()
    assert re.search(r"^KUBE_CONTEXT\s*:=\s*kind-yadgar\s*$", text, re.MULTILINE), (
        "expected `KUBE_CONTEXT := kind-yadgar` — `?=` would let an exported "
        "shell variable silently override it; `:=` does not read the "
        "environment, so only `make KUBE_CONTEXT=<ctx> ...` on the command "
        "line can retarget it"
    )


def _make_dry_run(tree: Path, target: str, env_overrides: dict[str, str]) -> str:
    """`make -n <target>` output against the copied tree, with extra env vars.

    `-n` (dry run) prints every recipe line without executing a single one —
    safe even for a mutated copy whose recipe would otherwise touch a
    cluster. The subprocess inherits this process's environment PLUS
    `env_overrides`, which is how an exported (not command-line) variable is
    simulated.
    """
    result = subprocess.run(
        ["make", "-n", target],
        cwd=tree,
        env={**os.environ, **env_overrides},
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout + result.stderr


def test_an_exported_kube_context_cannot_retarget_secrets(working_tree: Path) -> None:
    """`KUBE_CONTEXT=prod-x make -n secrets` must not put prod-x anywhere.

    The exact shape this gate exists to close: an operator's shell happens
    to have `KUBE_CONTEXT` exported from an unrelated session (a different
    repo's `.envrc`, a copy-pasted export). With `:=` that export is inert.
    """
    output = _make_dry_run(working_tree, "secrets", {"KUBE_CONTEXT": "prod-x"})
    assert "prod-x" not in output, (
        f"an exported KUBE_CONTEXT=prod-x leaked into `make -n secrets`:\n{output}"
    )
    assert "kind-yadgar" in output, (
        f"`make -n secrets` with KUBE_CONTEXT exported lost the pin entirely:\n{output}"
    )


def test_a_reverted_colon_equals_lets_the_export_through(tmp_path: Path) -> None:
    """Mutation check: put `?=` back; the exported-override red case returns.

    Proves `test_an_exported_kube_context_cannot_retarget_secrets` is
    actually discriminating — run against a `?=` copy, the SAME exported
    `KUBE_CONTEXT=prod-x` must leak through.
    """
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    mutated = text.replace("KUBE_CONTEXT := kind-yadgar", "KUBE_CONTEXT ?= kind-yadgar", 1)
    assert mutated != text, "no `KUBE_CONTEXT := kind-yadgar` found — update this test"
    makefile.write_text(mutated)
    output = _make_dry_run(tree, "secrets", {"KUBE_CONTEXT": "prod-x"})
    assert "prod-x" in output, (
        "reverting to `?=` should have let the exported KUBE_CONTEXT=prod-x "
        f"leak through, and it did not:\n{output}"
    )


def test_command_line_override_still_wins_with_colon_equals(working_tree: Path) -> None:
    """`make KUBE_CONTEXT=<ctx> secrets` still overrides `:=`, command-line beats file."""
    result = subprocess.run(
        ["make", "-n", "KUBE_CONTEXT=staging-y", "secrets"],
        cwd=working_tree,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = result.stdout + result.stderr
    assert "staging-y" in output, (
        "a command-line `make KUBE_CONTEXT=<ctx> ...` override should always win over "
        f"an in-file `:=`, per GNU Make precedence, and it did not:\n{output}"
    )
    assert "kind-yadgar" not in output, (
        f"the command-line override did not fully replace the default:\n{output}"
    )
