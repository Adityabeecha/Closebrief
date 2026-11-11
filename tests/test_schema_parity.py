"""Compare the actual fresh SQLite and fully migrated PostgreSQL catalogs.

Only TEST_DATABASE_URL is used for PostgreSQL. Each parity run creates and
removes its own database, leaving CI's shared test database untouched.
"""

import re
import sqlite3
from collections import defaultdict
from decimal import Decimal
from uuid import uuid4

import pytest
from dbharness import TEST_DATABASE_URL

from app import db

# The single allow-list for app-owned PostgreSQL-only objects.
PG_ONLY_OBJECTS = {
    "table:schema_migrations": "Postgres tracks numbered migrations; SQLite runs idempotent upgrades.",
    "table:context_vectors": "pgvector persists embeddings; SQLite uses the external FAISS index.",
    "extension:vector": "Provides the vector type/index operators used by context_vectors.",
    "policy:datasets.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:context_documents.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:workspace_members.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:workspace_invites.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:uploads.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:llm_calls.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:notification_configs.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:connectors.ws_isolation": "Postgres RLS backs up application workspace predicates.",
    "policy:audit_log.ws_isolation": "Postgres RLS backs up application workspace predicates.",
}


def normalize_default(value, generated=False):
    if generated:
        return "generated-id"
    if value is None:
        return None
    value = str(value).strip()
    # PostgreSQL annotates literals with their types; SQLite does not.
    parts = re.split(r"('(?:''|[^'])*')", value)
    value = "".join(part if index % 2 else re.sub(
        r"::(?:text|character varying|integer|bigint|numeric|boolean|date)\b", "", part, flags=re.I)
        for index, part in enumerate(parts))
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    lowered = re.sub(r"\s+", "", value).lower()
    if lowered in ("current_timestamp", "now()"):
        return "current-timestamp"
    # Remove only expression whitespace/parentheses, retaining every literal
    # and operator. An offset such as now() + interval '1 day' must stay distinct.
    expression = "".join(part if index % 2 else re.sub(r"[\s()]", "", part).lower()
                         for index, part in enumerate(re.split(r"('(?:''|[^'])*')", value)))
    if expression == """to_charnowattimezone'utc','YYYY-MM-DD"T"HH24:MI:SS'""":
        return "current-iso-utc"
    if expression == """strftime'%Y-%m-%dT%H:%M:%S','now'""":
        return "current-iso-utc"
    if lowered in ("false", "true"):
        return ("number", "1" if lowered == "true" else "0")
    if value.startswith("'") and value.endswith("'"):
        return ("text", value[1:-1].replace("''", "'"))
    try:
        return ("number", str(Decimal(value).normalize()))
    except ArithmeticError:
        return ("expression", value)


def type_family(type_name, default=None):
    value = type_name.lower().strip()
    if value in ("integer", "bigint", "smallint", "boolean") or "int" in value:
        return "integer"
    if value in ("real", "numeric", "double precision", "float", "decimal") or value.startswith(("numeric(", "decimal(")):
        return "real/numeric"
    if value in ("blob", "bytea"):
        return "blob/bytea"
    if value == "date":
        return "date"
    if "timestamp" in value or (value == "text" and default == "current-timestamp"):
        return "timestamp"
    # UUID attribution remains string-valued on SQLite.
    if value in ("text", "uuid") or value.startswith(("varchar", "character")):
        return "text"
    raise AssertionError(f"Unmapped SQL type: {type_name}")


