# PRD Addendum v1.1 — Flexible Ingestion, KPI Selection & UI Corrections

**Applies to:** Narrative BI / Closebrief (extends PRD v1.0)
**Reader:** Claude Code — this is a delta spec. Implement these changes on top of the existing codebase.
**Reason:** v1.0 assumed hardcoded CSV columns (`period, metric, value, budget`). Real FP&A data does not look like that. This addendum replaces rigid ingestion with a detect → map → pick flow, adds chart data to the output, adds Price/Volume/Mix decomposition, and removes auth from the MVP.

---

## 0. Priority of changes (do in this order)

1. **[BREAKING] Replace rigid ingestion** with the flexible ingestion pipeline (Section 1). This is the core change.
2. **Add KPI selection** step and the mapping-template concept (Section 2).
3. **Remove the login/auth page** from the MVP (Section 3).
4. **Add chart data** to the output contract so the UI can render graphs (Section 4).
5. **Add Price/Volume/Mix decomposition** as an advanced deterministic computation (Section 5).
6. **Update UI** to include the Import & Mapping screen and charts (Section 6).

Do not touch the faithfulness guard, retrieval, or generation logic — those stay. This addendum changes what feeds *into* compute, and what comes *out* for charts.

---

## 1. Flexible Ingestion Pipeline (replaces v1.0 Section: Ingestion)

### 1.1 The rule
Ingestion must NOT assume any fixed column names or layout. It profiles whatever file is uploaded, proposes a mapping, lets the user confirm/override, and normalizes to a canonical internal format. Support **CSV and Excel (.xlsx)**.

### 1.2 Canonical internal format
Everything downstream operates on a normalized long-format table:
```
period (date)  |  metric (str)  |  value (float)  |  budget (float, nullable)  |  dimensions (json, nullable)
```
All layouts get converted to this. Compute, retrieval, and generation are unchanged because they read this canonical table.

### 1.3 Stage 1 — Parse
- Accept `.csv`, `.xlsx`, `.xls`.
- For Excel with multiple sheets, list sheet names and let the user pick the sheet (default: first non-empty sheet).
- Load into a pandas DataFrame without type coercion assumptions.
- Endpoint: `POST /ingest/upload` → returns an `upload_id` + raw preview (first 20 rows) + sheet list.

### 1.4 Stage 2 — Schema detection (profiling)
Implement `app/ingestion/profiler.py` with a function that, for each column, returns:
```json
{
  "column_name": "Jan-2025",
  "dtype": "numeric | date | text | mixed",
  "sample_values": ["...", "..."],
  "distinct_count": 12,
  "null_pct": 0.0,
  "guessed_role": "period | metric_label | measure | budget | dimension | ignore"
}
```
**Role-guessing heuristics:**
- **period**: column parses as dates, OR its *name* matches month/quarter/year patterns (`Jan-25`, `2025-03`, `Q1 2025`, `FY24`). Also detect the **wide-format case**: many columns whose *names* are periods (see 1.6).
- **measure**: numeric dtype, high-ish cardinality, name doesn't look like budget.
- **budget**: numeric AND name matches `budget|plan|forecast|target|bud|fcst` (case-insensitive).
- **metric_label**: text, low-to-mid cardinality, name matches `metric|account|line item|kpi|gl`.
- **dimension**: text, low cardinality, name matches `department|region|entity|cost center|product|segment`.
- **ignore**: everything else (notes, ids).
Return guesses as *proposals* — never final.

### 1.5 Stage 3 — Column mapping (user confirms)
- Endpoint: `GET /ingest/{upload_id}/schema` → returns the profiled columns + guessed roles.
- Endpoint: `POST /ingest/{upload_id}/mapping` → accepts the user's confirmed mapping:
```json
{
  "layout": "long | wide",
  "period_col": "Month",                // long layout
  "metric_col": "Account",              // long layout
  "value_col": "Actual",                // long layout
  "budget_col": "Budget",               // optional
  "dimension_cols": ["Department"],     // optional
  "wide_period_cols": ["Jan-25","Feb-25"], // wide layout only
  "wide_value_label": "Revenue"            // wide layout: what the row values represent
}
```
- Validate the mapping (e.g. a `value_col` must be numeric). Return a 422 naming the specific problem, not a generic error.

