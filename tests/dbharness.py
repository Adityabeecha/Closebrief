"""Test database harness: lets the SAME suite run on SQLite (default) or, when
TEST_DATABASE_URL is set (CI's Postgres job), on Postgres — so the tests catch
Postgres-only bugs (dialect, BOOLEAN vs INTEGER, missing cursor.rowcount) that
SQLite silently accepts. See .github/workflows/ci.yml (the `postgres` job)."""

import os

# Set by CI's Postgres job to a reachable Postgres URL. Empty in local dev and
# the default CI job -> SQLite, exactly as before.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

_pg_schema_ready = False


def use_test_db(monkeypatch):
    """Point the app at the test database for one test.

    Drop-in replacement for `monkeypatch.setattr(settings, "database_url", "")`
    in the per-file fixtures: SQLite by default (each test already gets its own
    tmp db_path), but Postgres when TEST_DATABASE_URL is set — building the
    schema once and truncating every table so each test starts clean."""
    from app.config import settings

    if not TEST_DATABASE_URL:
        monkeypatch.setattr(settings, "database_url", "")
        return

    monkeypatch.setattr(settings, "database_url", TEST_DATABASE_URL)
    _ensure_pg_schema()
    _truncate_pg()


def _ensure_pg_schema():
    """Build the Postgres schema once per session (idempotent migrate)."""
    global _pg_schema_ready
    if _pg_schema_ready:
        return
    from app.db import migrate
    migrate(copy_data=False, reindex=False)
    _pg_schema_ready = True


def _dispose_pool():
    """Drop the app's connection pool between tests. Some tests call
    get_connection() without closing it (harmless on SQLite's file handles);
    on Postgres that leaves a pooled connection idle-in-transaction holding
    locks that would block the next test's TRUNCATE. Disposing the pool — and
    terminating any leftover backend in _truncate_pg — releases those locks."""
    from app import db as _db
    pool, _db._pg_pool = _db._pg_pool, None
    if pool is not None:
        try:
            pool.close(timeout=2.0)
        except Exception:
            pass


def _truncate_pg():
    """Wipe all data (keeping schema + migration history) so each test starts
    from a clean slate, mirroring SQLite's fresh-file-per-test isolation.
    RESTART IDENTITY resets serial ids so lastrowid values match SQLite's."""
    import psycopg

    from app.config import session_pool_url
    _dispose_pool()
    with psycopg.connect(session_pool_url(), connect_timeout=20) as conn:
        # Terminate any connection the previous test leaked (idle-in-transaction
        # holders would deadlock the TRUNCATE below), then bound the wait so a
        # genuine stuck lock fails loudly instead of hanging CI.
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        conn.execute("SET lock_timeout = '10s'")
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'schema_migrations'"
        ).fetchall()
        tables = [r[0] for r in rows]
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            conn.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")
        conn.commit()
