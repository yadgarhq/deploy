#!/usr/bin/env python3
"""The ARC runner pod runs a digest the estate's own CI published and signed.

LEDGER 742. `infra/estate-front-runner.yaml` pins the runner container to
`ghcr.io/yadgarhq/estate-runner@sha256:52bb30bb…` (ledger 610, c60b69f8). That
pin is the ONE image reference in this repository that names an artefact this
organisation builds itself, and NOTHING CHECKED IT. It is not covered by
`yadgarhq/argocd`'s `scripts/versions_pinned.py`, which reads `versions/*.yaml`
and derives its subject from that repository's own history — the runner is not a
`yadgar-deployable` module, has no `chart/`, and writes no version file. So the
pin could go stale, be edited back to a tag, or be pointed at a digest nobody
signed, and no check anywhere would say a word.

THREE WAYS THE PIN CAN BE LOST, AND ONLY ONE OF THEM LOOKS LIKE A MISTAKE.

- REPLACED BY A TAG. `ghcr.io/yadgarhq/estate-runner:latest` reads as a
  perfectly ordinary image line, and the publishing workflow moves `latest` on
  every weekly rebuild — so the runner pod silently changes underneath a cluster
  whose git says nothing changed. It is also how the pin got here: the image
  this manifest named before ledger 610 was `:0.1.0`, a tag pushed by hand,
  carrying no signature, no SBOM and no scan.
- DELETED ENTIRELY. This is the quiet one and it is why the floors below exist.
  `template.spec.containers[]` is a Helm VALUE. Remove the `image` key and the
  `gha-runner-scale-set` chart falls back to its own default runner image, the
  Application still renders, Argo still reports Synced, and a check that merely
  validates the references it finds finds none and reports success. That is the
  same asymmetry `versions_pinned.py` was written for: the thing whose absence
  is the failure cannot be the thing that triggers the check.
- POINTED AT AN UNSIGNED DIGEST. A digest is immutable, so it always LOOKS like
  a pin. `--verify-signature` below is the only part of this file that can tell
  a digest CI signed from a digest somebody pushed.

WHY THIS LIVES IN THIS REPOSITORY AND NOT IN `yadgarhq/actions`. D62 puts SHARED
CI in one place, and this check is not shared: `infra/estate-front-runner.yaml`
exists nowhere else, so a definition in `yadgarhq/actions` would be a hook that
is a no-op in all fifteen other repositories. It is exactly the argument
`versions_pinned.py` makes in `yadgarhq/argocd` — a fact about one deployment is
gated where that deployment lives.

THE EKU GATE USED TO BE THE OTHER HALF OF THAT SENTENCE, and ledger 720 moved
it, which is worth recording because it sharpens the test rather than weakening
it. `check-certificate-usages.py` was local here on the same reasoning: one
authority signs both directions in THIS deployment, so the `usages` list is the
whole wall. That fact is still true and still local. What was not local is the
RULE — a leaf names one direction — and keeping the rule local made it a rule in
force in one repository, scoped to one directory and one issuer name, both of
which it could be walked around. So the test is not "does only one repository
have the subject today", it is "is the rule itself general". The runner image
pin is not: `infra/estate-front-runner.yaml` is the only thing it can ever
describe. And the hook is where a gate becomes
load-bearing rather than advisory: the shared `ci-pr` workflow's `precommit` job
runs every hook in that file with no SKIP list, and it feeds `ci / passed`,
which is this repository's only required status check.

IT PARSES THE YAML RATHER THAN GREPPING FOR AN IMAGE REFERENCE, for the reason
`no_build_cache.py` and `helm_pin_agrees.py` both give at length: the strings
appear in PROSE in these files. `infra/estate-front-runner.yaml` discusses
`ghcr.io/actions/actions-runner` in a comment, and the comment ABOVE the pin
exists precisely to explain which references are refused — so a grep gate would
redden pointing at the explanation of itself, and would pass on the day somebody
rewrote the comment. Comments are not nodes; this walks the node tree.

THE INLINE HELM VALUES ARE PARSED AS YAML TOO, WITH THE LINE NUMBERS CARRIED
THROUGH. The reference does not sit in this document — it sits inside the
`spec.source.helm.values` BLOCK SCALAR, which YAML hands over as one opaque
string. An error naming the line the block starts on points 134 lines above the
fault. The offset arithmetic in `scalars` is asserted against the real
file rather than reasoned about: see `python3 scripts/runner_image_pinned.py`'s
success line, which prints the file and line of everything it inspected, and
compare it against `grep -n`. A gate that reddens naming the wrong line is a
gate whose output misleads the person trying to clear it.

THE SUBJECT IS THE RUNNER SCALE SET, NOT EVERY IMAGE IN `infra/`, and that is a
measured limit rather than a preference. Six other image references live here —
three `mariadb:11.8.8`, two `curlimages/curl:8.16.0`, one `valkey/valkey:9.1.1`
— and every one is a third-party image pinned by tag. A rule cast wide enough to
reach them would be breached on the day it landed and would need an exemption
list, which is how a limit stops meaning anything. The discriminator is not a
list of filenames: it is the registry namespace. `ghcr.io/yadgarhq/*` is what
this organisation publishes, and it is therefore the only thing whose signature
this estate can verify at all — the rule and the reason it is enforceable are
the same fact. Whether the third-party tags should also be digests is a real
question and a different one; this gate does not answer it and does not pretend
to.

TWO FLOORS, BECAUSE A GATE WITH NOTHING TO CHECK REPORTS SUCCESS. One scale set
Application and one container image inside it, both refused at zero. The second
is the per-Application one and it is the load-bearing half — a global "at least
one reference somewhere in infra/" would be satisfied by a first-party image
added anywhere else in this repository while the runner's own key sat deleted.
D76 is the shape being closed: a green run that inspected nothing.

WHAT `--verify-signature` ADDS, AND WHERE IT RUNS. `cosign verify` against the
workflow identity that signed the image, which no offline hook can do: it needs
cosign and it needs the registry. It is a SECOND MODE of this file rather than a
second file, so the two halves can never disagree about which references are the
subject. It runs in the `signature` job of `.github/workflows/ci.yaml` — and
that job is NOT yet a required check, which MIGRATION_NOTES.md says out loud
along with the one-line ruleset change that would make it one. Until that
change, it is a red cross nobody is required to clear.

THREE CAUSES, THREE MESSAGES (ADR-0578). cosign absent, registry unreachable,
and a digest genuinely not signed by CI are three different facts, and only the
third says anything about the pin. The first two REFUSE rather than skip: a
signature check that reports success when it could not reach the registry is
D76 again, wearing the costume of a helpful fallback.

THE IDENTITY IS EXACT RATHER THAN A REGEXP, and it was read off the live
signature rather than guessed — the certificate on
`ghcr.io/yadgarhq/estate-runner@sha256:52bb30bb…`'s sigstore bundle carries the
SAN below and the issuer below. An exact identity means a signature made from a
branch, from a fork, or by a different workflow is refused, which is the whole
value of checking one.

WHAT THIS DOES NOT CHECK, said here rather than left to be found. It does not
check that the pinned digest is the NEWEST published one — a stale pin is a
deliberate state (a rollback is an edit to this key), and the weekly rebuild
means "newest" changes with no edit to this repository, so a freshness rule
would redden every Monday naming nobody's change. It does not check the ARC
CHART version, which is a `targetRevision` rather than an image. And it says
nothing about the third-party tags above.

Run it: `python3 scripts/runner_image_pinned.py [--verify-signature]`.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import urllib.error
import urllib.request

import yaml

INFRA = pathlib.Path("infra")

# The chart whose values carry the runner pod template. Matched on the chart
# NAME rather than on a filename, so moving or renaming the manifest keeps it
# covered and a second scale set is covered on the day it is added.
RUNNER_CHART = "gha-runner-scale-set"

# The registry namespace this organisation publishes into. See the docstring for
# why this and not "every image in infra/".
FIRST_PARTY = "ghcr.io/yadgarhq/"

# Lowercase hex only: that is what the registry emits and what the kubelet
# accepts. Same expression as `versions_pinned.py`'s, deliberately.
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

# FLOORS, not counts of what is here today. Adding a second scale set is a
# legitimate change and must not redden; dropping to zero silently turns this
# file into a check that cannot fail.
MINIMUM_SCALE_SETS = 1
MINIMUM_IMAGES_PER_SCALE_SET = 1

# The workflow identity that signs this image, read off the live certificate on
# 2026-09-06 rather than inferred from the workflow file: SAN
# `https://github.com/yadgarhq/actions/.github/workflows/estate-runner-image.yaml@refs/heads/main`,
# issuer `https://token.actions.githubusercontent.com`. Both the `main` push and
# the weekly cron sign as `refs/heads/main`, so one exact identity covers every
# run that is allowed to publish.
COSIGN_IDENTITY = (
    "https://github.com/yadgarhq/actions"
    "/.github/workflows/estate-runner-image.yaml@refs/heads/main"
)
COSIGN_ISSUER = "https://token.actions.githubusercontent.com"

# Anonymous `GET /v2/` answers 401 on GHCR, which is a REACHED registry rather
# than a failure. Any HTTP answer at all proves the network; only a transport
# exception does not. This is what separates cause two from cause three.
REGISTRY_PROBE = "https://ghcr.io/v2/"
PROBE_TIMEOUT_SECONDS = 20


def one_line(text: object) -> str:
    """`::error::` annotates the FIRST line of its payload and drops the rest."""
    return " ".join(str(text).split())


def scalars(node, path=(), offset=0):
    """Every scalar in a composed document, as (path, value, 1-based line).

    `path` carries mapping keys and sequence indices, so a caller can ask where
    a value sits rather than only what it is.

    A `values` BLOCK SCALAR is descended into as YAML in its own right, with
    `offset` carrying the outer file's line numbering across the boundary. This
    is what makes an error name the line the reference is actually on. The
    arithmetic: PyYAML marks a block scalar at its `|` header, the content
    starts on the next line, and PyYAML preserves every content line — comments
    inside a block scalar are literal text — so an inner 0-based line `i` is
    outer 0-based `header + 1 + i`. Verified against
    `infra/estate-front-runner.yaml`, whose runner image reports line 274.
    """
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            step = key.value if isinstance(key, yaml.ScalarNode) else None
            if (
                step == "values"
                and isinstance(value, yaml.ScalarNode)
                and value.style in ("|", ">")
            ):
                try:
                    inner = yaml.compose(value.value)
                except yaml.YAMLError:
                    inner = None
                if inner is not None:
                    yield from scalars(
                        inner,
                        path + (step,),
                        offset + value.start_mark.line + 1,
                    )
                    continue
            yield from scalars(value, path + (step,), offset)
    elif isinstance(node, yaml.SequenceNode):
        for index, value in enumerate(node.value):
            yield from scalars(value, path + (index,), offset)
    elif isinstance(node, yaml.ScalarNode):
        yield path, node.value, offset + node.start_mark.line + 1


def manifests():
    """Every YAML document under `infra/`, as (path, composed node)."""
    for path in sorted(INFRA.rglob("*.yaml")):
        if not path.is_file():
            continue
        for document in yaml.compose_all(path.read_text()):
            if document is not None:
                yield path, document


def image_keys(document):
    """The scalars under `document` whose mapping key is `image`."""
    return [
        (path, value, line)
        for path, value, line in scalars(document)
        if path and path[-1] == "image" and isinstance(value, str)
    ]


def is_scale_set(document):
    """True when this document deploys the ARC runner scale-set chart."""
    return any(
        path[-2:] == ("source", "chart") and value == RUNNER_CHART
        for path, value, _ in scalars(document)
    )


def digest_pinned(reference: str) -> bool:
    """A reference that names an immutable artefact rather than a moving tag."""
    name, separator, digest = reference.partition("@")
    return bool(separator) and bool(name) and bool(DIGEST.fullmatch(digest))


def collect():
    """Every reference this gate is responsible for, and where it lives.

    Returns (references, problems). A reference is
    (path, line, value, in_scale_set).
    """
    references: list[tuple[pathlib.Path, int, str, bool]] = []
    problems: list[str] = []
    scale_set_count = 0

    for path, document in manifests():
        images = image_keys(document)
        if is_scale_set(document):
            scale_set_count += 1
            runner_images = [
                (p, v, line) for p, v, line in images if "containers" in p
            ]
            if len(runner_images) < MINIMUM_IMAGES_PER_SCALE_SET:
                problems.append(
                    f"`{path}` deploys the `{RUNNER_CHART}` chart and its values "
                    f"declare no container image at all. That is not a manifest "
                    f"with nothing to check — it is the chart's OWN default "
                    f"runner image being used instead, which is a moving tag "
                    f"this repository does not name, does not build and cannot "
                    f"verify. Argo renders it, reports Synced, and nothing else "
                    f"says a word. Restore "
                    f"`template.spec.containers[].image` with the digest "
                    f"`yadgarhq/actions`'s `estate-runner image` workflow "
                    f"published and signed."
                )
                continue
            for _, value, line in runner_images:
                references.append((path, line, value, True))
        else:
            for _, value, line in images:
                if value.startswith(FIRST_PARTY):
                    references.append((path, line, value, False))

    if scale_set_count < MINIMUM_SCALE_SETS:
        problems.append(
            f"found {scale_set_count} `{RUNNER_CHART}` Application(s) under "
            f"{INFRA}, and {MINIMUM_SCALE_SETS} is the fewest this can inspect. "
            f"This gate exists because the runner pod's image is the one "
            f"artefact here that this organisation builds itself, and nothing "
            f"else checks its pin; with no scale set it reports success having "
            f"inspected nothing. If the estate genuinely stopped running a "
            f"self-hosted runner, delete this gate rather than leaving it green "
            f"and blind."
        )

    return references, problems


def unpinned(references) -> list[str]:
    """One problem per reference that does not name an immutable artefact."""
    problems = []
    for path, line, value, in_scale_set in references:
        if digest_pinned(value):
            continue
        where = "the runner pod" if in_scale_set else "a first-party image"
        problems.append(
            f"`{path}:{line}` pins {where} to `{value}`, which is a TAG and not "
            f"a digest. A tag is mutable: `estate-runner` is rebuilt weekly and "
            f"its `latest` is re-promoted on every run, so the pod's contents "
            f"change with no commit in this repository to point at. Pin the "
            f"digest that workflow published and signed — "
            f"`ghcr.io/yadgarhq/<image>@sha256:<64 hex>` — which is what ledger "
            f"610 did here and what D61 requires everywhere else in this estate."
        )
    return problems


def registry_reachable() -> str | None:
    """`None` when the registry answered, else why the probe could not reach it.

    ANY HTTP answer counts, 401 included: GHCR refuses an anonymous `/v2/` and
    that refusal is proof the network worked. Only a transport failure is cause
    two, and telling it apart from cause three is the whole point of asking
    separately rather than reading tea leaves in cosign's stderr.
    """
    try:
        urllib.request.urlopen(REGISTRY_PROBE, timeout=PROBE_TIMEOUT_SECONDS)
    except urllib.error.HTTPError:
        return None
    except Exception as error:  # noqa: BLE001 — every transport fault is cause two
        return one_line(f"{type(error).__name__}: {error}")
    return None


def verify_signatures(references) -> list[str]:
    """cosign, over every digest-pinned reference. Three causes, three messages."""
    if shutil.which("cosign") is None:
        return [
            "asked to verify signatures and `cosign` is not on PATH, so nothing "
            "was verified. THIS SAYS NOTHING ABOUT THE PIN. Install it — "
            "`sigstore/cosign-installer` is what `.github/workflows/ci.yaml`'s "
            "`signature` job uses — or run this without `--verify-signature` for "
            "the structural half alone. It is refused rather than skipped "
            "because a signature check that reports success having verified "
            "nothing is the exact defect this file exists to close."
        ]

    unreachable = registry_reachable()
    if unreachable:
        return [
            f"the registry at {REGISTRY_PROBE} could not be reached "
            f"({unreachable}), so no signature was verified. THIS SAYS NOTHING "
            f"ABOUT THE PIN: it is a network or DNS fault here, not a fact about "
            f"what CI published. Re-run when the registry is reachable."
        ]

    problems = []
    for path, line, value, _ in references:
        if not digest_pinned(value):
            continue  # already a problem, and a tag has no digest to verify
        result = subprocess.run(
            [
                "cosign",
                "verify",
                "--certificate-identity",
                COSIGN_IDENTITY,
                "--certificate-oidc-issuer",
                COSIGN_ISSUER,
                value,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            problems.append(
                f"`{path}:{line}` pins `{value}`, and cosign found no signature "
                f"over that digest by `{COSIGN_IDENTITY}` at "
                f"`{COSIGN_ISSUER}`. The registry answered, so this is a fact "
                f"about the artefact rather than about the network: the digest "
                f"is one nobody's release signed. That is what ledger 610 found "
                f"here — the tag in this file resolved to an image built by "
                f"hand, carrying no signature, no SBOM and no scan. cosign said: "
                f"{one_line(result.stderr or result.stdout)}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--verify-signature",
        action="store_true",
        help="also ask cosign whether CI signed each pinned digest (needs "
        "cosign and the registry; refuses rather than skips without them)",
    )
    arguments = parser.parse_args()

    if not INFRA.is_dir():
        print(
            f"::error::{INFRA} does not exist. This gate reads the manifests "
            f"Argo applies, and pre-commit runs hooks from the repository root — "
            f"run it from there."
        )
        return 1

    try:
        references, problems = collect()
    except yaml.YAMLError as error:
        print(f"::error::a manifest under {INFRA} is not a YAML document: {error}")
        return 1

    problems += unpinned(references)

    if not problems and arguments.verify_signature:
        problems += verify_signatures(references)

    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 1

    verified = " and signed by CI" if arguments.verify_signature else ""
    print(
        f"{len(references)} first-party image reference(s), each digest-pinned"
        f"{verified}: "
        + "; ".join(f"{path}:{line} {value}" for path, line, value, _ in references)
        + "."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
