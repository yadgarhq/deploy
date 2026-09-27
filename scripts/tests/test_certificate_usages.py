"""Every committed Certificate under `infra/` names the right extended key usage.

LEDGER 720's EKU WALL, KEPT FOR THE ONE CERTIFICATE `deploy` STILL DECLARES.
Step 5 of `plans/retiring-the-deploy-copies.md` retired `infra/internal-tls/` to
the parent chart, and the shared `certificate-usages` hook left this
repository's pre-commit with it: that hook refuses fewer than two Certificates,
and after step 5 there is one. The ten internal leaves are guarded where they now
live, by `yadgarhq/platform`'s render-time suite (platform#19, 0a9a634).

WHAT IS LEFT HERE IS `infra/tls/certificate.yaml` — `gateway-tls`, the edge
leaf. Without this test it would sit unguarded from step 5 to step 7. The rule
below is `judge()` from `yadgarhq/actions` `hooks/certificate_usages.py`, ported
rather than imported: an authority (`isCA: true`) names NEITHER `server auth`
nor `client auth`; a leaf names EXACTLY ONE. Naming neither is not the safe
omission — cert-manager then issues a leaf with no extended key usage, which
webpki accepts in both directions.

THE FLOOR IS 1, not the hook's 2, because 1 is what this repository holds. A
walk that finds nothing fails rather than passing having checked nothing.

THIS FILE RETIRES AT STEP 7, in the merge that moves `infra/tls/certificate.yaml`
to `platform.edgeTLS.create`. That merge deletes this file with it; leaving it
would fail its own floor on a repository declaring no Certificate at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]

SERVER = "server auth"
CLIENT = "client auth"

# THE FEWEST CERTIFICATES THIS MAY EXAMINE. See the module docstring.
MINIMUM_CERTIFICATES = 1


def judge(path: str, name: str, is_ca: bool, usages: list[str] | None) -> Iterator[str]:
    """The wall, as one certificate's verdict. Yields one message per problem."""
    named = set(usages or [])
    serves = SERVER in named
    dials = CLIENT in named

    if is_ca:
        if serves or dials:
            yield (
                f"{path}: `{name}` is `isCA: true` and names an extended key usage "
                f"({sorted(named & {SERVER, CLIENT})}). An authority signs; it "
                f"neither answers nor dials."
            )
        return

    if serves and dials:
        yield f"{path}: `{name}` names both `{SERVER}` and `{CLIENT}`. Name one."
    elif not serves and not dials:
        yield (
            f"{path}: `{name}` names neither `{SERVER}` nor `{CLIENT}` "
            f"(usages: {sorted(named) or 'absent'}). A leaf with no extended key "
            f"usage is accepted in BOTH directions. Name one."
        )


def certificates(root: Path) -> list[tuple[str, dict]]:
    """Every `cert-manager.io` Certificate document in a `.yaml` file under `infra/`."""
    found: list[tuple[str, dict]] = []
    for path in sorted((root / "infra").rglob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text()):
            if (
                isinstance(document, dict)
                and document.get("kind") == "Certificate"
                and str(document.get("apiVersion", "")).split("/")[0] == "cert-manager.io"
            ):
                found.append((str(path.relative_to(root)), document))
    return found


def problems(found: list[tuple[str, dict]]) -> list[str]:
    """Every verdict over every certificate, in order."""
    messages: list[str] = []
    for path, document in found:
        spec = document.get("spec") or {}
        name = (document.get("metadata") or {}).get("name", "<unnamed>")
        messages += judge(path, name, bool(spec.get("isCA")), spec.get("usages"))
    return messages


def test_every_committed_certificate_names_one_direction() -> None:
    """The wall over the working tree, with the count it examined printed."""
    found = certificates(REPOSITORY)
    print(f"[certificate usages] examined {len(found)} Certificate(s)")
    assert len(found) >= MINIMUM_CERTIFICATES, (
        f"found {len(found)} Certificate(s) under infra/ and "
        f"{MINIMUM_CERTIFICATES} is the fewest this may examine"
    )
    assert not problems(found), "\n".join(problems(found))


def test_a_leaf_naming_both_directions_reddens() -> None:
    """Red case: `gateway-tls` given `client auth` beside `server auth`."""
    leaf = {"metadata": {"name": "gateway-tls"}, "spec": {"usages": [SERVER, CLIENT]}}
    assert problems([("infra/tls/certificate.yaml", leaf)]) == [
        f"infra/tls/certificate.yaml: `gateway-tls` names both `{SERVER}` and `{CLIENT}`. Name one."
    ]


def test_a_leaf_naming_neither_direction_reddens() -> None:
    """Red case: the `usages` list dropped, which is wider than naming both."""
    leaf = {"metadata": {"name": "gateway-tls"}, "spec": {}}
    found = problems([("infra/tls/certificate.yaml", leaf)])
    assert len(found) == 1 and "names neither" in found[0], found


def test_an_authority_naming_a_direction_reddens() -> None:
    """Red case: `isCA: true` does not exempt a certificate that names a direction."""
    authority = {"metadata": {"name": "ca"}, "spec": {"isCA": True, "usages": [SERVER]}}
    found = problems([("infra/x.yaml", authority)])
    assert len(found) == 1 and "isCA: true" in found[0], found


def test_an_empty_walk_fails_the_floor(tmp_path: Path) -> None:
    """Red case: a tree with no Certificate must not read as a clean tree."""
    (tmp_path / "infra").mkdir()
    assert len(certificates(tmp_path)) < MINIMUM_CERTIFICATES
