# Meridian Labs — Closebrief test dataset

**Company:** Meridian Labs, B2B SaaS (workflow automation for mid-market ops teams).
**Period:** Jan-2025 → Jun-2026 (18 months).
**Entities:** `MER-US` (Meridian Labs, Inc., USD) and `MER-EU` (Meridian Labs B.V., EUR). Every finance file carries `entity`.
**Scale:** ARR $21.18M opening → $28.51M Dec-25 → $31.77M Jun-26. FY25 revenue $26.9M. Headcount 98 → 153.

Everything below is internally consistent: the GL is built from the ARR movement, payroll is built from headcount, and the marketing programs GL account is built from the campaign spend file. Cross-file questions have correct answers.

---

## Files

| File | Shape | Notes |
|---|---|---|
| `finance_gl_actuals.csv` | long, 936 rows | period × entity × account. Has `amount_local`, `amount_usd`, `fx_rate` |
| `finance_budget_plan.xlsx` | **wide**, 3 sheets | months as columns, section headers, blank spacer rows, title rows above the header, one row written as text with `(1,234)` negatives, footnote row at the bottom, separate Assumptions sheet |
| `finance_arr_movement.csv` | long, 357 rows | New Logo / Expansion / Contraction / Churn, customer-level |
| `finance_customer_master.csv` | 324 rows | segment, industry, entity, source, status |
| `finance_headcount_payroll.csv` | long | by dept × entity, incl. `severance_usd` and `open_reqs` |
| `finance_ar_aging.xlsx` | 5 snapshot sheets | invoice-level, aging buckets, collector notes, SUM totals row |
| `finance_vendor_saas_spend.csv` | long | 33 vendors, category + owning department |
| `marketing_funnel_monthly.csv` | long | channel × month: spend → clicks → MQL → SQL → SQO → Won → ARR |
| `marketing_campaign_spend.csv` | long | campaign-level, deliberately messy |
| `marketing_opportunity_attribution.csv` | 1,309 opps | first-touch vs last-touch, stage, lost reason, cycle days |
| `marketing_web_analytics.xlsx` | **wide**, 2 sheets | months as columns, **Jul-2025 missing entirely** |
| `marketing_program_budget.xlsx` | **wide** | channel × month budget |

---

## Planted scenarios — finance

**F1 · Gross margin erosion with two stacked causes.**
GM runs 82% Jan–Jul-25, falls to 72% by Nov-25, bottoms at 71% in Feb-26, recovers to 77% by Jun-26.
Two independent drivers overlap, and a good answer separates them:
- Cloud hosting (`5010`) compounds ~11.8%/month Jul→Nov-25 ($204k → $356k) while revenue grows ~3%/month. Fixed by a Dec-25 re-architecture — hosting drops to $292k and then declines.
- The contractor reclass in F2 moves ~$120k/month into COGS from Oct-25.

*Failure mode to watch for:* attributing the whole GM drop to hosting, or to the reclass, instead of both.

**F2 · Account reclassification that looks like a real trend.**
`6120 Contractors – Engineering` runs Jan–Sep-25 then stops dead. `5210 Contractors – Professional Services` starts Oct-25 at the same run-rate and keeps growing. Nothing changed operationally.
Naive read: "R&D spend fell $121k/month in Q4 — engineering got more efficient." Wrong.
The budget file makes this worse on purpose: `5210` **does not exist in the plan at all**, and `6120` is budgeted for all 12 months. A variance report will show an infinite unfavourable variance on an unbudgeted account and a large favourable variance in R&D.

**F3 · One-time item distorting a trend.**
`6230 Legal & Professional Fees` is ~$40k/month, then $224k in Jun-25 and Jul-25 (run-up costs) and **$1.39M in Aug-25** (settlement). EBITDA is -$1.83M in Aug-25 against a -$570k trend. The FY25 plan budgeted $0.09× the actual for that month.
*Test:* does the tool normalise for it when asked about opex trend or run-rate burn?

**F4 · The big logo, with a breadcrumb trail.**
`Northwind Grocers` (opening ARR $1.88M, ~9% of book):
- Jun-25: **expansion** +$461.5k — looks like the healthiest account
- Nov-25: contraction -$310k
- Dec-25 AR snapshot: $470k invoice, 132 days past due, "Disputed — pending contract review with legal"
- Mar-26 AR snapshot: a second disputed invoice, $156.6k
- **Feb-26: churn -$1.81M** — total ARR falls MoM for the first time ($28.52M → $27.28M)
- Mar-26: `6260 Bad Debt Expense` spikes $310k
*Test:* ask "were there warning signs before the churn?" A good answer connects the AR aging file, the contraction, and the movement file. `Halcyon Freight` is a second, live instance of the same pattern (contraction Jan-26, 97-days-past-due AR from Sep-25 onward, still unresolved).