def sqlite_catalog(conn):
    catalog = {}
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    for table in tables:
        name = table["name"]
        columns = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
        pk = tuple(c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"])
        values = {}
        for c in columns:
            # INTEGER PRIMARY KEY is SQLite's non-null rowid, even when
            # PRAGMA table_info reports notnull=0. Other PK types allow NULL.
            generated = pk == (c["name"],) and c["type"].upper() == "INTEGER"
            default = normalize_default(c["dflt_value"], generated)
            values[c["name"]] = {"family": type_family(c["type"], default),
                                 "not_null": bool(c["notnull"] or generated), "default": default}
        unique = set()
        for index in conn.execute(f'PRAGMA index_list("{name}")'):
            if index["unique"] and index["origin"] != "pk":
                assert not index["partial"], f"Partial unique index needs explicit comparison: {name}"
                unique.add(tuple(r["name"] for r in conn.execute(f'PRAGMA index_info("{index["name"]}")')))
        foreign = defaultdict(list)
        for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")'):
            foreign[fk["id"]].append(fk)
        keys = set()
        for rows in foreign.values():
            rows.sort(key=lambda r: r["seq"])
            keys.add((tuple(r["from"] for r in rows), rows[0]["table"], tuple(r["to"] for r in rows),
                      rows[0]["on_update"], rows[0]["on_delete"]))
        catalog[name] = {"columns": values, "primary_key": pk, "unique": unique, "foreign_keys": keys}
    return catalog


def postgres_catalog(conn):
    rows = conn.execute("""
        SELECT c.relname AS table_name, a.attname AS name, a.attnotnull AS not_null,
               format_type(a.atttypid, a.atttypmod) AS type, a.attidentity AS identity,
               pg_get_expr(d.adbin, d.adrelid) AS default
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        WHERE n.nspname='public' AND c.relkind='r'
        ORDER BY c.relname, a.attnum
    """).fetchall()
    catalog = {}
    for row in rows:
        name = row["table_name"]
        if f"table:{name}" in PG_ONLY_OBJECTS:
            continue
        table = catalog.setdefault(name, {"columns": {}, "primary_key": (), "unique": set(), "foreign_keys": set()})
        generated = bool(row["identity"]) or str(row["default"]).startswith("nextval(")
        default = normalize_default(row["default"], generated)
        table["columns"][row["name"]] = {"family": type_family(row["type"], default),
                                         "not_null": row["not_null"], "default": default}
    constraints = conn.execute("""
        SELECT c.relname AS table_name, con.contype AS kind, target.relname AS target,
               ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY AS k(num, pos)
                     JOIN pg_attribute a ON a.attrelid=con.conrelid AND a.attnum=k.num ORDER BY k.pos) AS columns,
               ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY AS k(num, pos)
                     JOIN pg_attribute a ON a.attrelid=con.confrelid AND a.attnum=k.num ORDER BY k.pos) AS targets,
               con.confupdtype AS update_action, con.confdeltype AS delete_action
        FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_class target ON target.oid=con.confrelid
        WHERE n.nspname='public' AND con.contype IN ('p', 'f')
    """).fetchall()
    actions = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
    for row in constraints:
        if row["table_name"] not in catalog:
            continue
        table = catalog[row["table_name"]]
        if row["kind"] == "p":
            table["primary_key"] = tuple(row["columns"])
        else:
            table["foreign_keys"].add((tuple(row["columns"]), row["target"], tuple(row["targets"]),
                                       actions[row["update_action"]], actions[row["delete_action"]]))
    # Include unique indexes as well as named UNIQUE constraints (migration 003
    # deliberately implements dataset-scoped uniqueness with indexes).
    indexes = conn.execute("""
        SELECT c.relname AS table_name, i.indpred IS NOT NULL AS partial, i.indexprs IS NOT NULL AS expression,
               ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY AS k(num, pos)
                     JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num
                     WHERE k.pos <= i.indnkeyatts ORDER BY k.pos) AS columns
        FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND i.indisunique AND NOT i.indisprimary
    """).fetchall()
    for row in indexes:
        if row["table_name"] in catalog:
            assert not row["partial"] and not row["expression"], row
            catalog[row["table_name"]]["unique"].add(tuple(row["columns"]))
    return catalog


@pytest.fixture
def sqlite_db(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "parity.db"))
    db.init_db()
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


def test_sqlite_tables_have_primary_keys(sqlite_db):
    catalog = sqlite_catalog(sqlite_db)
    assert catalog
    assert all(table["primary_key"] for table in catalog.values())


def test_declared_sqlite_schema_contains_every_current_column(sqlite_db):
    declared = sqlite3.connect(":memory:")
    declared.row_factory = sqlite3.Row
    try:
        declared.executescript(db._SQLITE_SCHEMA)
        expected = sqlite_catalog(declared)
        actual = sqlite_catalog(sqlite_db)
        assert expected.keys() == actual.keys()
        missing = {t: sorted(actual[t]["columns"].keys() - expected[t]["columns"].keys()) for t in actual}
        assert not {t: columns for t, columns in missing.items() if columns}
    finally:
        declared.close()


