# CloseBrief

CloseBrief is a month-end close workspace for turning financial data into
reviewed commentary and a leadership digest. Numbers are computed in code;
AI drafts the commentary around those facts and the business context you add.

Every generated figure is checked against supplied facts or source context at
its written precision. Checks also compare rise/fall wording with the direction
of the computed change. The check covers the numbers; the reasons come from your
own context notes. You inspect, edit, and approve each draft. The digest uses
approved commentary; sending is blocked until every KPI in the close has approved
commentary.

CloseBrief does the last mile of month-end, the commentary. It isn't a planning,
budgeting, or BI tool: your model stays in Excel or your FP&A system.

## Main flow

**Import -> review -> add reasons -> approve -> send digest**

1. Import a CSV or Excel workbook, map its columns, and choose the KPIs to track.
2. Review the month's values, changes against plan, trends, and flagged movements.
3. Add reasons as context notes for the relevant metrics and period, then draft
   or revise commentary using those notes and computed facts.
4. Review the figures and explanation, then approve each KPI's commentary.
   Edits are recorded in version history.
5. Preview the digest of approved commentary and who it goes to, then send it
   through configured email, Slack, or webhook channels once all KPIs are approved.
6. Lock the close so later imports or syncs can't change its figures or commentary.

## What a close shows

- **Each KPI:** actual, plan and forecast, variances, the prior month and year,
  and fiscal year to date, in a review queue or as a statement grouped by category.
- **Why it moved:** contributors by department, product, or region when the import
  has a breakdown column, and anomalies judged against the same month last year.
- **Needs a reason:** anomalies and variances past the dataset's threshold
  (10% by default) are listed until a context note explains them.
- **Control:** an import receipt with per-KPI totals, a log of every figure an
  import changed, and an Activity history of approvals, sends, and locks.
- **Outputs:** the leadership digest, a print-ready board pack, PDF, PowerPoint,
  and an Excel workbook of the whole close.

## Run locally

Use Python 3.11 or later. From the repository root (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
```

On macOS/Linux, activate with `source .venv/bin/activate`.

Open [the app](http://127.0.0.1:8000/app), or the
[development API docs](http://127.0.0.1:8000/docs). With no `DATABASE_URL`, storage
uses local SQLite and FAISS. With no sign-in provider configured, local requests
use the development admin identity. Import and computed facts work without an
LLM key; drafting new AI commentary requires a configured provider and API key
(`OPENAI_API_KEY`, or `LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`).

Import [data/sample_fpa.csv](data/sample_fpa.csv) to try the editable close flow;
[data/sample_marketing.csv](data/sample_marketing.csv) is another sample.
To seed separate demo datasets, set this before starting the server:

```powershell
$env:DEMO_MODE = "true"
```

When authentication is configured, **Open the demo close** on the home page
opens those samples as a read-only visitor. Demo seeding runs in the background
at startup; it does not replace uploaded datasets. See [DEPLOY.md](DEPLOY.md)
for identity-provider and service configuration.

## Tests and evaluation

```powershell
python -m pytest -q
python -m ruff check app/ tests/ eval/
python -m eval.run --no-llm
```

The test suite uses isolated test databases. Leave `TEST_DATABASE_URL` unset for
SQLite; use only a dedicated disposable database to exercise PostgreSQL tests.
The retrieval-only eval skips LLM generation. For a fully offline run, ensure
`OPENAI_API_KEY` is absent from both shell and dotenv settings: embeddings use
OpenAI whenever that key is configured, and local hashing otherwise.
`python -m eval.run` also evaluates generated commentary and Q&A; it requires
an LLM key and makes API calls.

## Architecture

- **API:** FastAPI serves the app, auth middleware, and feature routers.
- **UI:** Vanilla JavaScript, HTML, and CSS in `ui/web/` call the same API.
- **Import and compute:** `app/ingestion/` maps uploads; `app/compute/` calculates
  KPI values, deltas, trends, anomalies, and bridges with pandas/numpy.
- **Context:** `app/context/` and `app/retrieval/` store notes and retrieve
  relevant context using FAISS locally or pgvector when configured.
- **Drafting and checks:** `app/generation/` wraps the configured LLM provider,
  builds prompts, and checks numeric precision, direction, and grounding.
- **Review and digest:** `app/review.py` records decisions and versions;
  `app/digest/` assembles approved commentary without another AI call.
- **Storage and operations:** SQLite/PostgreSQL hold workspace-scoped data;
  optional Redis caches responses, while scheduling, notifications, retention,
  audit history, and usage metering live in their respective `app/` modules.

Deployment instructions are in [DEPLOY.md](DEPLOY.md). Product requirements and
the historical QA review are under [docs/](docs/).
