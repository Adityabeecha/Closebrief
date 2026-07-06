import io
import sqlite3

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

    df["period"] = df["period"].astype(str)
    df["metric"] = df["metric"].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["budget"] = pd.to_numeric(df["budget"], errors="coerce")

    if df["value"].isna().any():
        raise IngestValidationError("Column 'value' contains non-numeric or missing entries")

    return df


def ingest_dataframe(conn: sqlite3.Connection, df: pd.DataFrame, dataset_id: int) -> IngestSummary:
    from app.datasets import get_or_create_metric

    cur = conn.cursor()
    metric_ids = {
        name: get_or_create_metric(conn, dataset_id, name)
        for name in sorted(df["metric"].unique())
    }
    conn.commit()

    rows_ingested = 0
    for _, row in df.iterrows():
        metric_id = metric_ids[row["metric"]]
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
    conn.commit()

    return IngestSummary(
        rows_ingested=rows_ingested,
        metrics=sorted(df["metric"].unique().tolist()),
        periods=sorted(df["period"].unique().tolist()),
    )
