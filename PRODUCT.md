# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **Finance operators** (FP&A leads, finance managers, controllers) at the end of each monthly close. Their job: turn last month's KPIs into variance commentary leadership can read, without spending days writing it by hand, and without a number they can't defend.
- **Readers of that commentary** (CFOs, executives, board members) who need the story behind the numbers quickly and want to trust it.
- **Reviewers of the work** (hiring managers, engineers) evaluating CloseBrief as a deployed portfolio product. CloseBrief serves real finance users and portfolio review at the same time; neither audience is secondary.

## Product Purpose

CloseBrief reads a company's financial exports (CSV/Excel from NetSuite, QuickBooks, spreadsheets, any layout), computes every KPI deterministically, and drafts plain-English, audit-ready variance commentary the moment the books close. Analysts add business context (pricing changes, campaigns, one-offs) so the commentary can explain the *why*, then review, edit and approve it before it goes to leadership as a dashboard, executive digest, or board pack.

Success: a finance lead goes from closed books to reviewed, defensible commentary in minutes, and a reader can trace every figure and every claim back to its source.

## Positioning

The AI never computes the numbers. Every figure is calculated in code first; the model only writes the words; a faithfulness guard checks every number in the prose against the computed facts and flags anything it can't trace. Explanations cite the analyst's own context notes. This is the promise neighbouring "AI BI" tools cannot truthfully make, and it must stay visible.

## Operating Context

- Monthly close rhythm: import the month, review anomalies and movers, generate narratives, review/approve, share.
- Inputs: CSV/XLSX exports in long or wide layouts, budgets joined from separate files, GL data with account groups.
- Outputs: insights dashboard (monthly / quarterly / yearly), metric detail with trend, budget-vs-actual and price/volume/mix bridge, executive digest, board pack (print/PDF), PowerPoint export, Slack/email/webhook notifications and schedules.
- Roles: admin, analyst, executive, viewer; workspaces and datasets; a public read-only demo; optional guest access.
- Used at a desk during close, often under deadline; also read on phones by executives.

## Capabilities and Constraints

- Backend: FastAPI, Postgres/pgvector (Supabase) or SQLite/FAISS locally, OpenAI or Anthropic behind interfaces.
- Frontend: single-page vanilla JS (`ui/web/index.html` + `ui/web/app.js`), no build step, no framework; self-hosted fonts and vendored libraries (Chart.js, pptxgen, Supabase, Sentry). Keep it that way unless explicitly changed.
- Every view must keep working: deep links (`#metric=…&period=…`), keyboard access, light and dark themes, phones at 375px.
- `docs/ui-standard.md` holds the agreed interaction standard (formats, status words, three states per screen, charts).
- Undecided: pricing, plans, and whether there is a paid tier.

## Brand Commitments

- Name: **Closebrief**.
- Core promise: the AI never computes the numbers; every figure is checked.
- Brand colour: a green (currently ledger green `#1e6e50`) stays the brand colour. Everything else about the visual world may change.

## Evidence on Hand

- Eval harness results: Recall@5 100%, Faithfulness 100%, Groundedness 100% on a 15-case golden set (`eval/fixtures/golden.json`, `python -m eval.run`).
- Demo datasets: "Demo — Sample FP&A" and "Demo — Marketing Funnel" (`app/demo.py`), plus `data/sample_fpa.csv` and `data/sample_marketing.csv` (synthetic).
- A live demo mode (read-only, no account).
- Absent, and must not be invented: customers, customer logos, testimonials, usage numbers, pricing, security certifications, integrations beyond the export formats listed above.

## Product Principles

1. **Numbers are facts, words are drafts.** The interface always distinguishes computed figures from generated prose and shows how each was checked.
2. **Show the why, with receipts.** Every explanation points at its source; nothing asks to be trusted blindly.
3. **The analyst stays in charge.** Generation is a draft; review, edit and approval are first-class.
4. **Fit the close.** Speed and clarity during a deadline beat novelty; the most important movement is always first.

## Accessibility & Inclusion

WCAG 2.1 AA in both themes: 4.5:1 text contrast, labelled inputs, full keyboard operation, tap targets of at least 24px (about 40px on touch), honoured reduced-motion.
