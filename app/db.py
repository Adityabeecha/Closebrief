"""Storage layer (v1.1): SQLite for local dev, Supabase Postgres when
DATABASE_URL is set — behind one connection wrapper so call sites keep the
sqlite3 style they were written in (`?` placeholders, `conn.execute`,
`cur.lastrowid`, dict-style rows).

Runtime Postgres traffic goes through the transaction pooler (port 6543) via
a psycopg connection pool. DDL/migrations use the session pooler (port 5432)
— see `python -m app.db migrate`.
"""

import re
import sqlite3
from pathlib import Path

from app.config import session_pool_url, settings

_IMPORT_RECEIPTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS import_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    workspace_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    kind TEXT NOT NULL CHECK (kind IN ('import', 'budget_join', 'connector_sync')),
    filename TEXT NOT NULL,
    mapping TEXT NOT NULL,
    report TEXT NOT NULL,
    kpi_totals TEXT NOT NULL
);
"""

# SQLite has no row-level security DDL. Migration 026 extends PostgreSQL RLS;
# SQLite continues to enforce tenancy through the request-scoped SQL predicates.
_SQLITE_SCHEMA = """
-- v4.0 multi-tenancy: a workspace is a tenant. Data roots (datasets,
-- context_documents) carry workspace_id; everything else hangs off a dataset.
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    monthly_budget_usd REAL,
    retention_days INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    user_id TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'analyst',   -- per-workspace: admin|analyst|executive
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS workspace_invites (
    token TEXT NOT NULL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    role TEXT NOT NULL DEFAULT 'analyst',
    email TEXT,
    accepted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    data_changed_at TIMESTAMP,
    source_upload_id TEXT,
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'SGD', 'CHF')),
    fiscal_year_start INTEGER NOT NULL DEFAULT 1 CHECK (fiscal_year_start BETWEEN 1 AND 12),
    is_active INTEGER NOT NULL DEFAULT 0,
    domain TEXT NOT NULL DEFAULT 'fpa',
    is_demo INTEGER NOT NULL DEFAULT 0,
    workspace_id INTEGER,
    uploaded_by TEXT,
    uploaded_by_email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER REFERENCES datasets(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Uncategorized',
    unit TEXT NOT NULL DEFAULT 'USD',
    direction_good TEXT NOT NULL DEFAULT 'up',
    -- A stable code from the source system (GL account code, SKU, ...),
    -- captured from the mapping's id_col at ingest. Lets a later budget-only
    -- upload join onto this metric by code, which doesn't drift the way a
    -- text label can ("Content/Seo" vs "Content/SEO").
    external_id TEXT,
    UNIQUE(dataset_id, name)
);

CREATE TABLE IF NOT EXISTS metric_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL REFERENCES metrics(id),
    period TEXT NOT NULL,
    value REAL NOT NULL,
    budget REAL,
    quantity REAL,
    price REAL,
    budget_quantity REAL,
    budget_price REAL,
    dimensions TEXT,
    source_rows INTEGER NOT NULL DEFAULT 1,
    UNIQUE(metric_id, period)
);

CREATE TABLE IF NOT EXISTS computed_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL REFERENCES metrics(id),
    period TEXT NOT NULL,
    value REAL NOT NULL,
    prior_value REAL,
    mom_pct REAL,
    yoy_pct REAL,
    budget_var_abs REAL,
    budget_var_pct REAL,
    trend TEXT,
    is_anomaly INTEGER NOT NULL DEFAULT 0,
    UNIQUE(metric_id, period)
);

