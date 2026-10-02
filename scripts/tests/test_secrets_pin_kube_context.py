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

THE GATE: every logical `kubectl`/`helm` invocation inside the six pinned
targets' recipe bodies must carry its context flag, and the gate resolves
`$(SECRET_APPLY)` back to its own definition first — a bare
`$(SECRET_APPLY)` token carries no literal `kubectl` for the naive per-line
scan to see, so dropping the flag from `SECRET_APPLY`'s definition alone
would otherwise pass unnoticed.

`status` IS IN SCOPE, WITH ONE DELIBERATE EXCEPTION. Its `kubectl get
applications`/`kubectl get nodes` calls read Secret-adjacent cluster state
exactly like the other five targets' calls do, and are pinned the same way.
Its FIRST line, `kubectl config current-context`, is not and must not be —
that line's entire purpose is to report the AMBIENT context, so pinning it
would make `status` unable to ever print "context: NOT kind-yadgar". `config`
is exempt the same way `helm repo add` is: it is a kubectl subcommand that
touches no cluster at all (it only reads the local kubeconfig file), so it is
not a context-sensitive invocation regardless of which target it sits in.
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

PINNED_TARGETS = ("secrets", "bootstrap", "ui", "password", "sync", "status")

# kubectl subcommands that touch no cluster at all — the only way a
# `kubectl` segment is exempt from carrying `--context`. `config` reads the
# local kubeconfig file; `status`'s first line exists ONLY to report that
# file's current context, so pinning it would defeat the target's purpose.
_KUBECTL_LOCAL_SUBCOMMANDS = frozenset({"config"})

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


# `kubectl` needs `--context`, `helm` needs `--kube-context`. `helm` is
# always a candidate at the match stage; whether a given invocation actually
# needs the flag is decided by `_helm_is_local_only` below, INVERTED: every
# `helm` segment needs `--kube-context` UNLESS its subcommand is proven
# local-only. A subcommand this list has never heard of is NOT exempt —
# the default is "needs the flag", not "assume it's safe".
_BINARY_FLAGS = {
    "kubectl": "--context",
    "helm": "--kube-context",
}
_INVOCATION_RE = re.compile(r"\b(kubectl|helm)\b")

# helm subcommands that touch no cluster at all — the only way a `helm`
# segment is exempt from carrying `--kube-context`.
_HELM_LOCAL_SUBCOMMANDS = frozenset(
    {
        "repo",
        "search",
        "show",
        "template",
        "version",
        "plugin",
        "env",
        "dependency",
        "package",
        "lint",
        "completion",
        "pull",
        "create",
        "verify",
    }
)


def _first_subcommand(segment_after_binary: str) -> str | None:
    """The first non-flag token after the binary, skipping recognised flags AND their values.

    `helm --namespace argocd upgrade ...` must read `upgrade` as the
    subcommand, not `argocd` (the flag's value) or `--namespace` itself — a
    flag taking a SEPARATE-token value is the common case global flags use
    (`--namespace`, `--kube-context`, `--kubeconfig`, `--repo`, `--context`,
    and any other `--flag value` pair), so a bare "skip tokens starting with
    -" would stop at `--namespace` and then read its value as the subcommand
    instead. A `--flag=value` form is self-contained and only costs one
    token.
    """
    tokens = segment_after_binary.split()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-"):
            if "=" in token:
                i += 1
                continue
            # Space-separated `--flag value`: consume the value too, unless
            # the next token is itself another flag (a bare boolean flag).
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        return token
    return None


def _helm_is_local_only(segment: str) -> bool:
    """Whether a `helm ...` segment's subcommand is in the local-only set.

    Unknown or absent subcommand -> NOT local-only (the inverted default:
    prove safety, do not assume it).
    """
    after = segment[segment.index("helm") + len("helm") :]
    subcommand = _first_subcommand(after)
    return subcommand in _HELM_LOCAL_SUBCOMMANDS


