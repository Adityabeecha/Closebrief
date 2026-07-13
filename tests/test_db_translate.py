"""SQLite→Postgres SQL translation (the layer that lets one query string run on
both backends). Guards the recurring cross-DB bug class."""

from app.db import _translate


def test_placeholders_and_percent():
    assert _translate("SELECT * FROM t WHERE a = ?") == "SELECT * FROM t WHERE a = %s"
    # Literal % (LIKE) is escaped so psycopg doesn't read it as a placeholder.
    assert _translate("WHERE p NOT LIKE '%-digest'") == "WHERE p NOT LIKE '%%-digest'"


def test_null_safe_is_becomes_distinct_from():
    # SQLite's `col IS ?` is null-safe equality; Postgres needs IS NOT DISTINCT FROM.
    assert _translate("WHERE workspace_id IS ?") == "WHERE workspace_id IS NOT DISTINCT FROM %s"
    assert _translate("WHERE a IS NOT ?") == "WHERE a IS DISTINCT FROM %s"
    # A normal IS NULL is untouched.
    assert _translate("WHERE a IS NULL") == "WHERE a IS NULL"


def test_insert_or_ignore_becomes_on_conflict():
    out = _translate("INSERT OR IGNORE INTO t (a) VALUES (?)")
    assert out.startswith("INSERT INTO t") and out.endswith("ON CONFLICT DO NOTHING")