def test_sqlite_upgrades_cover_postgres_column_additions():
    pattern = re.compile(r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", re.I)
    upgrades = {match.groups() for stmt in db._SQLITE_MIGRATIONS if isinstance(stmt, str) for match in pattern.finditer(stmt)}
    additions = {match.groups() for path in sorted((db.PG_SCHEMA_PATH.parent / "pg").glob("*.sql"))
                 for match in pattern.finditer(path.read_text(encoding="utf-8"))}
    assert not additions - upgrades


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="requires TEST_DATABASE_URL (Postgres only)")
def test_fresh_sqlite_and_migrated_postgres_schemas_match(sqlite_db, monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from psycopg.rows import dict_row

    from app.config import settings

    name = f"closebrief_parity_{uuid4().hex}"
    test_url = make_conninfo(TEST_DATABASE_URL, dbname=name)
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            monkeypatch.setattr(settings, "database_url", test_url)
            # Uses exactly pg_schema.sql + sorted numbered migrations, including
            # the production migration tracker; never copy data or embed/reindex.
            db.migrate(copy_data=False, reindex=False)
            with psycopg.connect(test_url, row_factory=dict_row) as conn:
                actual = postgres_catalog(conn)
                policies = {f"policy:{r['tablename']}.{r['policyname']}" for r in conn.execute(
                    "SELECT tablename, policyname FROM pg_policies WHERE schemaname='public'").fetchall()}
                assert policies == {key for key in PG_ONLY_OBJECTS if key.startswith("policy:")}
                extensions = {f"extension:{r['extname']}" for r in conn.execute(
                    "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'").fetchall()}
                assert extensions == {key for key in PG_ONLY_OBJECTS if key.startswith("extension:")}
            expected = sqlite_catalog(sqlite_db)
            differences = []
            for table in sorted(expected.keys() | actual.keys()):
                if table not in expected or table not in actual:
                    differences.append(f"{table}: table missing from one backend")
                elif expected[table] != actual[table]:
                    for field in ("columns", "primary_key", "unique", "foreign_keys"):
                        if expected[table][field] != actual[table][field]:
                            differences.append(f"{table}.{field}: SQLite={expected[table][field]!r}; PG={actual[table][field]!r}")
            assert not differences, "\n".join(differences)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.mark.sqlite_only
def test_sqlite_constraint_upgrade_preserves_data_indexes_and_is_idempotent(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "legacy-constraints.db"))
    legacy = sqlite3.connect(settings.db_path)
    legacy.executescript("""
        CREATE TABLE uploads (id TEXT PRIMARY KEY, workspace_id INTEGER, filename TEXT NOT NULL,
                              file_path TEXT, content BLOB, sheets TEXT NOT NULL DEFAULT '[]',
                              chosen_sheet TEXT, profiled_schema TEXT, mapping TEXT, column_signature TEXT,
                              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX uploads_filename ON uploads(filename);
        INSERT INTO uploads (id, filename, content) VALUES ('old', 'old.csv', X'0102');
        CREATE TABLE ai_spend_daily (day DATE PRIMARY KEY, cost_usd NUMERIC NOT NULL DEFAULT 0);
        INSERT INTO ai_spend_daily VALUES ('2020-01-01', 2.5);
    """)
    audit_ddl = db._SQLITE_SCHEMA.split("CREATE TABLE IF NOT EXISTS audit_log (", 1)[1].split("\n);", 1)[0]
    audit_ddl = audit_ddl.replace("(strftime('%Y-%m-%dT%H:%M:%S','now'))", "CURRENT_TIMESTAMP")
    legacy.execute(f"CREATE TABLE audit_log ({audit_ddl})")
    legacy.execute("INSERT INTO audit_log (id, action, entity_type, row_hash, created_at) "
                   "VALUES (7, 'old', 'report', 'keep-hash', '2020-01-01 00:00:00')")
    legacy.execute("INSERT INTO audit_log (id, action, entity_type, row_hash) VALUES (50, 'old', 'report', 'deleted')")
    legacy.execute("DELETE FROM audit_log WHERE id=50")
    legacy.commit()
    legacy.close()
    db.init_db()
    db.init_db()
    conn = db.get_connection()
    try:
        assert conn.execute("SELECT content FROM uploads WHERE id='old'").fetchone()["content"] == b"\x01\x02"
        old = conn.execute("SELECT row_hash, created_at FROM audit_log WHERE id=7").fetchone()
        assert tuple(old) == ("keep-hash", "2020-01-01 00:00:00")
        new = conn.execute("INSERT INTO audit_log (action, entity_type, row_hash) "
                           "VALUES ('new', 'report', 'new-hash') RETURNING id, created_at").fetchone()
        assert new["id"] > 50
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", new["created_at"])
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO uploads (id, filename) VALUES (NULL, 'invalid.csv')")
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='uploads_filename'").fetchone()
        assert conn.execute("SELECT cost_usd FROM ai_spend_daily WHERE day='2020-01-01'").fetchone()["cost_usd"] == 2.5
        for table, column in (("uploads", "id"), ("workspace_invites", "token"),
                              ("app_roles", "user_id"), ("ai_spend_daily", "day")):
            info = {c["name"]: c for c in conn.execute(f"PRAGMA table_info({table})")}
            assert info[column]["notnull"]
    finally:
        conn.close()


@pytest.mark.parametrize("raw,expected", [
    ("CURRENT_TIMESTAMP", "current-timestamp"),
    ("now()", "current-timestamp"),
    ("false", ("number", "0")),
    ("0", ("number", "0")),
    ("'a::text'::text", ("text", "a::text")),
    ("'can''t'::text", ("text", "can't")),
])
def test_default_normalization_keeps_literal_content(raw, expected):
    assert normalize_default(raw) == expected


def test_timestamp_default_offset_is_not_normalized_away():
    current = """to_char((now() AT TIME ZONE 'utc'), 'YYYY-MM-DD"T"HH24:MI:SS')"""
    shifted = """to_char((now() + interval '1 day') AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS')"""
    sqlite_now = """strftime('%Y-%m-%dT%H:%M:%S','now')"""
    assert normalize_default(current) == normalize_default(sqlite_now)
    assert normalize_default(shifted) != normalize_default(sqlite_now)
