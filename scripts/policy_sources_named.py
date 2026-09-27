"""Every ingress rule in this repository names a source (ledger 897).

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

`infra/network-policies/nats-ingress.yaml`'s 8222 rule was the one
from-less rule in this repository until ledger 897 narrowed it, which is why
this gate exists at the moment it does. `EXPECTED_ALLOW_ALL_PORTS` is empty for
every policy now, and stripping the `from` from any rule of `gateway-ingress`
reddens it, which is what stops that being vacuous.

THE BROKER'S MONITORING-RULE HALF LEFT WITH THE BROKER'S POLICY, at step 6's
first merge of `plans/retiring-the-deploy-copies.md`. That merge deleted
`nats-ingress.yaml` and set `platform.nats.create` true, so the parent chart
renders the policy now, with a spec equal to the deleted file's. The same
assertions already run where the policy is declared, in `yadgarhq/platform`'s
`scripts/tests/test_shared_infrastructure.py`, and they ship in platform
v0.1.13, the version inside the pinned parent 0.2.38 (ADR-0804: the check was
in its new home before this one left):
`test_the_monitoring_rule_admits_this_namespace_and_no_other`,
`test_the_monitoring_rule_losing_its_from_reddens_the_new_gate`,
`test_the_monitoring_gate_reddens_when_its_own_lookup_finds_nothing` and
`test_the_policies_admit_from_every_source_only_where_written_down`. The
`--subject-port` mode that made this file's copy testable left with it.

THE WHOLE TREE, NOT A FILE LIST, and that is the lesson of the EKU wall
(`certificate-usages`, ledger 720). A check scoped with pre-commit's `files:`
key is handed only what changed, so deleting the subject, renaming it, or moving
it out of the scoped directory makes the check pass having inspected nothing.
Every `*.yaml` under the repository root is parsed, and the floors below refuse
a run that found less than it expected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parent.parent

# ── THE PORTS ADMITTED FROM EVERY SOURCE, WRITTEN DOWN ───────────────────────
# Keyed by policy name and asserted as an EQUALITY. Every NetworkPolicy this
# repository declares appears here, including the ones that admit nothing from
# everywhere, so a new policy arriving with a from-less rule fails against a key
# that is missing rather than passing under a rule that only looks at the ones
# named.
#
# BOTH ARE EMPTY, AND THAT DOES NOT MAKE THIS VACUOUS. Deleting the `from` from
# `gateway-ingress`'s rule makes this equality go red, naming the port. An
# expectation of `set()` is the strongest form this gate takes, not the absence
# of one.
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
#
# `valkey-ingress` LEFT THIS DICT WITH THE POLICY ITSELF, at step 3 of
# `plans/retiring-the-deploy-copies.md`. The parent chart renders it now, behind
# `platform.valkey.create`, so this repository no longer declares it and a key
# left here would make the floor below refuse a tree that is correct. Removing
# the key is what keeps the floor a floor.
#
# `nats-ingress` LEFT THE SAME WAY, at step 6's first merge, behind
# `platform.nats.create`. Two is now the number of policies this repository
# declares, and either one going missing still reddens.
#
# `gateway-ingress` LEFT THE SAME WAY, at step 8, behind
# `gateway.networkPolicy.enabled`. The same assertions run where the policy is
# now declared, in `yadgarhq/gateway`'s `scripts/tests/test_ingress_policy.py`
# at v0.9.53, the version the pinned parent 0.3.7 carries (ADR-0804):
# `test_the_policy_admits_from_every_source_only_where_written_down`,
# `test_a_from_less_rule_added_to_the_policy_reddens_the_allow_all_census` and
# `test_a_from_written_as_an_empty_list_is_seen_too`. `deploy`'s own
# `scripts/tests/test_no_two_owners.py` holds the rendered spec equal to the
# deleted copy at this organisation's values.
#
# ONE POLICY IS LEFT, AND IT DECLARES NO INGRESS RULE. `estate-front-egress`'s
# `set()` examines nothing today, so this gate now guards only a FUTURE
# ingress rule added to it or a new policy added here; the floor still
# reddens if the policy is renamed or deleted.
EXPECTED_ALLOW_ALL_PORTS: dict[str, set[int]] = {
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


def main() -> int:
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

    problems = check_allow_all(found)
    if problems:
        print("", file=sys.stderr)
        for problem in problems:
            print(f"ERROR {problem}\n", file=sys.stderr)
        return 1

    print(
        f"[policy gate] every ingress rule of {len(found)} policies names a source."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