CREATE TABLE IF NOT EXISTS context_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metric_tags TEXT NOT NULL DEFAULT '',
    effective_date TEXT,
    is_demo INTEGER NOT NULL DEFAULT 0,
    workspace_id INTEGER,
    created_by TEXT,
    updated_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS generated_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL REFERENCES metrics(id),
    period TEXT NOT NULL,
    narrative TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL,
    faithfulness TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    prompt_version TEXT,
    user_id TEXT,
    user_email TEXT,
    review_status TEXT,
    assigned_to TEXT,
    assigned_email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT NOT NULL PRIMARY KEY,
    workspace_id INTEGER,
    filename TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'SGD', 'CHF')),
    file_path TEXT,
    content BLOB,
    sheets TEXT NOT NULL DEFAULT '[]',
    chosen_sheet TEXT,
    profiled_schema TEXT,
    mapping TEXT,
    column_signature TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mapping_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    column_signature TEXT UNIQUE NOT NULL,
    mapping TEXT NOT NULL,
    kpi_config TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kpi_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER REFERENCES datasets(id),
    source_metric TEXT NOT NULL,
    display_name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Uncategorized',
    unit TEXT NOT NULL DEFAULT 'USD',
    direction_good TEXT NOT NULL DEFAULT 'up',
    budget_source TEXT,
    aggregation_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset_id, source_metric)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES generated_reports(id),
    action TEXT NOT NULL,
    edited_text TEXT,
    reason TEXT,
    user_id TEXT,
    user_email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pvm_bridges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_id INTEGER NOT NULL REFERENCES metrics(id),
    period TEXT NOT NULL,
    volume REAL NOT NULL,
    price REAL NOT NULL,
    mix REAL NOT NULL,
    total REAL NOT NULL,
    n_items INTEGER NOT NULL DEFAULT 1,
    UNIQUE(metric_id, period)
);

