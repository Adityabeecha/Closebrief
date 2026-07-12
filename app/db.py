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

_SQLITE_SCHEMA = """
-- v4.0 multi-tenancy: a workspace is a tenant. Data roots (datasets,
-- context_documents) carry workspace_id; everything else hangs off a dataset.
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    monthly_budget_usd REAL,
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
    token TEXT PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
    role TEXT NOT NULL DEFAULT 'analyst',
    email TEXT,
    accepted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_upload_id TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    domain TEXT NOT NULL DEFAULT 'fpa',
    is_demo INTEGER NOT NULL DEFAULT 0,
    workspace_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER REFERENCES datasets(id),
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Uncategorized',
    unit TEXT NOT NULL DEFAULT 'USD',
    direction_good TEXT NOT NULL DEFAULT 'up',
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS uploads (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
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
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notification_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
_SQLITE_MIGRATIONS = [
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
]

PG_SCHEMA_PATH = Path(__file__).parent / "migrations" / "pg_schema.sql"


# --------------------------------------------------------------------------
# Postgres wrapper: lets sqlite3-style call sites run unchanged on psycopg.
# --------------------------------------------------------------------------

# INSERTs whose callers read cur.lastrowid — auto-append RETURNING id on PG.
_LASTROWID_TABLES = re.compile(
    r"^\s*INSERT(\s+OR\s+IGNORE)?\s+INTO\s+"
    r"(context_documents|generated_reports|feedback|datasets|metrics"
    r"|scheduled_jobs|digest_runs|workspaces)\b",
    re.I,
)
_OR_IGNORE = re.compile(r"^(\s*INSERT)\s+OR\s+IGNORE\s+", re.I)


def _translate(sql: str) -> str:
    """sqlite dialect -> postgres dialect for the statements this app uses."""
    # Escape literal % (e.g. LIKE '%-digest') to %% first so psycopg doesn't
    # read it as a client-side placeholder, THEN turn ? into %s placeholders.
    out = sql.replace("%", "%%").replace("?", "%s")
    if _OR_IGNORE.match(out):
        out = _OR_IGNORE.sub(r"\1 ", out)
        out = out.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return out


class _PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class PostgresConnection:
    backend = "postgres"

    def __init__(self, raw, pool=None):
        self._raw = raw
        self._pool = pool

    def execute(self, sql: str, params=()):
        pg_sql = _translate(sql)
        wants_id = bool(_LASTROWID_TABLES.match(sql)) and "RETURNING" not in pg_sql.upper()
        if wants_id:
            pg_sql = pg_sql.rstrip().rstrip(";") + " RETURNING id"
        cur = self._raw.execute(pg_sql, params or None)
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
                self._raw.rollback()
            except Exception:
                try:
                    self._pool.putconn(self._raw, close=True)
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
        return PostgresConnection(pool.getconn(), pool)
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
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists (fresh DBs get it from _SCHEMA)
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
                       "price", "budget_quantity", "budget_price", "dimensions"]),
    ("computed_facts", ["id", "metric_id", "period", "value", "prior_value", "mom_pct",
                        "yoy_pct", "budget_var_abs", "budget_var_pct", "trend", "is_anomaly"]),
    ("context_documents", ["id", "type", "title", "body", "metric_tags", "effective_date", "created_at"]),
    ("generated_reports", ["id", "metric_id", "period", "narrative", "sources", "confidence",
                           "faithfulness", "prompt_tokens", "completion_tokens", "cost_usd",
                           "latency_ms", "prompt_version", "created_at"]),
    ("feedback", ["id", "report_id", "action", "edited_text", "reason", "created_at"]),
    ("uploads", ["id", "filename", "file_path", "sheets", "chosen_sheet",
                 "profiled_schema", "mapping", "column_signature", "created_at"]),
    ("mapping_templates", ["id", "column_signature", "mapping", "kpi_config", "created_at"]),
    ("kpi_configs", ["id", "source_metric", "display_name", "category", "unit",
                     "direction_good", "budget_source", "created_at"]),
]

_IDENTITY_TABLES = [
    "metrics", "metric_values", "computed_facts", "context_documents",
    "generated_reports", "feedback", "mapping_templates", "kpi_configs",
]


def migrate(copy_data: bool = True, reindex: bool = True) -> None:
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
                    rows = src.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
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
