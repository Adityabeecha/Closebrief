# TypeSafe review follow-up

The original report contains 230 structured assessments of 126 eligible source
files. Its category/probability labels are triage signals, not line-specific bug
reports. A positive category is not proof of a defect; this pass fixes issues
reproduced against the existing implementation instead of rewriting flagged files.
The original report is preserved. No additional paid TypeSafe run was performed.

## Implemented fixes

| Area | Verified defect and change |
| --- | --- |
| Context documents | Apply the existing workspace/demo predicate to ID lookups, also protecting update/delete callers. |
| Uploads | Persist workspace ownership and enforce it on lookup and mapping access. |
| Mapping templates | Namespace signatures by workspace; reject cross-workspace ID lookups. |
| Upload parsing | Validate before writing raw files; turn malformed CSV/workbook failures into upload validation errors; bound initial reads and parser row counts. |
| Canonical CSV | Reject invalid month strings, missing metric names and non-finite numeric values. |
| Trend streaks | Require contiguous calendar months ending at the requested month. |
| Derived KPIs | Reject existing metric names, order dependent formulas, remove stale materialized values, and propagate undefined derived results. |
| KPI computation | Group by metric ID, preventing identical names in different datasets from being combined. |
| Audit verification | Normalize PostgreSQL datetime representations and verify stored previous-hash links. Read the previous hash once when recording an entry. |
| PostgreSQL pool | Roll back pending application work before resetting workspace scope; discard connections whose cleanup fails. |
| Connector/notification HTTP | Validate redirect targets, strip headers on cross-origin downloads, and reject cross-origin delivery redirects; disable implicit proxy routing for guarded requests. |
| Evaluation vectors | Honor the explicitly selected, allowlisted table instead of always writing/searching production vectors. |

Regression coverage lives in `tests/test_review_regressions.py`. Its 22 cases
were observed failing before the relevant fixes and passing afterward.

## Verification results

- Non-browser suite: 359 passed, with 12 NumPy warnings in ingestion tests.
- Browser suite: 26 passed outside the sandbox. The sandboxed run crashed Chrome
  before layout assertions; no UI source changes were needed.
- After the final review corrections: 58 targeted tests passed, including all
  22 regression cases and connector/notification/ingestion compatibility tests.
  These overlap the suite above and must not be added to its count.
- Ruff and `git diff --check`: passed.
- The separate read-only review identified three patch regressions (secondary
  sheet truncation, download redirects, and untyped PostgreSQL null parameters);
  all three were corrected before the final targeted test run.

## Deployment notes

- SQLite applies the new uploads column during normal initialization.
- PostgreSQL requires migration `023_upload_workspace.sql`. Use the existing
  migration workflow (`python -m app.db migrate`) before deploying if automatic
  migrations are disabled. No live database migration was run during this work.
- Legacy uploads/templates without ownership remain available only in the
  unscoped local workflow. Tenant users should re-upload/recreate their mappings;
  ownership is intentionally not guessed.
- Notification delivery URLs that redirect to a different origin must use the
  final public URL. Public download redirects, including Sheets exports, remain supported.
- The main app was not rebuilt or restructured. The TypeSafe utility remains
  separate. `.env` remains ignored; credentials were not included in reports.

## Boundaries and remaining review work

This is not a claim that every TypeSafe flag is resolved or that the app is
security-certified. Secret-blocked files were not submitted to TypeSafe, and
classification-only flags without a reproduced defect remain unconfirmed.

Live PostgreSQL/RLS/pgvector integration still needs validation in a disposable
database. Offline tests cover the fixes, not the live database deployment.
Existing fail-open RLS behavior for unscoped jobs was not redesigned. Audit
append concurrency and chain truncation protection need separate assessment.
Redirect protection does not eliminate DNS-rebinding risk: DNS validation and
connection establishment are separate; production egress restrictions remain
important. Upload persistence failures after successful validation can still
leave an orphaned raw file. No real connectors, notifications, or LLM calls were
used for verification.
