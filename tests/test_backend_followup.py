"""Input and delivery boundary regressions; no real network calls."""

import json

import pandas as pd
import pytest
from test_review_regressions import conn as _isolated_conn
from test_review_regressions import metric

conn = _isolated_conn


@pytest.mark.parametrize("value", ["inf", "-inf", "1e999"])
def test_mapping_rejects_nonfinite_numbers(value):
    from app.ingestion.mapping import MappingError, coerce_value
    with pytest.raises(MappingError):
        coerce_value(value)


@pytest.mark.parametrize("field", ["quantity_col", "price_col", "budget_quantity_col", "budget_price_col"])
def test_missing_mapping_column_is_validation_error(field):
    from app.ingestion.mapping import MappingError, MappingSpec, normalize
    df = pd.DataFrame({"month": ["2025-01"], "amount": [1]})
    mapping = MappingSpec(layout="long", period_col="month", value_col="amount", **{field: "absent"})
    with pytest.raises(MappingError):
        normalize(df, mapping)


def test_ambiguous_budget_labels_do_not_pick_arbitrary_metric(conn):
    from app.ingestion.mapping import match_budget_to_dataset
    ds, first = metric(conn, "Sales-A")
    _, second = metric(conn, "Sales A", ds)
    for mid in (first, second):
        conn.execute("INSERT INTO metric_values(metric_id,period,value,budget) VALUES (?,'2025-01',10,5)", (mid,))
    conn.commit()
    result = match_budget_to_dataset(conn, pd.DataFrame({"metric": ["sales a"], "period": ["2025-01"], "value": [99]}), ds)
    assert result["matched"] == 0
    assert result["unmatched_metrics"] == ["sales a"]
    assert [r[0] for r in conn.execute("SELECT budget FROM metric_values ORDER BY metric_id")] == [5, 5]


def test_connector_does_not_echo_exception_credentials(conn):
    from app.connectors import create_connector, list_connectors, sync_connector
    cid = create_connector(conn, "csv_url", "source", {"url": "https://example.test/data"}, "data", None)
    def fail(url):
        raise ValueError("server returned private-fixture-value")
    result = sync_connector(conn, cid, None, fetch=fail)
    assert result["status"] == "error"
    assert "private-fixture-value" not in json.dumps(result)
    assert "private-fixture-value" not in json.dumps(list_connectors(conn, None))


def test_review_email_escapes_untrusted_labels(monkeypatch):
    from types import SimpleNamespace

    from app.notifications.channels import EmailChannel
    from app.routers import review
    messages = []
    monkeypatch.setattr(review, "bg_executor", SimpleNamespace(submit=lambda action: action()))
    monkeypatch.setattr(EmailChannel, "_send", lambda self, subject, body: messages.append(body))
    review._email_review_nudge_async("reviewer@example.test", "<img src=x>", "2025-01", "<b>sender</b>")
    assert len(messages) == 1
    assert "<img src=x>" not in messages[0]
    assert "&lt;img src=x&gt;" in messages[0]
    assert "<b>sender</b>" not in messages[0]


def test_upload_database_failure_does_not_leave_raw_file(conn, tmp_path, monkeypatch):
    import sqlite3

    from app.ingestion import upload
    folder = tmp_path / "uploads"
    monkeypatch.setattr(upload, "UPLOAD_DIR", folder)
    conn.execute("CREATE TRIGGER reject_upload BEFORE INSERT ON uploads "
                 "BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END")
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        upload.create_upload(conn, b"A,B\n1,2\n", "data.csv")
    assert not list(folder.iterdir())
    assert conn.execute("SELECT count(*) FROM uploads").fetchone()[0] == 0


@pytest.mark.parametrize("values", [([1, None], [2, 3]), ([1, 1], [2, 3])])
def test_profiler_keeps_measures_without_valid_correlation(values):
    import warnings

    from app.ingestion.profiler import profile_columns

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        result = profile_columns(pd.DataFrame({"revenue": values[0], "cost": values[1]}))
    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]
    assert all(col["redundant_with"] is None for col in result["columns"])


@pytest.mark.parametrize("url", [
    "https://[broken/path?token=private-fixture-value",
    "https://example.test:invalid/path?token=private-fixture-value",
    "https://user:private-fixture-value@example.test/path",
])
def test_invalid_delivery_url_is_safe_validation_error(url, monkeypatch):
    from app.notifications import channels

    monkeypatch.setattr(channels.socket, "getaddrinfo", lambda *a, **kw: [])
    with pytest.raises(channels.NotificationError) as caught:
        channels.validate_public_url(url)
    assert "private-fixture-value" not in str(caught.value)


def test_delivery_url_rejects_empty_dns_result(monkeypatch):
    from app.notifications import channels

    monkeypatch.setattr(channels.socket, "getaddrinfo", lambda *a, **kw: [])
    with pytest.raises(channels.NotificationError):
        channels.validate_public_url("https://example.test/path")
