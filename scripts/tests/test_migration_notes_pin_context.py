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
(`version`/`completion`/`help`) carries either `--kube-context kind-yadgar` or
a throwaway `KUBECONFIG=` on the same line — NOT `argocd`'s own `--context`,
which selects a named SERVER/login alias from `~/.config/argocd/config` and
has nothing to do with which Kubernetes cluster is reached. `argocd --core`
reads the kubeconfig's CURRENT context directly to talk to the API server
inside the cluster, bypassing `argocd-server`, which is why `--kube-context`
(or a throwaway, single-context kubeconfig) is the form that makes it safe
rather than merely likely-correct.

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

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
MIGRATION_NOTES = REPOSITORY / "MIGRATION_NOTES.md"

HISTORICAL_MARKER = "<!-- historical: not pinned, do not re-run -->"

# CommonMark accepts both fence styles; a renderer treats them identically,
# so a reader copy-pastes from a `~~~`-fenced block exactly as readily.
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

# helm subcommands that touch no cluster — see yadgarhq/argocd's
# test_secrets_pin_kube_context.py (the Makefile and this reasoning both
# moved there in argocd#59; deploy#85 only deleted this repository's own
# copy afterward) for the shared reasoning. Kept here too rather than
# imported: this gate reads prose, that one reads a Makefile this
# repository no longer carries, and the two files should be free to diverge
# if either binary's subcommand list ever does.
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


_COMMAND_START_TOKENS = ("|", "$(", "`", ";", "&&", "||", "(", "{")

# Shell keywords that introduce a new command in their own right (the word
# itself carries no punctuation a token-suffix check could see), and wrapper
# commands that run their trailing argv as a command rather than consuming it
# as data. `env FOO=bar kubectl ...` and `sudo kubectl ...` are the same
# shape: the wrapper's last word before the real binary is irrelevant, only
# that ONE of these words precedes it with nothing else in between matters.
_COMMAND_START_WORDS = frozenset(
    {"do", "then", "else", "xargs", "watch", "sudo", "env", "time"}
)
_WORD_BEFORE_RE = re.compile(r"(\S+)\s*$")
_VAR_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*\s*$")


def _starts_a_command(raw: str, match_start: int) -> bool:
    """Whether `match_start` begins a new command rather than sitting mid-argument.

    `-n argocd` is the namespace flag's VALUE, not an invocation of the
    `argocd` CLI — this repository names its own Argo CD namespace `argocd`,
    so a bare `\\bargocd\\b` scan flags every `kubectl -n argocd ...` line
    measured, which is nearly a third of the file. A binary only counts as
    invoked at the true start of a (sub)command: line start; right after a
    pipe, command substitution, backtick, `;`, `&&`, `||`, `(`, `{` or `!`
    (negation, which is a token but not a word, hence checked the same way
    as the punctuation set); right after a shell keyword that opens a new
    command (`do`/`then`/`else`) or a wrapper that runs its argv as a command
    rather than consuming it (`xargs`/`watch`/`sudo`/`env`/`time`); or after
    zero or more `VAR=value` assignments (`env FOO=bar kubectl ...` and the
    `env`-less `FOO=bar kubectl ...` are both valid shell, and `env`'s own
    assignments sit BETWEEN it and the real binary, not immediately before
    either).
    """
    before = raw[:match_start].rstrip()
    if before == "":
        return True
    if any(before.endswith(tok) for tok in _COMMAND_START_TOKENS) or before.endswith("!"):
        return True
    stripped = before
    while True:
        match = _VAR_ASSIGNMENT_RE.search(stripped)
        if not match:
            break
        stripped = stripped[: match.start()].rstrip()
    if stripped != before and (stripped == "" or any(stripped.endswith(tok) for tok in _COMMAND_START_TOKENS)):
        return True
    word = _WORD_BEFORE_RE.search(stripped)
    return bool(word and word.group(1) in _COMMAND_START_WORDS)


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


