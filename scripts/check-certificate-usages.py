#!/usr/bin/env python3
"""Refuse an internal-CA leaf whose `usages` does not separate the two directions.

`yadgar-internal-ca` signs BOTH the serving leaves and the client ones, so the
extended key usage is the only thing that stops one being replayed as the
other. That wall separates NAMED purposes and nothing else: webpki's
`KeyUsage::client_auth()` and `KeyUsage::server_auth()` are
`required_if_present` rather than `required`, so a leaf carrying no extended
key usage at all passes BOTH checks -- and a Certificate with no `usages` block
is exactly what cert-manager emits by default. Review is a weak gate against a
default, so two shapes are refused here instead:

  * `usages` absent or empty        -- the default, and valid in both directions
  * `usages` naming both directions -- the same hole, spelled out

Everything else is ignored: Certificates under another issuer (the edge leaves,
the MariaDB operator's own stack) and the CA itself, which the self-signed
issuer signs and which serves nothing.
"""

import sys

import yaml

ISSUER = "yadgar-internal-ca"
SERVER = "server auth"
CLIENT = "client auth"


def problems(path):
    with open(path, encoding="utf-8") as handle:
        documents = list(yaml.safe_load_all(handle))

    for document in documents:
        # A file that opens with a comment block yields a leading `None`.
        if not isinstance(document, dict):
            continue
        if document.get("kind") != "Certificate":
            continue
        spec = document.get("spec") or {}
        if (spec.get("issuerRef") or {}).get("name") != ISSUER:
            continue

        name = (document.get("metadata") or {}).get("name", "<unnamed>")
        usages = spec.get("usages") or []
        if not usages:
            yield (
                f"{path}: {name} names no `usages`. cert-manager then issues a leaf "
                f"with no extended key usage, which is valid as BOTH a serving and a "
                f"client credential. Name `{SERVER}` or `{CLIENT}`, never neither."
            )
        elif SERVER in usages and CLIENT in usages:
            yield (
                f"{path}: {name} names both `{SERVER}` and `{CLIENT}`. One authority "
                f"signs both directions here, and this list is what keeps them apart."
            )


def main(paths):
    found = [problem for path in paths for problem in problems(path)]
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