**F5 · Currency.**
EUR/USD goes 1.089 → 1.038 (-4.7%). MER-EU grows faster in EUR than the USD-reported numbers suggest. `amount_local` and `fx_rate_usd_per_local` are both present, so constant-currency growth is computable. Plan FX was 1.075 (on the Assumptions sheet).

**F6 · Budget variance that nets out but hides volatility.**
S&M is budgeted 22% above actual Jan–Mar-25 (hiring didn't land — see `open_reqs` in the payroll file) and 9% below actual Oct–Dec-25. Full year lands near plan. A quarterly view tells a very different story than an annual one.

**F7 · Working capital deterioration.**
AR: $3.04M (Jun-25) → $3.71M (Sep-25) → $4.77M (Dec-25) → $5.16M (Mar-26) → $4.44M (Jun-26). DSO ≈ 41 → 47 → 55 → 62 → 49. Roughly $915k of the Mar-26 balance sits in three accounts. Bucket columns and the >90 day column are the tell.

**F8 · RIF hidden by a merit cycle.**
Headcount 147 (Dec-25) → 150 (Jan-26) → **139 (Feb-26)** → 153 (Jun-26). Eleven people out of Support, Sales and Marketing. But a 4.2% merit increase lands in Jan-26 and severance of $246k (Feb) + $73k (Mar) posts in the same period, so total payroll does not fall until Apr-26.
*Test:* "did the February reduction save money?" Answer: not in Q1.

**F9 · SaaS tool sprawl.**
Vendor spend $203k (Jan-25) → $539k (Jun-26), +165%. Duplicate categories to find: two marketing automation platforms (HubSpot, Marketo), two sales engagement (Outreach, Salesloft), two observability (Datadog, New Relic), three BI tools (Looker, Tableau, Power BI), two support desks (Zendesk, Intercom), two product analytics (Amplitude, Mixpanel), two wikis (Notion, Confluence). Overlapping tools are owned by *different departments*, which is why nobody caught it. AI API spend compounds ~14%/month off a small base.

**F10 · Burn does not improve.**
ARR grows 40%+ but EBITDA is -$407k in Jan-25 and -$543k in Jun-26. Operating leverage is flat. This is the "so what" question — nothing in the data flags it; it only appears if the tool computes a ratio over time.

---

## Planted scenarios — marketing

**M1 · Auction inflation destroying a channel.**
Paid Search CPC $5.90 (Jan-25) → $11.40 (Feb-26) on a flat ~$100k/month budget. Clicks 16,896 → 8,878. MQLs 163 → 82. Blended CPL $837. Partial recovery from Apr-26.
Corroboration lives in two other files: `Lost to Loop3` is a top-4 lost reason in the attribution file, and Paid Search sessions decline in the web analytics workbook.
*Test:* "why did paid search MQLs halve?" — spend didn't change, price did.

**M2 · The vanity channel and the starved channel.**
| Channel | Spend (18mo) | MQLs | MQL→SQL | Wins | CAC | ARR / spend |
|---|---|---|---|---|---|---|
| Webinars | $620k | 4,314 (2nd highest) | **3.2%** | 3 | $207k | **0.11×** |
| Paid Social – Meta | $277k | 1,267 | 3.9% | 1 | $277k | 0.07× |
| Partner Co-marketing | $463k | 1,083 | **19.3%** | 25 | $18.5k | **3.17×** |
| Events/Field | $1.07M | 2,068 | 15.0% | 31 | $34k | 1.92× |
| Review Sites (G2) | $307k | 1,107 | 13.5% | 14 | $22k | 1.67× |

Webinars looks great on any MQL-volume dashboard and is worthless downstream. Partner is the best channel in the book and was **budgeted 38% below** what was actually spent. The correct recommendation is to move budget from Webinars/Meta into Partner and G2.

**M3 · First-touch vs last-touch conflict.**
On closed-won amount, first-touch credits Paid Search and Content/SEO. Last-touch gives **$7.35M to "Direct / Branded Search"**, which has *zero* first-touch credit — it is harvesting demand created elsewhere. Email/Lifecycle and Retargeting also look 2–3× better on last-touch than first-touch.
*Test:* ask "which channel drives the most revenue?" A good answer refuses the single number and explains the two views disagree and why.

**M4 · Broken tracking that looks like a dead channel.**
`Q4-RETARGET-LINKEDIN`: ~$19–20k spend in Nov-25 and Dec-25 with **0 attributed MQLs**, then 68–89 MQLs/month from Jan-26 at similar spend.
Naive read: "LinkedIn retargeting produced nothing in Q4, cut it." Correct read: the UTM tagging broke; this is a data quality flag, not a performance finding. Note this also drags the whole Retargeting channel's 18-month efficiency down to 0.43×.

**M5 · Volume up, quality down.**
Content/SEO MQLs jump 184 → 615 in Jan-26 (gated ebook campaign) and stay high through Mar-26. SQLs stay flat at 26–29. MQL→SQL falls from ~10% to ~4.3%. Cost per SQL roughly doubles. Reverts in Apr-26.
*Test:* "was the Q1 content push successful?" MQL charts say yes; SQL charts say nothing changed.

**M6 · A missing month.**
**Jul-2025 is absent from `marketing_web_analytics.xlsx`** (documented in the sheet's subtitle: GA4 property migration, never backfilled). Any MoM series, YoY comparison, or total across that workbook has to handle the gap rather than silently treating it as zero.

**M7 · Dirty rows in `marketing_campaign_spend.csv`.**
- A **negative** spend row: -$8,420 in Nov-25 (Google Ads invalid-click credit)
- A **$0.00** spend row in Feb-26 with an explanatory note
- Channel written as `paid search` lowercase on one row
- Campaign name drift: `OpsCon Sponsorship` vs `OpsCon sponsorship ` (trailing space) vs `Q1 OpsCon - deposit only` — all under `campaign_id = EVT-OPSCON`
- Same campaign_id appearing twice in Sep-25 with different names and both legitimate

**M8 · A reconciliation gap you should catch.**
GL account `6020 Marketing Programs & Advertising` totals **$6,694,540**. `marketing_campaign_spend.csv` totals **$6,601,189**. The **$93k difference** is the three rows in M7 (a credit, a $0 accrual timing row, a separately-billed booth invoice). If the tool reports both numbers without reconciling them, that's a finding.

---

## Cross-file questions worth asking

1. What was blended CAC by quarter? (S&M accounts `6010/6015/6020/6030/6040` in the GL ÷ New Logo count in the ARR movement file.)
2. What was net revenue retention in FY25 and in the twelve months to Jun-26? (Expansion + Contraction + Churn against opening ARR. The Northwind churn moves this materially.)
3. Which customer segment has the worst gross retention, and does the marketing mix reflect it?
4. Was the FY25 plan met? Which variances are real and which are classification artifacts? (F2 is the trap.)
5. Show gross margin excluding the contractor reclass. (Requires recognising 6120 and 5210 are the same cost.)
6. What is EMEA's growth in constant currency vs as reported?
7. If we cut Webinars and Meta entirely and moved the budget to Partner and G2, what would the pipeline impact have been? (Uses the funnel file's conversion rates.)
8. Rank the top five cost-reduction opportunities with an estimated annual saving each. (Should surface F9 duplicate tooling, Webinars/Meta spend, and hosting.)

## Entity-scoping tests

Your entity-pooling bug class is worth explicitly regression-testing here. Every finance file carries `entity` with exactly two values, and the marketing files carry `region` (NA/EMEA) which is *not* the same cut. Suggested probes:

- "What was MER-EU gross margin in Q4 2025?" — must not include US hosting.
- Upload the finance set and the marketing set as two separate ingestions, then ask a finance-only question and confirm no marketing rows leak in.
- "How many customers do we have?" — 324 total, but 275 active at Jun-26. Ask both ways and check the tool doesn't silently pick one.
- Ask an MER-EU question after an MER-US question in the same session and confirm the second answer isn't scoped by the first.

## Format-handling tests

- `finance_budget_plan.xlsx` has three title rows above the real header on row 5, a `GL Code` column between labels and data, blank spacer rows between sections, a subtotal row at the bottom, a footnote row, and one account (`7020`) written as **text** with parenthesised negatives.
- `marketing_web_analytics.xlsx` has a totals row using SUM formulas and a missing month column.
- `finance_ar_aging.xlsx` is five separate sheets that need to be stacked into a time series before DSO is computable — the as-of date only exists in the title row and the sheet name.
