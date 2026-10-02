"""Every `kubectl` in the `secrets` and `bootstrap` recipes carries `--context`.

`SECRET_APPLY` and every `kubectl create secret` / `kubectl create namespace`
/ `kubectl apply` line in these two targets relied on whatever context the
operator's shell happened to have current. `kubectl config current-context`
not reading `kind-yadgar` at the moment `make secrets` or `make bootstrap`
runs writes a Secret, a namespace or the root Application into the WRONG
cluster, silently — none of these commands refuses on a context mismatch,
and a Secret that writes to production reads exactly like one that writes to
`kind-yadgar`.

THE FIX: a single `KUBE_CONTEXT ?= kind-yadgar` variable, referenced as
`--context $(KUBE_CONTEXT)` on every `kubectl` invocation inside `secrets:`
and `bootstrap:`. `?=` rather than `:=` so a caller can override it for a
different cluster without editing the file.

THE GATE: every logical `kubectl` invocation inside the `secrets:` and
`bootstrap:` recipe bodies must carry `--context`. Scoped to those two
targets deliberately — `status`, `ui`, `password` and `sync` read the
AMBIENT context on purpose (`status` even prints whether it is
`kind-yadgar`), and widening this gate to the whole file would make `status`
itself a false positive.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

PINNED_TARGETS = ("secrets", "bootstrap")


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


_KUBECTL_INVOCATION_RE = re.compile(r"\bkubectl\b")


def kubectl_invocations_missing_context(tree: Path) -> list[str]:
    """Every `kubectl` invocation in `secrets`/`bootstrap` with no `--context`.

    A line can carry more than one `kubectl` invocation (a `create ... |
    kubectl apply ...` pipeline), so this checks each `kubectl`-starting
    segment independently rather than the logical line as a whole — a
    pipeline with `--context` on only one side must still be named.
    """
    text = (tree / "Makefile").read_text()
    offenders = []
    for line in _target_bodies(text, PINNED_TARGETS):
        # Split the line into kubectl-led segments: each segment starts at a
        # `kubectl` token and runs to the next one (or end of line), since a
        # pipe can chain two kubectl invocations on one logical line.
        starts = [m.start() for m in _KUBECTL_INVOCATION_RE.finditer(line)]
        if not starts:
            continue
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(line)
            segment = line[start:end]
            if "--context" not in segment:
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


def test_every_kubectl_in_secrets_and_bootstrap_pins_context(working_tree: Path) -> None:
    """No `kubectl` in `secrets:` or `bootstrap:` relies on the ambient context."""
    offenders = kubectl_invocations_missing_context(working_tree)
    assert offenders == [], (
        f"{len(offenders)} kubectl invocation(s) in `secrets`/`bootstrap` carry no "
        f"--context, so they write to whatever cluster the shell's ambient context "
        f"names rather than a pinned one: {offenders}"
    )


def test_a_dropped_context_flag_reddens(tmp_path: Path) -> None:
    """Mutation check: strip one --context flag; the gate names the exposed line.

    Targets the `iam-keys` create line specifically, inside `secrets:` — the
    FIRST `--context $(KUBE_CONTEXT)` in the file is `SECRET_APPLY`'s own
    definition, outside any target body, which this gate does not scope to.
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
    offenders = kubectl_invocations_missing_context(tree)
    assert offenders, "stripping one --context flag did not redden the gate"


def test_kube_context_var_is_overridable(working_tree: Path) -> None:
    """KUBE_CONTEXT is declared with `?=`, so a caller can override it."""
    text = (working_tree / "Makefile").read_text()
    assert re.search(r"^KUBE_CONTEXT\s*\?=\s*kind-yadgar\s*$", text, re.MULTILINE), (
        "expected `KUBE_CONTEXT ?= kind-yadgar` — `?=` lets a caller override it "
        "for a different cluster without editing the file; `:=` would not"
    )