def _kubectl_is_local_only(segment: str) -> bool:
    """Whether a `kubectl ...` segment's subcommand is in the local-only set.

    Unlike helm (mostly local), kubectl is mostly cluster-touching — `config`
    is the one subcommand this Makefile actually uses that is not.
    """
    after = segment[segment.index("kubectl") + len("kubectl") :]
    subcommand = _first_subcommand(after)
    return subcommand in _KUBECTL_LOCAL_SUBCOMMANDS


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


_COMMAND_START_TOKENS = ("|", "$(", "`", ";", "&&", "||", "(")


def _starts_a_command(line: str, match_start: int) -> bool:
    """Whether `match_start` begins a new command rather than sitting mid-token.

    A bare `\\bhelm\\b` also matches inside an unrelated token at a word
    boundary — the literal URL `https://argoproj.github.io/argo-helm` ends in
    `helm` right after a `-`, which is non-word, so `\\b` fires there too.

    Make's own recipe-prefix characters (`@` silences echo, `-` ignores a
    non-zero exit) sit between the leading tab and the real command — a line
    reading `\\t@kubectl ...` measured as NOT starting a command without this,
    because `@` is not whitespace and matches none of `_COMMAND_START_TOKENS`,
    so `status`'s two `@`-prefixed lines passed through unseen entirely
    rather than being flagged for the missing `--context` they actually had.
    """
    before = line[:match_start].rstrip()
    if before.strip(" \t@-") == "":
        return True
    return any(before.endswith(tok) for tok in _COMMAND_START_TOKENS)


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
        starts = [
            (m.start(), m.group(1))
            for m in _INVOCATION_RE.finditer(line)
            if _starts_a_command(line, m.start())
        ]
        if not starts:
            continue
        for i, (start, binary) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(line)
            segment = line[start:end]
            if binary == "helm" and _helm_is_local_only(segment):
                continue
            if binary == "kubectl" and _kubectl_is_local_only(segment):
                continue
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


def _minimal_tree_with_bootstrap_line(tmp_path: Path, extra_line: str) -> Path:
    """A throwaway tree whose `bootstrap:` target is just `extra_line`.

    Isolates the helm-subcommand classifier from the real Makefile's
    content, so a red case tests exactly the one line it names.
    """
    tree = tmp_path / "deploy"
    tree.mkdir()
    (tree / "Makefile").write_text(f"KUBE_CONTEXT := kind-yadgar\n\nbootstrap:\n\t{extra_line}\n")
    return tree


def test_helm_flag_with_separate_value_does_not_hide_the_subcommand(tmp_path: Path) -> None:
    """`helm --namespace argocd upgrade ...`: `argocd` is the flag's VALUE, not the subcommand.

    A classifier that stops at the first non-`-`-prefixed token would read
    `argocd` (the `--namespace` value) as the subcommand, find it in neither
    the local nor any other known set, and — under the inverted default —
    still redden correctly for the WRONG reason. This pins the right reason:
    the subcommand is `upgrade`, which is unambiguously not local-only.
    """
    tree = _minimal_tree_with_bootstrap_line(
        tmp_path, "helm --namespace argocd upgrade --install argocd argo/argo-cd"
    )
    offenders = invocations_missing_context(tree)
    assert offenders, "an unpinned `helm --namespace argocd upgrade ...` should have reddened"
    assert "upgrade" in offenders[0], offenders


def test_unpinned_helm_uninstall_reddens(tmp_path: Path) -> None:
    """`helm uninstall` is not in the local-only set — it deletes a release from a cluster."""
    tree = _minimal_tree_with_bootstrap_line(tmp_path, "helm uninstall argocd -n argocd")
    offenders = invocations_missing_context(tree)
    assert offenders, "an unpinned `helm uninstall` should have reddened"


def test_unpinned_helm_rollback_reddens(tmp_path: Path) -> None:
    """`helm rollback` is not in the local-only set — it mutates a release in a cluster."""
    tree = _minimal_tree_with_bootstrap_line(tmp_path, "helm rollback argocd 1 -n argocd")
    offenders = invocations_missing_context(tree)
    assert offenders, "an unpinned `helm rollback` should have reddened"


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
