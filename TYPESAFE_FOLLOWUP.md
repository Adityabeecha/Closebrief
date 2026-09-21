# Second TypeSafe follow-up — verified fixes and open work

This follows the full 232-request report and the 89-request sanitized supplement.
Both return classifications, not explanatory bug reports. The reports are
preserved; a detected category is not proof of a defect, and this document does
not claim that every category has been eliminated.

## Implemented and regression-tested

| Area | Concrete correction | Regression file |
| --- | --- | --- |
| Workspace invitations | Enforce the invited email; claim the single-use invitation and add membership in one transaction, rolling back on failure. | `test_security_followup.py` |
| Database security | Abort checkout when RLS scope setup fails. Close and return poisoned connections using the supported pool API. | `test_security_followup.py` |
| Audit verification | Report malformed JSON summaries as broken integrity rather than crashing verification. | `test_security_followup.py` |
| Dataset isolation | Scope rename and deletion to the current workspace/demo universe. | `test_security_followup.py`, `test_auth_followup.py` |
| Digest isolation | An empty workspace returns empty digest history, not global history. | `test_security_followup.py` |
| Authentication | Require expiration and subject claims on session and Supabase JWTs; safely handle malformed optional metadata. | `test_auth_followup.py` |
| Google identity | Resolve the stable Google subject before email lookup, preserving identity when the verified email changes. | `test_auth_followup.py` |
| Funnel comparisons | Compare only consecutive months and identical stage transitions. | `test_math_followup.py` |
| Derived arithmetic | Treat overflow/nonfinite arithmetic as unavailable, propagating through formulas. | `test_math_followup.py` |
| Vector updates | Validate replacement shape before removing the existing vector. | `test_math_followup.py` |
| Context retrieval | Expand search past unrelated-workspace hits without relaxing tenant/business filters; handle zero requested chunks. | `test_math_followup.py` |
| Narrative numeric checks | Do not treat month digits as KPI evidence; distinguish context percentages from amounts; preserve multiplier direction; align percentage attribution with the guard. | `test_ui_generation_followup.py` |
| Generation accounting | Count both successful paid attempts when a faithfulness retry occurs. | `test_ui_generation_followup.py` |
| Mapping validation | Reject infinite/overflowing input numbers; report missing quantity/price columns as validation errors. | `test_backend_followup.py` |
| Budget matching | Do not choose an arbitrary metric when normalized labels are ambiguous. | `test_backend_followup.py` |
| Connector failures | Do not persist or return upstream exception bodies that may contain credentials or source data. | `test_backend_followup.py` |
| Review emails | HTML-escape untrusted sender/metric/period fields and link attributes. | `test_backend_followup.py` |
| Browser API | Reject generic 503 errors while preserving the explicitly shaped facts-only generation fallback. | `test_frontend_followup.py` |
| Empty workspace UI | Clear old period, facts, filtered facts and detail selection. | `test_frontend_followup.py` |
| Export warnings | Preserve failed numeric-faithfulness warnings in PDF and PowerPoint output. | `test_frontend_followup.py` |
| Upload persistence | Roll back and remove the newly created raw file if storage fails; exclusive creation avoids overwriting an existing upload. | `test_backend_followup.py` |
| Profiling statistics | Skip undefined correlation for fewer than two paired observations or constant series; preserve both measures without numeric warnings. | `test_backend_followup.py` |
| Delivery URL validation | Return safe validation errors for malformed URLs/ports, reject embedded credentials and empty DNS results, and use the correct default HTTP port. | `test_backend_followup.py` |

All named regression files are under `tests/`. Changes extend the existing
application; no rebuild, new architecture, production deployment or secret
configuration change was performed. Prior fixes are documented separately in
`TYPESAFE_FIXES.md`.

## Investigated distinctions — not automatically bugs

- Notification configuration is intentionally **platform-admin** controlled,
  not workspace-admin controlled. It must not be reinterpreted as tenant-local
  without designing ownership and operator-delivery requirements.
- Deliberate unscoped PostgreSQL policies support administrative/background
  workflows. Scope-setup failure is now fail-closed; changing all unscoped policy
  behavior is a separate compatibility/security design decision.
- Missing observations in combined account KPIs can deliberately mean zero;
  undefined *derived* results must not mean zero. Existing behavior is preserved
  for the former and corrected for the latter.
- A runtime-risk label on test scaffolding, migration fragments or a partial
  related-file excerpt is not a specific application exception to patch.

## Remaining work — not marked fixed

The interrupted broad audit did not establish a file-by-file resolution for
every classifier label. In particular, unchanged billing/cache, forecasting,
cross-domain/scenario, retention/scheduler, profiler heuristics, all migration
paths, and every large UI/API branch still need complete disposition. Some were
inspected without a reproduced defect, which is not a proof of correctness.

Live PostgreSQL/RLS/pgvector verification requires a disposable test database;
the checks here use isolated SQLite and focused adapter tests. Migration 023
from the prior pass still needs the existing deployment migration workflow.
Audit append/version concurrency, full DNS-rebinding defense,
and truncated/redacted source context remain separate open
items from the previous pass. No production credentials or service calls were
used to test these changes. No further paid TypeSafe rerun was made.

## Verification — 2026-09-21

- Full application suite including Chrome: **436 passed**, with 12 profiler
  warnings, before the final upload/profiler/URL follow-up patches.
- After upload cleanup and undefined-correlation guards: **413 non-browser
  tests passed**, with no warnings. The URL patch was made during that run,
  so its verification is recorded separately below.
- Final focused backend, notification, connector and prior-regression suites:
  **53 passed**, including the four new URL validation cases.
- Ruff (`app tests`), JavaScript syntax check, and Git diff whitespace check
  passed. `.env` is ignored and is not tracked by Git.
- Reviewer suite in `.venv-typesafe`: **17 passed, 1 skipped** (Windows symlink
  permission). The application venv lacks the SDK's `httpx2`; this suite must
  run in the isolated reviewer environment. No paid requests were made.

These checks are evidence for the tested paths, not a declaration that all
TypeSafe labels or all application risks are resolved.
