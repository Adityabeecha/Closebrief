from app.cache import InMemoryCache, make_insight_key
from app.db import _translate

# ---------- cache behavior ----------

def test_set_get_roundtrip():
    c = InMemoryCache()
    c.set("k1", {"narrative": "hello"}, ttl_seconds=60)
    assert c.get("k1") == {"narrative": "hello"}


def test_miss_returns_none():
    assert InMemoryCache().get("nope") is None


def test_ttl_expiry(monkeypatch):
    import time as time_mod

    c = InMemoryCache()
    c.set("k1", {"v": 1}, ttl_seconds=10)
    real_time = time_mod.time()
    monkeypatch.setattr("app.cache.time.time", lambda: real_time + 11)
    assert c.get("k1") is None


def test_namespace_bump_invalidates():
    c = InMemoryCache()
    c.set("k1", {"v": 1}, ttl_seconds=60)
    c.bump_namespace()
    assert c.get("k1") is None


# ---------- key hashing ----------

def _key(**overrides):
    base = dict(
        metric="Net Revenue", period="2025-03",
        fact_payload={"value": 100.0, "deltas": {"mom_pct": -5.0}},
        context_ids_and_bodies=[(1, "Pricing", "prices rose")],
        prompt_version="v1", provider="openai", model="gpt-4o",
    )
    base.update(overrides)
    return make_insight_key(**base)


def test_identical_inputs_same_key():
    assert _key() == _key()


def test_fact_change_changes_key():
    assert _key() != _key(fact_payload={"value": 200.0, "deltas": {"mom_pct": -5.0}})


def test_context_change_changes_key():
    assert _key() != _key(context_ids_and_bodies=[(1, "Pricing", "prices FELL")])


def test_prompt_version_changes_key():
    assert _key() != _key(prompt_version="v2")


def test_model_changes_key():
    assert _key() != _key(model="gpt-4o-mini")


# ---------- sqlite -> postgres SQL translation ----------

def test_placeholders_translated():
    assert _translate("SELECT * FROM t WHERE a = ? AND b = ?") == \
        "SELECT * FROM t WHERE a = %s AND b = %s"


def test_insert_or_ignore_translated():
    out = _translate("INSERT OR IGNORE INTO metrics (name) VALUES (?)")
    assert out == "INSERT INTO metrics (name) VALUES (%s) ON CONFLICT DO NOTHING"


def test_on_conflict_update_untouched():
    sql = "INSERT INTO t (a) VALUES (?) ON CONFLICT(a) DO UPDATE SET a=excluded.a"
    assert _translate(sql) == sql.replace("?", "%s")


def test_literal_percent_escaped():
    # LIKE patterns keep a literal % which psycopg needs doubled to %%.
    out = _translate("SELECT * FROM t WHERE v NOT LIKE '%-digest' AND a = ?")
    assert "%%-digest" in out
    assert out.endswith("a = %s")
