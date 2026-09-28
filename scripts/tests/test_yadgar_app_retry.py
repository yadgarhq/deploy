"""`infra/yadgar-app.yaml`'s `syncPolicy.retry` is a positive, finite budget.

Argo CD 3.1.8's `autoSync` (`controller/appcontroller.go`, func at line 2084)
gives an automated sync operation a default `Retry: appv1.RetryStrategy{Limit:
5}` (line 2148), applied unless `spec.syncPolicy.retry` overrides it (lines
2151-2153). Once a retry budget is spent, the operation is already attempted
and unsuccessful, and the next reconcile logs `Skipping auto-sync: failed
previous sync attempt to %s` (line 2160) — the Application pins on that
revision until a new commit or a manual sync changes it.

`limit: -1` is Argo CD's spelling of "unlimited", and unlimited is wrong here:
a retrying operation pins the revision it started on, so Argo starts no second
operation on the very commit meant to fix it. This gate asserts the budget is
present, positive and finite, so a future edit cannot silently drop it or
spell it unlimited.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
APPLICATION = REPOSITORY / "infra" / "yadgar-app.yaml"


def test_retry_limit_is_a_positive_finite_int() -> None:
    manifest = yaml.safe_load(APPLICATION.read_text())
    sync_policy = manifest["spec"]["syncPolicy"]
    retry = sync_policy.get("retry")
    assert retry is not None, "syncPolicy.retry is missing on the yadgar Application"
    limit = retry.get("limit")
    assert isinstance(limit, int) and not isinstance(limit, bool), limit
    assert limit > 0, f"retry.limit must be positive and finite (never -1/unlimited), got {limit}"