-- Global totals contain no tenant identifiers and intentionally have no RLS.
-- Keep the global AI cap meaningful while llm_calls is workspace-isolated.
CREATE TABLE IF NOT EXISTS ai_spend_daily (
    day DATE NOT NULL PRIMARY KEY,
    cost_usd NUMERIC NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    user_id TEXT,
    workspace_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_roles (
    user_id TEXT NOT NULL PRIMARY KEY,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notification_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER,
    channel TEXT NOT NULL,            -- 'email' | 'slack' | 'webhook'
    config TEXT NOT NULL,             -- JSON: {recipients, schedule, events, ...}
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,               -- 'digest' | 'anomaly_scan'
    cadence TEXT NOT NULL,            -- 'daily' | 'weekly' | 'monthly'
    dataset_id INTEGER,               -- NULL = active real dataset at run time
    config TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT,
    last_status TEXT,                 -- 'ok' | 'skipped' | 'error'
    last_error TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,             -- 'ok' | 'skipped' | 'error'
    detail TEXT,
    latency_ms REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- v5.0 custom/derived KPIs: a formula over other metrics, materialized into
-- metric_values so it gets full delta/trend/anomaly + narrative treatment.
CREATE TABLE IF NOT EXISTS derived_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER,
    name TEXT NOT NULL,
    formula TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT 'USD',
    category TEXT NOT NULL DEFAULT 'Derived',
    direction_good TEXT NOT NULL DEFAULT 'up',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(dataset_id, name)
);

-- v4.0 live data connectors: scheduled syncs replace manual CSV upload.
CREATE TABLE IF NOT EXISTS connectors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER,
    kind TEXT NOT NULL,               -- 'csv_url' | 'google_sheets'
    name TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    dataset_name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- v5.0 collaborative review: narrative version history (diffs computed on read).
CREATE TABLE IF NOT EXISTS report_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    narrative TEXT,
    editor_id TEXT,
    editor_email TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- v4.0 immutable audit trail (append-only, hash-chained per workspace).
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER,
    actor_id TEXT,
    actor_email TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    summary TEXT,
    row_hash TEXT NOT NULL,
    prev_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now'))
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER,
    period TEXT NOT NULL,
    top_n INTEGER NOT NULL,
    items TEXT NOT NULL,              -- JSON snapshot of the digest items
    cost_usd REAL,
    trigger TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'scheduled'
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns added after first release; applied to pre-existing SQLite DBs on startup.
_SQLITE_SCHEMA += _IMPORT_RECEIPTS_SCHEMA


def _migrate_dataset_freshness(conn: sqlite3.Connection) -> None:
    """Backfill existing datasets once, when the timestamp column is introduced."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(datasets)")}
    if "data_changed_at" in columns:
        return
    conn.execute("ALTER TABLE datasets ADD COLUMN data_changed_at TIMESTAMP")
    if "created_at" in columns:
        conn.execute("UPDATE datasets SET data_changed_at = created_at WHERE data_changed_at IS NULL")


_SQLITE_MIGRATIONS = [
    _migrate_dataset_freshness,
    "ALTER TABLE datasets ADD COLUMN data_changed_at TIMESTAMP",
    "ALTER TABLE datasets ADD COLUMN fiscal_year_start INTEGER NOT NULL DEFAULT 1 CHECK (fiscal_year_start BETWEEN 1 AND 12)",
    _IMPORT_RECEIPTS_SCHEMA,
    "ALTER TABLE metric_values ADD COLUMN source_rows INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE datasets ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD' "
    "CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'SGD', 'CHF'))",
    "ALTER TABLE uploads ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD' "
    "CHECK (currency IN ('USD', 'EUR', 'GBP', 'INR', 'JPY', 'CAD', 'AUD', 'SGD', 'CHF'))",
    "ALTER TABLE notification_configs ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE uploads ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE uploads ADD COLUMN content BLOB",
    "ALTER TABLE metric_values ADD COLUMN quantity REAL",
    "ALTER TABLE metric_values ADD COLUMN price REAL",
    "ALTER TABLE metric_values ADD COLUMN budget_quantity REAL",
    "ALTER TABLE metric_values ADD COLUMN budget_price REAL",
    "ALTER TABLE metric_values ADD COLUMN dimensions TEXT",
    "ALTER TABLE generated_reports ADD COLUMN prompt_tokens INTEGER",
    "ALTER TABLE generated_reports ADD COLUMN completion_tokens INTEGER",
    "ALTER TABLE generated_reports ADD COLUMN cost_usd REAL",
    "ALTER TABLE generated_reports ADD COLUMN latency_ms REAL",
    "ALTER TABLE generated_reports ADD COLUMN prompt_version TEXT",
    # v1.3 dataset scoping (fresh SQLite DBs get these from _SQLITE_SCHEMA;
    # pre-existing dev DBs pick up the columns here — the UNIQUE change only
    # matters for fresh DBs, which is fine since SQLite is dev-only).
    "ALTER TABLE metrics ADD COLUMN dataset_id INTEGER",
    "ALTER TABLE kpi_configs ADD COLUMN dataset_id INTEGER",
    # v1.2 user attribution (Phase 3)
    "ALTER TABLE generated_reports ADD COLUMN user_id TEXT",
    "ALTER TABLE generated_reports ADD COLUMN user_email TEXT",
    "ALTER TABLE feedback ADD COLUMN user_id TEXT",
    "ALTER TABLE feedback ADD COLUMN user_email TEXT",
    "ALTER TABLE context_documents ADD COLUMN created_by TEXT",
    "ALTER TABLE context_documents ADD COLUMN updated_by TEXT",
    "ALTER TABLE datasets ADD COLUMN uploaded_by TEXT",
    "ALTER TABLE datasets ADD COLUMN uploaded_by_email TEXT",
    "ALTER TABLE llm_calls ADD COLUMN user_id TEXT",
    # v2.1 multi-domain
    "ALTER TABLE datasets ADD COLUMN domain TEXT NOT NULL DEFAULT 'fpa'",
    # v3.0 demo isolation
    "ALTER TABLE datasets ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE kpi_configs ADD COLUMN aggregation_type TEXT",
    # v3.1 demo isolation for context docs (retrieval + library + conflicts)
    "ALTER TABLE context_documents ADD COLUMN is_demo INTEGER NOT NULL DEFAULT 0",
    # v3.0 scheduler observability (dev DBs that already have scheduled_jobs)
    "ALTER TABLE scheduled_jobs ADD COLUMN last_status TEXT",
    "ALTER TABLE scheduled_jobs ADD COLUMN last_error TEXT",
    "ALTER TABLE scheduled_jobs ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0",
    # v4.0 multi-tenancy (data roots carry workspace_id)
    "ALTER TABLE datasets ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE context_documents ADD COLUMN workspace_id INTEGER",
    # v4.0 per-workspace usage metering + spend limits
    "ALTER TABLE llm_calls ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE workspaces ADD COLUMN plan TEXT NOT NULL DEFAULT 'free'",
    "ALTER TABLE workspaces ADD COLUMN monthly_budget_usd REAL",
    "ALTER TABLE workspaces ADD COLUMN retention_days INTEGER",
    # v5.0 collaborative review workflow on reports
    "ALTER TABLE generated_reports ADD COLUMN review_status TEXT",
    "ALTER TABLE generated_reports ADD COLUMN assigned_to TEXT",
    "ALTER TABLE generated_reports ADD COLUMN assigned_email TEXT",
    # budget-join by external id (GL code / SKU) rather than label alone
    "ALTER TABLE metrics ADD COLUMN external_id TEXT",
    # Backfill once per UTC day on legacy SQLite databases. Never recompute an
    # existing total: retention may have removed the detailed call rows.
    "INSERT INTO ai_spend_daily (day, cost_usd) "
    "SELECT date('now'), COALESCE(SUM(cost_usd), 0) FROM llm_calls "
    "WHERE created_at >= date('now') ON CONFLICT (day) DO NOTHING",
]

PG_SCHEMA_PATH = Path(__file__).parent / "migrations" / "pg_schema.sql"


def _migrate_sqlite_uploads(conn: sqlite3.Connection) -> None:
    """SQLite cannot drop NOT NULL in place; rebuild only legacy uploads tables."""
    columns = conn.execute("PRAGMA table_info(uploads)").fetchall()
    if not any(col["name"] == "file_path" and col["notnull"] for col in columns):
        return
    ddl = _SQLITE_SCHEMA.split("CREATE TABLE IF NOT EXISTS uploads (", 1)[1].split(");", 1)[0]
    names = ", ".join(f'"{col["name"]}"' for col in columns)
    conn.execute("SAVEPOINT migrate_uploads")
    try:
        conn.execute(f"CREATE TABLE uploads_new ({ddl})")
        conn.execute(f"INSERT INTO uploads_new ({names}) SELECT {names} FROM uploads")
        conn.execute("DROP TABLE uploads")
        conn.execute("ALTER TABLE uploads_new RENAME TO uploads")
        conn.execute("RELEASE SAVEPOINT migrate_uploads")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT migrate_uploads")
        conn.execute("RELEASE SAVEPOINT migrate_uploads")
        raise


def _migrate_sqlite_constraints(conn: sqlite3.Connection) -> None:
    """SQLite needs a rebuild to tighten existing PKs or change a default.

    These five tables have no incoming foreign keys. Preserve every stored
    value, custom index/trigger, and AUTOINCREMENT high-water mark.
    """
    desired = sqlite3.connect(":memory:")
    desired.row_factory = sqlite3.Row
    try:
        desired.executescript(_SQLITE_SCHEMA)
        for table in ("workspace_invites", "uploads", "app_roles", "ai_spend_daily", "audit_log"):
            actual = {c["name"]: c for c in conn.execute(f'PRAGMA table_info("{table}")')}
            wanted = {c["name"]: c for c in desired.execute(f'PRAGMA table_info("{table}")')}
            needs_pk = any(c["pk"] and c["notnull"] and not actual[name]["notnull"] for name, c in wanted.items() if name in actual)
            needs_default = table == "audit_log" and actual["created_at"]["dflt_value"] != wanted["created_at"]["dflt_value"]
            if not needs_pk and not needs_default:
                continue
            ddl = _SQLITE_SCHEMA.split(f"CREATE TABLE IF NOT EXISTS {table} (", 1)[1].split("\n);", 1)[0]
            columns = ", ".join(f'"{name}"' for name in actual)
            objects = conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index', 'trigger') AND sql IS NOT NULL",
                (table,),
            ).fetchall()
            sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
            conn.execute("SAVEPOINT sqlite_constraints")
            try:
                conn.execute(f'CREATE TABLE "{table}_parity" ({ddl})')
                conn.execute(f'INSERT INTO "{table}_parity" ({columns}) SELECT {columns} FROM "{table}"')
                conn.execute(f'DROP TABLE "{table}"')
                conn.execute(f'ALTER TABLE "{table}_parity" RENAME TO "{table}"')
                for obj in objects:
                    conn.execute(obj["sql"])
                if sequence is not None:
                    current = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
                    high_water = max(sequence["seq"], current["seq"] if current else 0)
                    conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
                    conn.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (table, high_water))
                conn.execute("RELEASE SAVEPOINT sqlite_constraints")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT sqlite_constraints")
                conn.execute("RELEASE SAVEPOINT sqlite_constraints")
                raise
    finally:
        desired.close()


# Existing dev databases need the same constraints/default as fresh databases.
# Constraint changes cannot be represented by SQLite ALTER COLUMN statements.
_SQLITE_MIGRATIONS.append(_migrate_sqlite_constraints)


# --------------------------------------------------------------------------
# Postgres wrapper: lets sqlite3-style call sites run unchanged on psycopg.
# --------------------------------------------------------------------------

# SQL literals use doubled single quotes for an embedded quote.
_SQL_PARTS = re.compile(r"('(?:''|[^'])*')")
_IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
_INSERT_TABLE = re.compile(
    rf"^\s*INSERT(?:\s+OR\s+IGNORE)?\s+INTO\s+({_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})?)",
    re.I,
)
_OR_IGNORE = re.compile(r"^(\s*INSERT)\s+OR\s+IGNORE\s+", re.I)


def _translate(sql: str, has_params: bool = True) -> str:
    """Translate SQLite dialect tokens outside single-quoted SQL literals.

    Literal contents remain SQL, including ? and %. Psycopg's percent escaping
    inside literals is a separate execution-time binding concern.
    """
    parts = _SQL_PARTS.split(sql)
    for index in range(0, len(parts), 2):
        part = parts[index]
        if has_params:
            part = part.replace("%", "%%")
        part = part.replace(" IS NOT ?", " IS DISTINCT FROM ?").replace(" IS ?", " IS NOT DISTINCT FROM ?")
        parts[index] = part.replace("?", "%s")
    out = "".join(parts)
    if _OR_IGNORE.match(out):
        out = _OR_IGNORE.sub(r"\1 ", out)
        out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return out


def _driver_sql(sql: str, has_params: bool) -> str:
    """Psycopg scans percent placeholders even inside SQL literal quotes.

    Escaping these percents only for parameter binding preserves their actual
    database values; unparameterised SQL must be sent verbatim.
    """
    if not has_params:
        return sql
    return "".join(part.replace("%", "%%") if index % 2 else part
                   for index, part in enumerate(_SQL_PARTS.split(sql)))


class _PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


# table -> has a serial/identity id; see PostgresConnection._has_generated_id.
_GENERATED_ID_CACHE: dict[str, bool] = {}


class PostgresConnection:
    backend = "postgres"

    def __init__(self, raw, pool=None):
        self._raw = raw
        self._pool = pool

    def _set_rls_scope(self) -> None:
        """Set the app.workspace_id GUC from the current tenant scope so RLS
        policies filter to it. Empty string = 'unscoped' (policies allow all).
        Failure must prevent the connection from reaching application queries."""
        try:
            from app.datasets import current_workspace
            ws = current_workspace()
            self._raw.execute("SELECT set_config('app.workspace_id', %s, false)",
                              (str(int(ws)) if ws is not None else "",))
            self._raw.commit()
        except Exception:
            try:
                self._raw.rollback()
            except Exception:
                pass
            raise

    def _has_generated_id(self, table: str) -> bool:
        """Derive lastrowid support from serial/identity columns in this DB.

        Answers are cached for the process, yes and no alike: tables only change
        when migrations run, and migrate() clears the cache. Caching only "yes"
        cost every insert into an id-less table (each metric_values row of an
        import) an extra catalogue round trip.
        """
        cached = _GENERATED_ID_CACHE.get(table)
        if cached is not None:
            return cached
        row = self._raw.execute(
            """SELECT to_regclass(%s) IS NOT NULL AS present,
                      EXISTS (SELECT 1 FROM pg_attribute a
                              LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                              WHERE a.attrelid=to_regclass(%s) AND a.attname='id'
                                AND a.attnum>0 AND NOT a.attisdropped
                                AND a.atttypid IN ('int2'::regtype, 'int4'::regtype, 'int8'::regtype)
                                AND (a.attidentity <> '' OR
                                     strpos(COALESCE(pg_get_expr(d.adbin, d.adrelid), ''), 'nextval(') = 1)
                      ) AS generated""",
            (table, table),
        ).fetchone()
        generated = bool(row and row["generated"])
        # A table that doesn't exist yet isn't cached, so it's looked up again once created.
        if row and row["present"]:
            _GENERATED_ID_CACHE[table] = generated
        return generated

    def execute(self, sql: str, params=()):
        pg_sql = _translate(sql, has_params=bool(params))
        insert = _INSERT_TABLE.match(sql)
        outside = "".join(_SQL_PARTS.split(pg_sql)[::2])
        wants_id = bool(insert and not re.search(r"\bRETURNING\b", outside, re.I)
                        and self._has_generated_id(insert.group(1)))
        if wants_id:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
        cur = self._raw.execute(_driver_sql(pg_sql, has_params=bool(params)), params or None)
        lastrowid = None
        if wants_id:
            row = cur.fetchone()
            lastrowid = row["id"] if row else None
        return _PgCursor(cur, lastrowid)

    def cursor(self):
        return self

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self._pool is not None:
            # Return to the pool clean; psycopg resets state on putconn. If the
            # rollback fails the connection is poisoned (e.g. aborted txn on a
            # broken socket) — discard it instead of handing the fault to the
            # next request that draws it from the pool.
            try:
                # Discard unfinished application work before scope cleanup.
                self._raw.rollback()
                if settings.rls_enabled:
                    self._raw.execute("SELECT set_config('app.workspace_id', '', false)")
                    self._raw.commit()
            except Exception:
                try:
                    self._raw.close()
                    self._pool.putconn(self._raw)
                except Exception:
                    pass
                return
            self._pool.putconn(self._raw)
        else:
            self._raw.close()


_pg_pool = None


def _get_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _pg_pool = ConnectionPool(
            settings.database_url,
            min_size=0,
            max_size=5,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
    return _pg_pool


def get_connection():
    """Postgres (pooled) when DATABASE_URL is set; SQLite otherwise."""
    if settings.database_url:
        pool = _get_pg_pool()
        conn = PostgresConnection(pool.getconn(), pool)
        if settings.rls_enabled:
            try:
                conn._set_rls_scope()
            except Exception:
                conn.close()
                raise
        return conn
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Note: sqlite3.Connection is a C type and rejects arbitrary attributes,
    # so the backend is identified via getattr(conn, "backend", "sqlite").
    return conn


def init_db() -> None:
    if settings.database_url:
        if settings.auto_migrate:
            # Always run migrate(): it applies the base schema (IF NOT EXISTS) and
            # any *pending* versioned migrations from migrations/pg/, tracked in
            # schema_migrations — so new migrations reach an already-provisioned DB,
            # not just a brand-new one. Idempotent; no data copy / reindex.
            migrate(copy_data=False, reindex=False)
            return
        # auto_migrate off: just verify the base schema is present.
        conn = get_connection()
        try:
            row = conn.execute("SELECT to_regclass('public.metrics') AS t").fetchone()
            if row["t"] is None:
                raise RuntimeError("Postgres schema missing. Run: python -m app.db migrate")
        finally:
            conn.close()
        return

    conn = get_connection()
    try:
        conn.executescript(_SQLITE_SCHEMA)
        for stmt in _SQLITE_MIGRATIONS:
            if callable(stmt):
                stmt(conn)
                continue
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                is_alter = bool(re.match(r"\s*ALTER\b", stmt, re.I))
                duplicate_column = is_alter and message.startswith("duplicate column name:")
                existing_object = not is_alter and bool(re.fullmatch(r"(?:table|index|view|trigger) .+ already exists", message))
                if not (duplicate_column or existing_object):
                    raise
        _migrate_sqlite_uploads(conn)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Migration CLI: python -m app.db migrate [--copy-data] [--reindex]
# --------------------------------------------------------------------------

_COPY_TABLES = [
    # (table, columns) in FK-safe insert order
    ("metrics", ["id", "name", "category", "unit", "direction_good"]),
    ("metric_values", ["id", "metric_id", "period", "value", "budget", "quantity",
                       "price", "budget_quantity", "budget_price", "dimensions", "source_rows"]),
    ("computed_facts", ["id", "metric_id", "period", "value", "prior_value", "mom_pct",
                        "yoy_pct", "budget_var_abs", "budget_var_pct", "trend", "is_anomaly"]),
    ("context_documents", ["id", "type", "title", "body", "metric_tags", "effective_date", "created_at"]),
    ("generated_reports", ["id", "metric_id", "period", "narrative", "sources", "confidence",
                           "faithfulness", "prompt_tokens", "completion_tokens", "cost_usd",
                           "latency_ms", "prompt_version", "created_at"]),
    ("feedback", ["id", "report_id", "action", "edited_text", "reason", "created_at"]),
    ("uploads", ["id", "filename", "file_path", "content", "sheets", "chosen_sheet",
                 "profiled_schema", "mapping", "column_signature", "created_at"]),
    ("mapping_templates", ["id", "column_signature", "mapping", "kpi_config", "created_at"]),
    ("kpi_configs", ["id", "source_metric", "display_name", "category", "unit",
                     "direction_good", "budget_source", "created_at"]),
]

_IDENTITY_TABLES = [
    "metrics", "metric_values", "computed_facts", "context_documents",
    "generated_reports", "feedback", "mapping_templates", "kpi_configs",
]


def _sqlite_copy_rows(src: sqlite3.Connection, table: str, cols: list[str]) -> list:
    """Copy both upgraded and legacy SQLite sources without losing actuals."""
    selected = list(cols)
    if table == "metric_values":
        existing = {r["name"] for r in src.execute("PRAGMA table_info(metric_values)")}
        if "source_rows" not in existing:
            selected = ["1 AS source_rows" if c == "source_rows" else c for c in cols]
    return src.execute(f"SELECT {', '.join(selected)} FROM {table}").fetchall()


def migrate(copy_data: bool = True, reindex: bool = True) -> None:
    _GENERATED_ID_CACHE.clear()   # tables may gain or lose generated ids below
    import psycopg
    from psycopg.rows import dict_row

    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set — nothing to migrate to.")

    ddl_url = session_pool_url()
    print("[1/3] Applying schema via session pooler ...")
    with psycopg.connect(ddl_url, connect_timeout=20) as conn:
        # Base schema (idempotent), then versioned migrations tracked in
        # schema_migrations so future ALTERs are managed, not ad hoc.
        # Skip the (large) base-schema re-execute when it's already present —
        # keeps every startup cheap; only pending versioned migrations run.
        base_present = conn.execute("SELECT to_regclass('public.metrics')").fetchone()[0] is not None
        if not base_present:
            conn.execute(PG_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   version TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        applied = {
            r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        versioned_dir = PG_SCHEMA_PATH.parent / "pg"
        for path in sorted(versioned_dir.glob("*.sql")):
            if path.stem in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
            )
            print(f"      applied {path.stem}")
        conn.commit()
    print("      schema OK")

    if copy_data and Path(settings.db_path).exists():
        print(f"[2/3] Copying data from {settings.db_path} ...")
        src = sqlite3.connect(settings.db_path)
        src.row_factory = sqlite3.Row
        with psycopg.connect(ddl_url, connect_timeout=20, row_factory=dict_row) as dst:
            for table, cols in _COPY_TABLES:
                try:
                    rows = _sqlite_copy_rows(src, table, cols)
                except sqlite3.OperationalError:
                    print(f"      {table}: skipped (not in SQLite)")
                    continue
                if not rows:
                    print(f"      {table}: 0 rows")
                    continue
                placeholders = ", ".join(["%s"] * len(cols))
                stmt = (
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
                    f"ON CONFLICT DO NOTHING"
                )
                with dst.cursor() as cur:
                    cur.executemany(stmt, [tuple(r[c] for c in cols) for r in rows])
                print(f"      {table}: {len(rows)} rows")
            for table in _IDENTITY_TABLES:
                dst.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1))"
                )
            dst.commit()
        src.close()
    else:
        print("[2/3] Data copy skipped")

    if reindex:
        print("[3/3] Reindexing context vectors into pgvector ...")
        from app.context.embeddings import get_embedder
        from app.context.pg_vector_store import PgVectorStore
        from app.context.store import ContextStore

        conn = get_connection()
        try:
            embedder = get_embedder()
            vs = PgVectorStore(dim=embedder.dim)
            n = ContextStore(conn, embedder, vs).reindex_all()
            print(f"      {n} context documents embedded into pgvector")
        finally:
            conn.close()
    else:
        print("[3/3] Reindex skipped")

    print("Migration complete.")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        migrate(
            copy_data="--no-copy" not in sys.argv,
            reindex="--no-reindex" not in sys.argv,
        )
    else:
        print("Usage: python -m app.db migrate [--no-copy] [--no-reindex]")
