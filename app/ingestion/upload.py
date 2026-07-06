"""Stage 1 — Parse (Addendum v1.1 Section 1.3).

Accepts .csv/.xlsx/.xls bytes, persists the raw file under data/uploads/,
records the upload row, and returns a preview + sheet list. No type coercion
assumptions at this stage.
"""

import io
import json
import sqlite3
import uuid
from pathlib import Path

import pandas as pd

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ROWS = 100_000  # hardening: cap rows so a huge file can't exhaust memory
UPLOAD_DIR = Path("./data/uploads")


class UploadError(Exception):
    pass


def _read_frame(raw: bytes, filename: str, sheet: str | None = None) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=True)
    if name.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0, dtype=str, engine="openpyxl")
    if name.endswith(".xls"):
        # Legacy .xls needs xlrd, which isn't a dependency — fail clearly rather
        # than with an opaque engine error.
        raise UploadError(
            "Legacy .xls files aren't supported. Re-save as .xlsx or export to CSV."
        )
    raise UploadError(f"Unsupported file type: {filename}. Use .csv or .xlsx.")


def list_sheets(raw: bytes, filename: str) -> list[str]:
    if filename.lower().endswith(".xlsx"):
        with pd.ExcelFile(io.BytesIO(raw), engine="openpyxl") as xl:
            return [str(s) for s in xl.sheet_names]
    return []


def load_upload_frame(conn: sqlite3.Connection, upload_id: str, sheet: str | None = None) -> pd.DataFrame:
    row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise UploadError(f"Upload {upload_id} not found")
    raw = Path(row["file_path"]).read_bytes()
    chosen = sheet or row["chosen_sheet"]
    df = _read_frame(raw, row["filename"], chosen)
    # Drop fully-empty rows/columns that Excel exports love to include.
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    return df


def column_signature(columns: list[str]) -> str:
    """Stable signature of a file format: hash of sorted, normalized names."""
    import hashlib

    normalized = sorted(str(c).strip().lower() for c in columns)
    return hashlib.sha256("|".join(normalized).encode()).hexdigest()[:16]


def create_upload(conn: sqlite3.Connection, raw: bytes, filename: str) -> dict:
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError("File exceeds the 10 MB upload limit")
    if not raw.strip():
        raise UploadError("Uploaded file is empty")

    upload_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix or ".csv"
    file_path = UPLOAD_DIR / f"{upload_id}{suffix}"
    file_path.write_bytes(raw)

    sheets = list_sheets(raw, filename)
    df = _read_frame(raw, filename, sheets[0] if sheets else None)
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        raise UploadError("File parsed but contains no data rows")
    if len(df) > MAX_ROWS:
        raise UploadError(f"File has {len(df):,} rows; the limit is {MAX_ROWS:,}")

    signature = column_signature(list(df.columns))
    conn.execute(
        """
        INSERT INTO uploads (id, filename, file_path, sheets, chosen_sheet, column_signature)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (upload_id, filename, str(file_path), json.dumps(sheets), sheets[0] if sheets else None, signature),
    )
    conn.commit()

    template = conn.execute(
        "SELECT id FROM mapping_templates WHERE column_signature = ?", (signature,)
    ).fetchone()

    preview = df.head(20).fillna("").astype(str)
    return {
        "upload_id": upload_id,
        "filename": filename,
        "sheets": sheets,
        "columns": [str(c) for c in df.columns],
        "preview": preview.to_dict(orient="records"),
        "row_count": int(len(df)),
        "suggested_template_id": template["id"] if template else None,
    }
