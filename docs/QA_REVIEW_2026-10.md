# Closebrief — product re-evaluation (2026-10-23)

Explored end-to-end with Playwright CLI against a local build (isolated SQLite DB, FAISS, no Redis/Sentry),
using the Meridian Labs files in `../real_testing/`. Flows covered: local-dev boot, auth gate + demo,
GL import, wide budget join (both FY sheets), marketing funnel import, dashboard (M/Q/Y), bulk + single
generation, metric detail, Why-did-this-move, Ask, Executive Digest, Board pack, Export menu, Context
Library (RAG), Analytics, Review, Costs, Notifications, User Management, Ctrl+K, dark mode, 390px mobile,
and unauthenticated API access.

What works well: visual design (light/dark/mobile), demo mode, guest isolation (writes 403, guests only
see demo data), wide-budget parsing with title rows / section headers / footnotes, anomaly detection on
the planted spikes (Legal fees Aug-25, Bad Debt Mar-26, severance Feb-26), and RAG — one context note
turned the hosting narrative from "unexplained" into a correct, cited explanation.

---

## P0 — correctness / data bugs

> **Status (2026-10-23): all seven fixed**, each with regression tests that fail on the old code —
> `tests/test_qa_p0_ui.py` (#1, #2, #7), `tests/test_guard.py` (#3), `tests/test_digest_format.py` (#4),
> `tests/test_kpi_suggestions.py` (#5, #6). Re-verified end-to-end in the browser on the Meridian files.

| # | Issue | Evidence / root cause |
|---|---|---|
| 1 | **Finishing an import renames + re-domains the *previous* dataset.** Importing the marketing file with name "Marketing Funnel QA" / domain Marketing renamed the *finance* dataset and switched it to the marketing domain (changes its prompts). New dataset kept the filename + FP&A. On a first import the name/domain are silently dropped. | `confirmKpis()` PATCHes `activeDatasetId`, which still points at the old dataset (or `null`) — `ui/web/app.js` confirmKpis. Use the `dataset_id` returned by `/ingest/{id}/mapping`. |
| 2 | **Duplicate value columns are both imported.** GL file: warning says "amount_local … only one was kept", but both stay selected → 54 metrics (each account twice). `amount_local` sums EUR + USD across entities — meaningless numbers. | Mapping UI/defaults contradict the profiler warning. Auto-set the redundant column to `ignore`, and never default-sum a local-currency column across entities with different currencies. |
| 3 | **Faithfulness guard false-positives on trend streaks.** 4/26 correct narratives marked `failed` / Low confidence because of "grown for **4** consecutive months" — a number the app itself computed and put in the prompt. | `_DURATION_AFTER` in `app/generation/guard.py` only matches when "month(s)" immediately follows the digit. Allow an adjective (consecutive/straight/successive), or add streak length to `_allowed_values`. |
| 4 | **Digest prints raw floats** — "rose 1.1999997910746822%", "-0.09029834254144847% variance" — and still gets a *Verified* badge. | `app/digest/digest.py:95-100` interpolates unrounded floats; the system prompt forbids re-rounding, so the model copies them. Pre-format (1 dp for %, $K/$M for magnitudes) and allow those forms in the guard. |
| 5 | **KPI direction/category defaults are wrong for GL data.** Bad Debt, Cloud Hosting, Legal Fees, Insurance, etc. default to "↑ good"; anything containing "Sales" (Sales Salaries, Sales Tools & CRM, T&E – Sales) is categorized **Revenue ↑ good**. Result: an 18% hosting cost *reduction* shows red, sales payroll growth shows green. The file's own `account_group` column (Revenue / COGS / Opex) is ignored. | `suggest_kpi` keyword match. Use `account_group`/GL code ranges (4xxx revenue, 5xxx COGS, 6xxx opex) when present; default unknown expense-like names to ↓ good. |
| 6 | **Marketing counts shown as dollars** — closed_won "$2", MQLs "$279", SQOs "$18". All 80 metrics default to unit USD. | Infer unit from column name (`*_usd` → USD, clicks/mqls/sqls/won → count). |
| 7 | **Local-dev mode is a blank page.** With `AUTH_ENABLED=false` (README's documented setup) `#app-root` stays `display:none`. | `boot()` → `startApp()` never calls `hideLogin()`. |

## P1 — state / UX bugs

| # | Issue | Evidence |
|---|---|---|
| 8 | After bulk generate, dashboard is self-contradictory: header "0/26 narratives generated", tile "26/26 all explained", banner "No narratives for 2026-06 yet · Generate narratives". | `generateAll()` ends with `updateGenButton()` but not `setFreshness()` / `maybePromptGenerate()`. Banner also persists into Q/Y views where generation isn't possible. |
| 9 | After a budget join, "Done — view dashboard" shows stale facts ("vs plan –", Off plan 0) until a full reload. | Join path doesn't reload facts. |
| 10 | Root cause / correlations are spurious: Subscription Revenue "root cause — moves with Contractors – Engineering (r=0.997)", a metric with no data since Sep-25. | Pearson on trending *levels*; correlate MoM changes (or detrended series), require overlapping periods, exclude discontinued metrics. |
| 11 | Ask can't answer history questions ("lowest month in the last year?" → "The data available doesn't answer that." + Verified badge) although 18 months are loaded. | Ask context is limited to the current period's fact. Include the trend series. |
| 12 | Two datasets with identical names are indistinguishable in the dataset picker / budget target select. Abandoning step 3 of import leaves an orphan, half-configured dataset. | Show created date / metric count; create the dataset only on finish, or offer cleanup. |
| 13 | No materiality threshold: "Off plan: 15 KPIs behind budget" counts −0.1% variances; dashboard "Top mover vs plan" (by %) and board pack "Largest variance" (by $) disagree. | Add a configurable threshold (e.g. ±2% and ±$X); use one ranking definition everywhere. |
| 14 | Cost & usage: digest call logged with 0 tokens but $0.0024; "Alert latency p95 0.0 s · 1 alerts" tile is nonsensical; generation estimate "$0.16 · ~182s" vs actual $0.013 (≈12× high). | Token logging on digest path; recalibrate estimator from `llm_calls` history. |
| 15 | Opening the Digest tab silently runs a ~21 s LLM call. | Generate on click, or show the cached last digest. |
| 16 | Metric detail doesn't update the URL (stays `#dashboard`) — not linkable, browser Back leaves the app view. | `openMetric` should set `#metric=…&period=…` (the parser already exists). |
| 17 | Budget join warning "6 periods had no actuals" doesn't name the metric; zero-budget months for discontinued accounts are treated as missing actuals. | |
| 18 | Empty metrics are created then hidden (9 "<channel> clicks" with no values → 80 normalized vs 71 shown). | Skip all-null measure × dimension combos at normalize time. |

## P2 — product gaps (the "upgrade" list)

1. **Narratives without context are low value.** Every generated narrative restates the card at cent precision ("USD 271,254.84, up 1.20% from USD 268,038.38") and ends "unexplained this period". The demo's hand-written narratives ("$10.72M, rose 0.3%…") are far better than what a real user gets after signing up. Fix: format numbers like the card, drop the boilerplate disclaimer, and use deterministic signals the app already has (YoY, streaks, anomalies, budget seasonality) to say *something* useful.
2. **Structural-change detection.** The app has every signal to spot the planted reclass — 6120 stops in Sep-25 as 5210 starts in Oct-25 at the same run-rate — but nothing surfaces it. A "series ended / series started at matching level" detector would be a standout feature for FP&A.
3. **Roll-ups from GL data.** A GL import produces 27 account cards and no Total Revenue / Gross Margin / EBITDA / Opex. Use `account_group` to build P&L subtotals and derived KPIs automatically; lead the dashboard and board pack with them.
4. **Sort by materiality, not alphabet/DB order.** Subscription Revenue (the largest line) is buried. Analytics dropdowns are unsorted.
5. **Digest should fall back to YoY/MoM movers** when everything is on plan (June: all within 0.5% of budget while hosting is +37.6% YoY and CS payroll +48.9% YoY).
6. **Board pack needs an executive summary** (P&L rollup, top 3–5 stories) before the per-KPI detail; today it's 26 cards that each say "unexplained".
7. **Quarterly/annual views carry no anomaly or narrative signal** (Customer Support −40% QoQ shows "All clear").
8. **Multi-sheet budget import** — FY2025 and FY2026 sheets must be uploaded separately.
9. **KPI picker**: no include/exclude checkboxes or bulk direction/category edit despite "Pick the KPIs to track"; humanize snake_case names (`closed_won_arr_usd` → "Closed-won ARR").
10. **Funnel domain**: derive conversion rates, CAC, cost per MQL/SQO from the funnel file instead of 80 flat metrics.
11. **Sign conventions**: Interest Income imports as −$53K (GL credit sign) and the narrative calls a move from −53.6K to −52.7K a "1.70% increase". Offer a flip-sign option per account group.
12. **Anomaly noise**: near-linear series (Contractors–PS +2%) get flagged because their σ is tiny; add a minimum-%-move floor.

## Minor / polish
- Login password placeholder "••••••••" looks like a pre-filled password; password field not inside a `<form>` (autofill / Enter-to-submit).
- `ALLOW_GUEST=true` has no visible guest entry on the gate (only Demo).
- Empty-state "Generate narratives" toast says "Nothing to generate at this granularity" when there's simply no data.
- Detail chart labelled "12-month trend" regardless of history length.
- Scenario key `real_testing/README_scenario_key.md` F2 says 5210 is absent from the plan; the workbook actually plans the reclass (5210 from Oct-25, 6120 zeroed). Update the key so tests assert the right thing.