### 1.6 Handle BOTH layouts
- **Long layout**: already tidy. Rename mapped columns to canonical and load.
- **Wide layout** (months as columns — the most common real FP&A export): `pandas.melt` the `wide_period_cols` into rows, parsing each column name into a `period`. This is mandatory; wide is the common case.

### 1.7 Stage 4 — Normalize & store
- Convert to the canonical long format (1.2).
- Parse all periods to real dates (store as first-of-month for monthly data).
- Coerce values to float, stripping currency symbols/commas/parentheses-as-negative (`(1,200)` → `-1200`).
- Persist: the raw file, the profiled schema, the confirmed mapping, and the normalized table (keyed by `upload_id`).

### 1.8 Acceptance criteria
- Given a **wide** Excel with months as columns, When mapped as `layout=wide`, Then it normalizes to long format with one row per period.
- Given a **long** CSV with arbitrary column names, When mapped, Then it normalizes correctly.
- Given a value like `$1,234.50` or `(500)`, Then it parses to `1234.50` and `-500`.
- Given a mapping where `value_col` is text, Then a 422 names that column as the problem.
- No column name is hardcoded anywhere in ingestion.

---

## 2. KPI Selection & Mapping Templates (new)

### 2.1 KPI selection
After mapping, the user picks which metrics to actually track and how each is treated.
- Endpoint: `GET /ingest/{upload_id}/metrics` → distinct metric names found (from `metric_col`, or the `wide_value_label` for wide files).
- Endpoint: `POST /kpis` → user selects/configures KPIs:
```json
{
  "kpis": [
    {
      "source_metric": "Net Revenue",
      "display_name": "Net Revenue",
      "category": "Revenue",
      "unit": "USD",
      "direction_good": "up",          // up = higher is better; down = lower is better (e.g. OpEx)
      "budget_source": "Budget"        // which budget maps to this KPI, if any
    }
  ]
}
```
- Provide a small built-in **KPI library** (Revenue, Gross Margin %, OpEx, EBITDA, Cash Runway) the user can select from to auto-fill `category/unit/direction_good`.
- `direction_good` drives whether a delta renders green or red in the UI — a −8% on OpEx is *good*, on Revenue is *bad*. Compute must respect this.

### 2.2 Mapping templates (reusable)
- When a mapping + KPI config is saved, persist it as a **template** keyed by a signature of the file's columns (sorted column-name hash).
- Endpoint: `POST /ingest/upload` should check if an existing template matches the uploaded file's signature; if so, return `suggested_template_id` so the user can skip mapping ("We recognized this format — apply saved mapping?").
- This makes month-2 uploads one click. Store templates in a `mapping_templates` table.

### 2.3 Acceptance criteria
- Given a saved template, When a file with the same column signature is uploaded, Then the template is suggested and mapping can be skipped.
- Given a KPI with `direction_good: down`, When it improves (value decreases), Then the delta is flagged positive/green in the output.

---

## 3. Remove Auth from MVP

- Remove the login/landing gate. The app opens directly to the Insights Dashboard (or the Import screen if no data is loaded yet).
- Delete or disable any auth middleware, login route, and session checks in the MVP build.
- Keep auth as a **v1 concern only** (Supabase Auth), noted in the backlog — do not implement now.

---

## 4. Chart Data in the Output Contract (add graphs)

The v1.0 output had no chart data, so the UI had nothing to plot. Extend the insight schema and add endpoints.

### 4.1 Extend the insight object
Add a `chart_data` block to each insight:
```json
"chart_data": {
  "trend": [ {"period": "2024-04", "value": 3.9, "budget": 4.1}, ... ],  // 12 months
  "budget_vs_actual": {"actual": 4.2, "budget": 4.57},
  "variance_bridge": [                                                    // see Section 5
    {"component": "Volume", "impact": -0.20},
    {"component": "Mix",    "impact": -0.15},
    {"component": "Price",  "impact": -0.02}
  ]
}
```

### 4.2 UI charts to render (frontend)
- **Trend line**: 12-month actual vs budget (line + dashed budget line). Small sparkline on each dashboard card; full-size on Metric Detail.
- **Budget-vs-Actual bar**: two bars per metric.
- **Variance bridge (waterfall)**: on Metric Detail, showing how the total variance decomposes (from Section 5).

