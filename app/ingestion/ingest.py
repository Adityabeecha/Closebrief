import io
import sqlite3

import numpy as np
import pandas as pd

from app.schemas import IngestSummary

REQUIRED_COLUMNS = ["period", "metric", "value", "budget"]


class IngestValidationError(Exception):
    """Raised when the uploaded CSV fails validation. Message names the missing column(s)."""


def parse_csv(raw_bytes: bytes) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(raw_bytes))
    except Exception as exc:  # pandas raises many exception subtypes for malformed CSV
        raise IngestValidationError(f"Could not parse CSV: {exc}") from exc

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise IngestValidationError(
            f"CSV is missing required column(s): {', '.join(missing)}"
        )

    if df["metric"].isna().any() or df["metric"].astype(str).str.strip().eq("").any():
        raise IngestValidationError("Column 'metric' contains missing entries")
    df["period"] = df["period"].astype(str)
    if not df["period"].str.fullmatch(r"\d{4}-(0[1-9]|1[0-2])").all():
        raise IngestValidationError("Periods must be valid YYYY-MM months")
    df["metric"] = df["metric"].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["budget"] = pd.to_numeric(df["budget"], errors="coerce")

    if not np.isfinite(df["value"]).all():
        raise IngestValidationError("Column 'value' contains non-numeric or missing entries")
    if not np.isfinite(df["budget"].dropna()).all():
        raise IngestValidationError("Column 'budget' contains non-finite entries")

    return df


def ingest_dataframe(conn: sqlite3.Connection, df: pd.DataFrame, dataset_id: int, *, source="import",
                     actor_id=None, actor_email=None) -> IngestSummary:
    from app.close_control import locked_months, record_changes, skipped_report, snapshot
    from app.datasets import get_or_create_metric, mark_data_changed

    before = snapshot(conn, dataset_id)
    locked = locked_months(conn, dataset_id)
    skipped = {}
    written_pairs = set()
    cur = conn.cursor()
    metric_ids = {
        name: get_or_create_metric(conn, dataset_id, name)
        for name in sorted(df["metric"].unique())
    }

    rows_ingested = 0
    for _, row in df.iterrows():
        if row["period"] in locked:
            skipped[row["period"]] = skipped.get(row["period"], 0) + 1
            continue
        metric_id = metric_ids[row["metric"]]
        written_pairs.add((row["metric"], row["period"]))
        budget = None if pd.isna(row["budget"]) else float(row["budget"])
        cur.execute(
            """
            INSERT INTO metric_values (metric_id, period, value, budget)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(metric_id, period) DO UPDATE SET value=excluded.value, budget=excluded.budget
            """,
            (metric_id, row["period"], float(row["value"]), budget),
        )
        rows_ingested += 1
    record_changes(conn, dataset_id, before, source, actor_id=actor_id, actor_email=actor_email)
    if rows_ingested:
        mark_data_changed(conn, dataset_id)
    conn.commit()

    df.attrs["written_pairs"] = written_pairs
    return IngestSummary(
        rows_ingested=rows_ingested,
        import_report={"locked_skipped": skipped_report(skipped)},
        metrics=sorted(df["metric"].unique().tolist()),
        periods=sorted(df["period"].unique().tolist()),
    )
