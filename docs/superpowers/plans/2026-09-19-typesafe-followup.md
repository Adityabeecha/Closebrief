# TypeSafe Findings Follow-up Implementation Plan

**Goal:** Investigate both TypeSafe reports, fix reproducible defects, and account for unverified classifications without unnecessary restructuring.

**Architecture:** Keep the existing FastAPI/pandas/SQLite/PostgreSQL/vanilla-JS architecture. Independent subsystem audits use new focused regression tests. Source-only reviews and tests run offline with default settings; secrets are never printed or sent.

**Spec:** User request to check and solve all potential issues in the full and sanitized TypeSafe reports. Reports contain category labels rather than explanatory findings; labels require local verification.

## Work and ownership

- [ ] Security: authentication, workspaces/datasets, database lifecycle, audit log, admin routes and relevant migrations. Reproduce tenant/authorization or transaction failures in `tests/test_security_followup.py`; fix within those modules, run related suites.
- [ ] Calculations: all compute modules, domains, billing/cache, context/vector retrieval. Reproduce calendar, numerical and data-integrity errors in `tests/test_math_followup.py`; preserve domain contracts and run related suites.
- [ ] UI and generation: web UI, narrative generation/grounding, insights, schemas, board packs/digests and evaluation runner. Reproduce false grounding or runtime behavior in `tests/test_ui_generation_followup.py`; run generation and UI checks.
- [ ] Ingestion and remaining backend: ingestion, notifications/connectors/scheduling, retention/review/services and API integration. Add `tests/test_backend_followup.py` for reproduced failures, implement bounded fixes and run related suites.
- [ ] Consolidate file-level dispositions and unresolved risks in `TYPESAFE_FOLLOWUP.md`. Category-only findings without evidence must not be described as fixed or as proven false positives.
- [ ] Run fresh full application/reviewer tests, Ruff and diff checks. Verify browser tests outside the sandbox if sandbox Chrome startup fails. Record live PostgreSQL or external-service limitations separately.

## Execution constraints

- Preserve prior fixes and unrelated user files; do not commit, deploy or run production migrations.
- Each fix follows: read caller and contract, add failing behavioral test, observe failure, apply smallest safe patch, rerun tests.
- Use isolated temporary SQLite DBs and no configured external services. Test basetemp folders use `.pytest-review-*` and are ignored.
- Do not repeatedly call TypeSafe until labels change. No new paid reviews are needed to prove deterministic fixes.