def _strip_comment(line: str) -> str:
    """The line with a trailing `# comment` removed, quote-aware.

    A `#` inside a single- or double-quoted string is data, not a comment —
    none of this file's commands happen to quote one, but a scanner that
    assumed otherwise would be an accident waiting to happen. Stops at the
    first UNQUOTED `#`.

    A `#` STARTS A COMMENT ONLY AT THE START OF A WORD, matching bash: `echo
    nixpkgs#x && echo ran` prints `ran` (the `#` is data, mid-word), but `echo
    nixpkgs #x && echo ran` does not (the `#` is a comment, preceded by
    whitespace). `nix shell nixpkgs#x` and `https://x.example/#frag` both
    carry a literal, non-comment `#` — treating every unquoted `#` as a
    comment start truncated the line right there, hiding an unpinned command
    chained after it with `&&`.
    """
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (i == 0 or line[i - 1].isspace()):
            return line[:i]
    return line


def unpinned_invocations(path: Path = MIGRATION_NOTES) -> list[tuple[int, str]]:
    """Every (line number, text) of an unpinned kubectl/helm-cluster/argocd invocation.

    Checked per SEGMENT, not per line. A line can chain more than one
    invocation (`kubectl ... | kubectl ...`), and checking "does --context
    appear anywhere on this line" lets an earlier, correctly-pinned
    invocation excuse a later, unpinned one on the same pipeline — measured:
    `kubectl --context kind-yadgar ... | kubectl -n other apply -f -` passed
    whole, with the second `kubectl` naming no context of its own. Each
    invocation's segment runs from its own start to the NEXT invocation's
    start (or end of line), AFTER stripping any trailing `# comment` — a
    `--kube-context` mentioned only in a trailing comment is not one the
    shell will see, and previously excused the command beside it.
    """
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
            line = _strip_comment(raw)
            starts = [
                m for m in re.finditer(r"\b(kubectl|helm|argocd)\b", line) if _starts_a_command(line, m.start())
            ]
            for i, m in enumerate(starts):
                binary = m.group(1)
                seg_end = starts[i + 1].start() if i + 1 < len(starts) else len(line)
                segment = line[m.start() : seg_end]
                after = line[m.end() : seg_end]
                if binary == "kubectl":
                    if "--context" not in after:
                        offenders.append((lineno, raw.strip()))
                elif binary == "helm":
                    sub = _first_word_after(segment, len("helm"))
                    if sub in _HELM_LOCAL_SUBCOMMANDS:
                        continue
                    if "--kube-context" not in after:
                        offenders.append((lineno, raw.strip()))
                elif binary == "argocd":
                    sub = _first_word_after(segment, len("argocd"))
                    if sub in _ARGOCD_LOCAL_SUBCOMMANDS:
                        continue
                    # argocd's OWN `--context` selects a named SERVER/login
                    # context (an alias stored in ~/.config/argocd/config) —
                    # a different axis entirely from which Kubernetes cluster
                    # `--core` mode talks to. Only `--kube-context` (or a
                    # throwaway `KUBECONFIG=` prefix) actually pins that.
                    if "--kube-context" not in segment and "KUBECONFIG=" not in segment:
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


def test_argocd_core_with_argocds_own_context_flag_reddens(tmp_path: Path) -> None:
    """`argocd`'s OWN `--context` selects a server/login alias, not a kube context.

    `argocd app sync yadgar --core --context kind-yadgar` looks pinned and is
    not: `--context` here is unrelated to which Kubernetes cluster `--core`
    mode reaches. Only `--kube-context` (or a throwaway `KUBECONFIG=`)
    actually constrains that.
    """
    text = "```bash\nargocd app sync yadgar --core --context kind-yadgar\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "argocd's own --context flag should not have been accepted as a pin"


def test_argocd_core_with_kube_context_flag_is_pinned(tmp_path: Path) -> None:
    """The real fix: `--kube-context` on an `argocd --core` invocation is accepted."""
    text = "```bash\nargocd app sync yadgar --core --kube-context kind-yadgar\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    assert unpinned_invocations(path) == []


