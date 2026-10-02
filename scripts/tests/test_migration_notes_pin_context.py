"""Every copy-pasteable kubectl/helm-cluster/argocd command in MIGRATION_NOTES.md is pinned.

THIS HOST'S DEFAULT KUBECONFIG CONTEXT IS PRODUCTION. A fenced command block in
this file is written to be copy-pasted by a human at a terminal, and nothing
about the block itself says which cluster it runs against — that comes ENTIRELY
from whatever `kubectl config current-context` happens to read at paste time.
Measured reddening cases this gate exists to catch: `kubectl -n yadgar delete
secret project-db-tls` (a mutation), `kubectl -n yadgar get secret iam-keys`
(a Secret-data read), `kubectl -n yadgar rollout restart deployment/project-db`
(a mutation) — none of these refuse on a context mismatch, and each reads
identically whether it ran against `kind-yadgar` or against production.

THE FIX: every `kubectl` invocation in a fenced block carries `--context
kind-yadgar`; every CLUSTER-TOUCHING `helm` invocation carries `--kube-context
kind-yadgar` (`helm repo add`/`template`/`lint`/… touch no cluster and need
neither); an `argocd` invocation that is not purely local
(`version`/`completion`) carries either `--context kind-yadgar` or a throwaway
`KUBECONFIG=` on the same line — `argocd --core` reads the kubeconfig's
CURRENT context directly to reach the API server inside the cluster, bypassing
`argocd-server`, so a throwaway, single-context kubeconfig is the form that
makes it safe rather than merely likely-correct.

THE GATE scans every fenced code block in the file (language-tagged or bare —
a reader copy-pastes from the rendered block, not from its info string) and
flags any non-comment line whose first binary-shaped word is `kubectl`, a
cluster-touching `helm` subcommand, or a non-local `argocd` subcommand, that
does not carry its required flag.

HISTORICAL BLOCKS ARE MARKED, NOT GUESSED AT. A block that documents a
completed, one-time action and is not meant to be re-run carries
`HISTORICAL_MARKER` (see below) on the line immediately before its opening
fence. The gate skips a block ONLY on that explicit marker — there is
deliberately no heuristic ("this looks like a past-tense section", "this is
under a `## already done` heading") that could silently exempt a block nobody
actually reviewed for safety. As of this gate's introduction, zero blocks in
this file are marked historical: every command here was judged copy-pasteable
today and pinned instead.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
MIGRATION_NOTES = REPOSITORY / "MIGRATION_NOTES.md"

HISTORICAL_MARKER = "<!-- historical: not pinned, do not re-run -->"

_FENCE_RE = re.compile(r"^\s*```")

# helm subcommands that touch no cluster — see the Makefile's own
# test_secrets_pin_kube_context.py for the shared reasoning. Kept here too
# rather than imported: this gate reads prose, that one reads the Makefile,
# and the two files should be free to diverge if either binary's subcommand
# list ever does.
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

_ARGOCD_LOCAL_SUBCOMMANDS = frozenset({"version", "completion", "help"})


def _first_word_after(line: str, start: int) -> str:
    """The first whitespace-delimited word beginning at or after `start`."""
    match = re.match(r"\s*(\S+)", line[start:])
    return match.group(1) if match else ""


_COMMAND_START_TOKENS = ("|", "$(", "`", ";", "&&", "||", "(")


def _starts_a_command(raw: str, match_start: int) -> bool:
    """Whether `match_start` begins a new command rather than sitting mid-argument.

    `-n argocd` is the namespace flag's VALUE, not an invocation of the
    `argocd` CLI — this repository names its own Argo CD namespace `argocd`,
    so a bare `\\bargocd\\b` scan flags every `kubectl -n argocd ...` line
    measured, which is nearly a third of the file. A binary only counts as
    invoked at the true start of a (sub)command: line start, or right after
    a pipe, command substitution, backtick, `;`, `&&`, `||` or `(`.
    """
    before = raw[:match_start].rstrip()
    if before == "":
        return True
    return any(before.endswith(tok) for tok in _COMMAND_START_TOKENS)


def _blocks(text: str) -> list[tuple[int, list[str], bool]]:
    """Every fenced block as (start_line, code_lines, exempt).

    `exempt` is True only when the line immediately before the opening fence
    is exactly `HISTORICAL_MARKER` — blank lines in between do NOT count, so
    the marker has to sit right next to the block it exempts and cannot drift
    onto the wrong one as the file is edited.
    """
    lines = text.splitlines()
    blocks: list[tuple[int, list[str], bool]] = []
    i = 0
    while i < len(lines):
        if _FENCE_RE.match(lines[i]):
            exempt = i > 0 and lines[i - 1].strip() == HISTORICAL_MARKER
            start = i + 1
            body: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            blocks.append((start, body, exempt))
        i += 1
    return blocks


def unpinned_invocations(path: Path = MIGRATION_NOTES) -> list[tuple[int, str]]:
    """Every (line number, text) of an unpinned kubectl/helm-cluster/argocd invocation."""
    text = path.read_text()
    offenders: list[tuple[int, str]] = []
    for start, body, exempt in _blocks(text):
        if exempt:
            continue
        for offset, raw in enumerate(body):
            lineno = start + offset
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for m in re.finditer(r"\b(kubectl|helm|argocd)\b", raw):
                if not _starts_a_command(raw, m.start()):
                    # Not an invocation: either a substring of an unrelated
                    # token (`argo-helm` in a URL contains `helm` at a word
                    # boundary) or, far more commonly here, a flag's VALUE
                    # (`-n argocd`, this namespace's own name).
                    continue
                binary = m.group(1)
                after = raw[m.end() :]
                if binary == "kubectl":
                    if "--context" not in after and "--context" not in raw[: m.start()]:
                        offenders.append((lineno, raw.strip()))
                elif binary == "helm":
                    sub = _first_word_after(raw, m.end())
                    if sub in _HELM_LOCAL_SUBCOMMANDS:
                        continue
                    if "--kube-context" not in after:
                        offenders.append((lineno, raw.strip()))
                elif binary == "argocd":
                    sub = _first_word_after(raw, m.end())
                    if sub in _ARGOCD_LOCAL_SUBCOMMANDS:
                        continue
                    if "--context" not in after and "KUBECONFIG=" not in raw:
                        offenders.append((lineno, raw.strip()))
    return offenders


def test_every_cluster_command_in_migration_notes_is_pinned() -> None:
    """No fenced kubectl/helm-cluster/argocd command relies on the ambient context."""
    offenders = unpinned_invocations()
    assert offenders == [], (
        f"{len(offenders)} unpinned cluster command(s) in MIGRATION_NOTES.md — this "
        f"host's default kubeconfig context is PRODUCTION, so a copy-pasted command "
        f"with no --context runs there by default: {offenders}"
    )


def test_a_dropped_kubectl_context_reddens(tmp_path: Path) -> None:
    """Mutation check: strip one --context from a pinned kubectl line; the gate names it."""
    text = MIGRATION_NOTES.read_text()
    anchor = "kubectl --context kind-yadgar -n yadgar delete secret project-db-tls"
    assert anchor in text, "the project-db-tls delete line moved — update this test"
    mutated = text.replace(anchor, "kubectl -n yadgar delete secret project-db-tls", 1)
    assert mutated != text
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(mutated)
    offenders = unpinned_invocations(path)
    assert any("delete secret project-db-tls" in o[1] for o in offenders), offenders


def test_a_marked_historical_block_is_exempt(tmp_path: Path) -> None:
    """An explicitly marked block is skipped; an unmarked copy of the same text is not."""
    marked = (
        f"{HISTORICAL_MARKER}\n"
        "```bash\n"
        "kubectl -n yadgar delete secret ancient-tls\n"
        "```\n"
    )
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(marked)
    assert unpinned_invocations(path) == [], "the historical marker did not exempt its block"

    unmarked = marked.replace(f"{HISTORICAL_MARKER}\n", "")
    path.write_text(unmarked)
    offenders = unpinned_invocations(path)
    assert offenders, "the same block with no marker should have reddened and did not"


def test_a_misplaced_marker_does_not_exempt_the_next_block(tmp_path: Path) -> None:
    """The marker must sit directly before its block — a blank line between breaks it.

    Proves the marker cannot drift onto covering a block nobody actually put
    it there for, which is the whole reason it is checked by adjacency rather
    than by, say, "the nearest marker above this point in the file".
    """
    text = (
        f"{HISTORICAL_MARKER}\n"
        "\n"
        "```bash\n"
        "kubectl -n yadgar delete secret ancient-tls\n"
        "```\n"
    )
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "a blank line between the marker and the fence should not exempt the block"


def test_helm_repo_add_needs_no_kube_context(tmp_path: Path) -> None:
    """`helm repo add`/`template` touch no cluster — not false positives."""
    text = "```bash\nhelm repo add argo https://argoproj.github.io/argo-helm\nhelm template foo ./chart\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    assert unpinned_invocations(path) == []


def test_unpinned_helm_upgrade_reddens(tmp_path: Path) -> None:
    """A cluster-touching helm subcommand with no --kube-context is caught."""
    text = "```bash\nhelm upgrade --install argocd argo/argo-cd\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "an unpinned `helm upgrade` should have reddened and did not"