Use a lightweight chart lib (Recharts if React; Plotly/Chart.js if plain HTML). No new backend deps required — chart data is computed in the deterministic layer.

---

## 5. Price / Volume / Mix Decomposition (advanced deterministic compute)

This is the signature FP&A analysis and a strong differentiator. Implement in `app/compute/pvm.py`, deterministic only.

### 5.1 Requirement
When a metric has underlying quantity + price detail (or when the user provides unit/volume columns), decompose a revenue-type variance into:
- **Volume effect** = (actual qty − budget qty) × budget price
- **Price effect** = (actual price − budget price) × actual qty
- **Mix effect** = residual from shifts in composition across sub-items
Reconciliation must hold: `volume + price + mix ≈ total variance` (assert within tolerance).

### 5.2 Graceful degradation
- If the data lacks quantity/price detail, skip P/V/M and return `variance_bridge: null`. Do not fabricate a decomposition. The narrative must not claim price/volume/mix causes when the bridge is null.

### 5.3 Feed into narrative (safely)
- When a bridge exists, pass its components (as computed numbers) into the generation prompt as additional facts. The LLM may reference them but — per the unchanged faithfulness guard — every number must still trace to a computed fact.

### 5.4 Acceptance criteria
- Given quantity + price detail, Then P/V/M components sum to total variance within tolerance.
- Given no such detail, Then `variance_bridge` is null and no P/V/M claim appears in the narrative.

---

## 6. UI Screens (updated set)

Lock these five screens. **Hand the Claude Design mockup to Claude Code as the visual reference** so the built UI matches the mockup (the prior drift came from generating them separately).

1. **Import & Mapping** (NEW — highest priority)
   - Upload (CSV/Excel) → sheet picker (Excel) → profiled-columns table with a role dropdown per column (pre-filled with guesses) → layout toggle (long/wide) → KPI picker (with library) → "Save as template."
   - If a template matches: banner "Recognized format — apply saved mapping?"
2. **Insights Dashboard**
   - One card per KPI: value, deltas (colored per `direction_good`), **sparkline trend**, source chips, confidence badge, verified-numbers badge, 👍/👎/edit.
3. **Metric Detail**
   - Full 12-month trend line (actual vs budget), budget-vs-actual bars, **variance bridge waterfall**, full narrative, sources, Q&A box.
4. **Context Library** — CRUD for business context (unchanged).
5. **Executive Digest** — top movers (unchanged).
- **No login page.** App opens to Dashboard (or Import if empty).

---

## 7. Updated Repo Structure (additions)

```
app/
├── ingestion/
│   ├── upload.py          # NEW: parse CSV/Excel, sheet handling
│   ├── profiler.py        # NEW: column profiling + role guessing
│   ├── mapping.py         # NEW: apply confirmed mapping, long/wide normalize
│   └── templates.py       # NEW: mapping-template save/match by signature
├── compute/
│   ├── kpis.py            # unchanged deterministic KPI math
│   └── pvm.py             # NEW: price/volume/mix decomposition
├── kpis/
│   └── library.py         # NEW: built-in KPI definitions
tests/
├── test_profiler.py       # NEW: role-guessing on messy inputs
├── test_mapping.py        # NEW: long + wide normalization, value parsing
└── test_pvm.py            # NEW: decomposition reconciliation
```
New tables: `mapping_templates`, `kpi_configs`, plus store raw upload + profiled schema.

---

## 8. Updated Milestones (delta)

- **M1.5 — Flexible ingestion:** Sections 1–2 (upload, profile, map, KPI select, templates) with tests. Exit: upload a **wide Excel** and a **messy long CSV**, map both via the API, get a correct normalized table and computed KPIs. *Do this before any UI work.*
- **M2.5 — Charts + P/V/M:** Sections 4–5. Exit: insight output includes `chart_data`; P/V/M reconciles or degrades to null.
- **M3 (revised) — UI:** Section 6 five screens, no auth, charts rendered, mockup used as reference.

---

## 9. Explicitly out of scope (still)
Live ERP/warehouse connectors, forecasting, multi-tenancy, auth, background job queue. Note connectors as the eventual replacement for manual upload (that's how commercial tools like Abacum/Cube work), but do not build them now.

---

*End of Addendum v1.1.*