def test_a_pinned_kubectl_does_not_excuse_an_unpinned_one_on_the_same_pipe(
    tmp_path: Path,
) -> None:
    """A whole-line scan lets an earlier pinned invocation excuse a later unpinned one.

    `kubectl --context kind-yadgar ... | kubectl -n other apply -f -` measured
    as passing WHOLE under the old per-line check, because `--context`
    appeared somewhere on the line — just not in the second kubectl's own
    segment.
    """
    text = (
        "```bash\n"
        "kubectl --context kind-yadgar get secret foo -o yaml "
        "| kubectl -n other apply -f -\n"
        "```\n"
    )
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "the second, unpinned kubectl should have reddened on its own"


def test_a_trailing_comment_does_not_excuse_an_unpinned_helm(tmp_path: Path) -> None:
    """`--kube-context` mentioned only in a trailing `# comment` does not count.

    The shell never sees a `#`-comment's text — a reader who edits the real
    flags away but leaves a stale reminder comment behind must still see the
    gate redden.
    """
    text = "```bash\nhelm upgrade --install argocd argo/argo-cd  # use --kube-context kind-yadgar\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "a --kube-context mentioned only in a trailing comment should not have counted"


@pytest.mark.parametrize(
    "command",
    [
        "for p in a b; do kubectl get pod $p; done",
        "if true; then kubectl get nodes; fi",
        "false || kubectl get nodes; true && kubectl get pods; ! kubectl get svc",
        "true; ! kubectl get svc",
        "echo nodes | xargs kubectl get",
        "watch kubectl get pods",
        "sudo kubectl get nodes",
        "env FOO=bar kubectl get nodes",
        "time kubectl get nodes",
    ],
)
def test_shell_keyword_and_wrapper_prefixed_kubectl_reddens(tmp_path: Path, command: str) -> None:
    """kubectl after a shell keyword or a wrapper word still needs its own --context.

    None of `do`/`then`/`else`/`xargs`/`watch`/`sudo`/`env`/`time`/`!` are
    punctuation, so the punctuation-only command-start check missed every one
    of these — an invocation this close to the obvious cases should not have
    been a silent gap.
    """
    text = f"```bash\n{command}\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, f"{command!r} should have reddened and did not"


def test_tilde_fence_is_scanned(tmp_path: Path) -> None:
    """CommonMark accepts `~~~` fences too — a reader's renderer does not care which."""
    text = "~~~bash\nkubectl get nodes\n~~~\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "a ~~~-fenced unpinned kubectl should have reddened and did not"


@pytest.mark.parametrize(
    "command",
    [
        "nix shell nixpkgs#x && kubectl -n yadgar delete pod a",
        "curl https://x.example/#frag && kubectl -n yadgar delete pod a",
    ],
)
def test_mid_word_hash_does_not_start_a_comment(tmp_path: Path, command: str) -> None:
    """A `#` is a comment only at the START of a word — mid-word it is data.

    `nixpkgs#x` (a nix flake reference) and `https://x.example/#frag` (a URL
    fragment) both contain a literal `#` with no preceding whitespace. Bash
    agrees: `echo nixpkgs#x && echo ran` prints `ran`, but `echo nixpkgs #x &&
    echo ran` does not. A comment-stripper that stops at ANY unquoted `#`
    truncates these lines right there, hiding everything after — including
    the unpinned `kubectl` this test's command carries.
    """
    text = f"```bash\n{command}\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, f"{command!r} hid its kubectl behind a false comment and did not redden"


def test_brace_group_open_starts_a_command(tmp_path: Path) -> None:
    """`{ kubectl ...; }` groups commands in the current shell — `{` opens one."""
    text = "```bash\n{ kubectl -n yadgar get pod a; }\n```\n"
    path = tmp_path / "MIGRATION_NOTES.md"
    path.write_text(text)
    offenders = unpinned_invocations(path)
    assert offenders, "an unpinned kubectl right after `{` should have reddened and did not"
