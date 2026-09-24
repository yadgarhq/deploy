"""Every ingress rule in this repository names a source, and the broker's
monitoring rule names THIS namespace (ledger 897).

WHAT THIS REFUSES, AND WHY IT IS NOT THE SAME AS A CLIENT CENSUS. An ingress
rule whose `from` is absent OR EMPTY matches ALL SOURCES — every namespace, and
whatever else the CNI presents to the pod. ADR-0664 read that off the OpenAPI
description compiled into the kubectl binary: "If this field is empty or
missing, this rule matches all sources". A census that walks each rule's `from`
therefore cannot see such a rule at all: it contributes nothing, and an
allow-all leaves the list of admitted clients reading exactly as it did before.
So the ports admitted from EVERY source are asserted separately, as an equality
against a written-down expectation rather than as a ceiling — a ceiling passes
when a from-less rule is added to a policy that had none.

`infra/network-policies/shared-infrastructure.yaml`'s 8222 rule was the one
from-less rule in this repository until ledger 897 narrowed it, which is why
this gate exists at the moment it does. `EXPECTED_ALLOW_ALL_PORTS` is empty for
every policy now, and RED CASE (a) below is what stops that being vacuous.

THE WHOLE TREE, NOT A FILE LIST, and that is the lesson of the EKU wall
(`certificate-usages`, ledger 720). A check scoped with pre-commit's `files:`
key is handed only what changed, so deleting the subject, renaming it, or moving
it out of the scoped directory makes the check pass having inspected nothing.
Every `*.yaml` under the repository root is parsed, and the floors below refuse
a run that found less than it expected.

TWO MODES, the same way `runner_image_pinned.py` splits its structural and
signature halves, and here the second mode exists to make the gate's own lookup
testable. `--subject-port` moves the monitoring lookup onto a port; pointing it
at a port no policy names must make this REFUSE rather than pass over an empty
list. That is how the check is shown to be capable of failing at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parent.parent

# ── THE SUBJECT ──────────────────────────────────────────────────────────────
# The broker's monitoring port, the policy that admits it, and the label the
# narrowed rule selects namespaces by. `kubernetes.io/metadata.name` is set by
# kube-apiserver on every Namespace, so it is the one namespace label that needs
# no labelling step and cannot be forgotten on a namespace created later.
MONITORING_PORT = 8222
BROKER_POLICY = "nats-ingress"
NAMESPACE_LABEL = "kubernetes.io/metadata.name"

# ── THE PORTS ADMITTED FROM EVERY SOURCE, WRITTEN DOWN ───────────────────────
# Keyed by policy name and asserted as an EQUALITY. Every NetworkPolicy this
# repository declares appears here, including the ones that admit nothing from
# everywhere, so a new policy arriving with a from-less rule fails against a key
# that is missing rather than passing under a rule that only looks at the ones
# named.
#
# ALL FOUR ARE EMPTY, AND THAT DOES NOT MAKE THIS VACUOUS. Red case (a) in the
# pull request deletes the `from` from the monitoring rule and this equality
# goes red, naming the port. An expectation of `set()` is the strongest form
# this gate takes, not the absence of one.
#
# `estate-front-egress` IS THE EXCEPTION, AND IT IS STATED RATHER THAN LEFT TO
# BE READ OFF THE DICT. It declares `policyTypes: [Ingress, Egress]` with NO
# `ingress` key — a deny-all for ingress — and four `egress` rules. So
# `ingress_rules` returns nothing for it and its `set()` here passes without
# examining anything. It is listed so that renaming or deleting it reddens the
# floor below, NOT because its egress is checked: this gate asks only "does
# every INGRESS rule name a source". The mirror question for egress — a `to`-less
# rule, which admits every destination the same way — is a separate gate that
# nothing in this repository asks yet.
EXPECTED_ALLOW_ALL_PORTS: dict[str, set[int]] = {
    "valkey-ingress": set(),
    "nats-ingress": set(),
    "gateway-ingress": set(),
    "estate-front-egress": set(),
}

# FLOORS, not counts of what is here today — the same reason
# `runner_image_pinned.py` carries its own. A policy that is renamed or deleted
# must redden rather than quietly shrink the subject, and a tree that yields no
# policies at all is a broken walk rather than a clean repository.
MINIMUM_POLICIES = len(EXPECTED_ALLOW_ALL_PORTS)


def documents():
    """Every YAML document in the repository, with the file it came from.

    `.git` is skipped because it holds packed objects rather than manifests. A
    document that does not parse is REPORTED rather than skipped: a manifest this
    gate cannot read is a manifest it is not checking, and silence there is the
    failure this whole file is written against.
    """
    for path in sorted(REPOSITORY.rglob("*.yaml")):
        if ".git" in path.parts:
            continue
        try:
            loaded = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError as failure:
            yield path, failure
            continue
        for document in loaded:
            if isinstance(document, dict):
                yield path, document


def policies() -> list[tuple[Path, dict]]:
    found = []
    for path, document in documents():
        if isinstance(document, Exception):
            raise SystemExit(f"{path}: does not parse as YAML: {document}")
        if document.get("kind") == "NetworkPolicy":
            found.append((path, document))
    return found


def ingress_rules(policy: dict) -> list[dict]:
    """The policy's ingress rules, or none.

    `.get("ingress") or []` rather than `["ingress"]`: `estate-front-egress`
    declares `policyTypes: [Ingress, Egress]` with NO `ingress` key at all, which
    is a deny-all for ingress and a perfectly valid policy. Indexing would raise
    on it, and a gate that crashes on a legitimate manifest gets scoped away from
    that manifest, which is how a check stops covering things.
    """
    return policy["spec"].get("ingress") or []


# ── THE MATCHERS, COPIED FROM yadgarhq/platform (ADR-0679) ───────────────────
# `scripts/tests/test_shared_infrastructure.py` there already hardened these
# against the shapes below. A second derivation is a second set of bugs, so the
# predicates are carried over rather than re-reasoned; the only change is that
# they take the RULE LIST rather than the policy, because of `ingress_rules`
# above.


def from_less_rules(rules: list[dict]) -> list[dict]:
    """Every ingress rule that names NO source, i.e. every rule admitting all of them. PURE.

    `not rule.get("from")` rather than `"from" not in rule`, and the difference is
    the whole gate. A rule carrying `from: []` is the SAME allow-all to the API
    server as a rule carrying no `from` key at all — ADR-0664 read it off the
    OpenAPI description compiled into the kubectl binary: "If this field is empty
    or missing, this rule matches all sources". The empty-list shape is also the
    one a templating step over an empty list produces, so a predicate keyed on the
    missing KEY would miss the shape most likely to arrive.
    """
    return [rule for rule in rules if not rule.get("from")]


def policy_ports(rules: list[dict]) -> set[int]:
    return {port["port"] for rule in rules for port in rule.get("ports", [])}


def rules_admitting(rules: list[dict], port: int) -> list[dict]:
    """Every ingress rule that names `port`. PURE.

    RETURNS THE LIST RATHER THAN THE RULE, and the caller asserts its length. A
    helper that returned "the" rule would have to pick one when a policy names the
    same port twice — two rules on one port are an OR, so a second one widens what
    is admitted while the first still reads correctly — and it would have to raise
    or return `None` when there are none, which is the shape that lets a caller
    written as `for rule in ...:` pass over an empty list in silence.
    """
    return [
        rule
        for rule in rules
        if any(named["port"] == port for named in rule.get("ports", []))
    ]


# ── THE ASSERTIONS ───────────────────────────────────────────────────────────


def check_allow_all(found: dict[str, tuple[Path, dict]]) -> list[str]:
    """Every policy admits from every source exactly the ports written down."""
    problems = []
    for name, expected in EXPECTED_ALLOW_ALL_PORTS.items():
        path, policy = found[name]
        rules = ingress_rules(policy)
        admitted = policy_ports(from_less_rules(rules))
        if admitted != expected:
            problems.append(
                f"{path}: {name} admits {sorted(admitted)} from EVERY source in "
                f"the cluster; {sorted(expected)} is what this repository says it "
                f"should. A rule whose `from` is absent or empty matches all "
                f"sources (ADR-0664) — it is the widest scope there is, not a "
                f"narrow one. Either write the `from` the rule needs, or change "
                f"EXPECTED_ALLOW_ALL_PORTS and say in the manifest why the port "
                f"is open to the whole cluster."
            )
    return problems


def check_monitoring_rule(found: dict[str, tuple[Path, dict]], port: int) -> list[str]:
    """The monitoring rule names exactly one namespace, and it is this one.

    THE NAMESPACE IS READ OFF THE POLICY'S OWN `metadata.namespace` rather than
    compared to a literal held here. A literal in this file that matched a literal
    in the manifest would be a fixture certifying a copy of itself; reading the
    selector back against the object's own namespace catches the failure that can
    actually happen — a selector naming a DIFFERENT namespace than the policy sits
    in, which admits a namespace nobody meant while still reading like a narrow
    rule.
    """
    problems = []
    path, policy = found[BROKER_POLICY]
    namespace = policy["metadata"]["namespace"]
    rules = ingress_rules(policy)
    admitting = rules_admitting(rules, port)

    # THE DENOMINATOR, PRINTED ON EVERY RUN rather than only on a failure. A gate
    # that reports what it examined makes a walk that quietly stopped finding
    # rules visible in the output, instead of only in a passing exit code.
    print(
        f"[monitoring-rule gate] {path.relative_to(REPOSITORY)}: {BROKER_POLICY} "
        f"in namespace {namespace!r}: {len(rules)} ingress rule(s) examined, "
        f"{len(admitting)} naming port {port}, "
        f"{len(from_less_rules(rules))} naming no source at all; "
        f"ports admitted overall: {sorted(policy_ports(rules))}"
    )

    if len(admitting) != 1:
        return [
            f"{path}: expected exactly ONE ingress rule of {BROKER_POLICY} naming "
            f"port {port}, found {len(admitting)} among {len(rules)} rule(s) "
            f"admitting {sorted(policy_ports(rules))}. Zero means either the port "
            f"is DENIED to everything — and the broker's own probes with it — or "
            f"that this gate is pointed at a port nothing names, which is a check "
            f"that cannot fail. More than one is an OR, so a second rule widens "
            f"what is admitted while the first still reads correctly."
        ]

    rule = admitting[0]
    peers = rule.get("from")
    if not peers:
        return [
            f"{path}: the rule of {BROKER_POLICY} admitting port {port} names NO "
            f"source, so it admits every pod in every namespace (ADR-0664). The "
            f"port is plain HTTP with no authorization on it, so the network "
            f"layer is the only thing that scopes it."
        ]

    if len(peers) != 1:
        problems.append(
            f"{path}: the monitoring rule names {len(peers)} peers and each is an "
            f"OR with the others, so any one of them admits traffic on its own: "
            f"{peers}"
        )
        return problems

    if set(peers[0]) != {"namespaceSelector"}:
        problems.append(
            f"{path}: the monitoring rule's peer carries {sorted(peers[0])}; a "
            f"`podSelector` beside the `namespaceSelector` NARROWS this peer to "
            f"named pods, and an `ipBlock` in a peer of its own WIDENS it to a "
            f"CIDR — neither is what 'this namespace, all of it, and nothing "
            f"else' means."
        )
        return problems

    selector = peers[0]["namespaceSelector"].get("matchLabels")
    if selector != {NAMESPACE_LABEL: namespace}:
        problems.append(
            f"{path}: the monitoring rule selects namespaces by {selector}; this "
            f"policy lives in namespace {namespace!r}, and `{NAMESPACE_LABEL}` is "
            f"the label kube-apiserver sets on every Namespace — so any other key "
            f"here selects on a label nothing guarantees exists, and any other "
            f"value admits a namespace this policy does not live in."
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--subject-port",
        type=int,
        default=MONITORING_PORT,
        help=(
            "the port the monitoring-rule gate looks up. Exists so the gate's own "
            "lookup can be broken deliberately: a port no policy names must make "
            "this refuse, not pass."
        ),
    )
    arguments = parser.parse_args()

    declared = policies()
    print(
        f"[policy gate] {len(declared)} NetworkPolicy document(s) found under "
        f"{REPOSITORY.name}/: "
        f"{sorted(policy['metadata']['name'] for _, policy in declared)}"
    )

    if len(declared) < MINIMUM_POLICIES:
        print(
            f"\nREFUSING: found {len(declared)} NetworkPolicy documents, and this "
            f"repository declares at least {MINIMUM_POLICIES}. A run that "
            f"inspected less than it expected is not a pass — see the EKU wall in "
            f".pre-commit-config.yaml for the same failure caught the hard way.",
            file=sys.stderr,
        )
        return 1

    found = {policy["metadata"]["name"]: (path, policy) for path, policy in declared}
    missing = sorted(set(EXPECTED_ALLOW_ALL_PORTS) - set(found))
    if missing:
        print(
            f"\nREFUSING: {missing} named in EXPECTED_ALLOW_ALL_PORTS and not "
            f"found in the tree. A policy that is renamed or deleted must redden "
            f"here rather than silently shrink what this gate covers.",
            file=sys.stderr,
        )
        return 1

    unexpected = sorted(set(found) - set(EXPECTED_ALLOW_ALL_PORTS))
    if unexpected:
        print(
            f"\nREFUSING: {unexpected} declared in the tree and not named in "
            f"EXPECTED_ALLOW_ALL_PORTS. Every policy states what it admits from "
            f"every source, including `set()` — a new one arriving unlisted would "
            f"otherwise be the one this gate does not read.",
            file=sys.stderr,
        )
        return 1

    problems = check_allow_all(found) + check_monitoring_rule(
        found, arguments.subject_port
    )
    if problems:
        print("", file=sys.stderr)
        for problem in problems:
            print(f"ERROR {problem}\n", file=sys.stderr)
        return 1

    print(
        f"[policy gate] every ingress rule names a source, and {BROKER_POLICY}'s "
        f"port {arguments.subject_port} rule names one namespace."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
