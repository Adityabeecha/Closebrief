"""Stage 1 — Parse (Addendum v1.1 Section 1.3).

Accepts .csv/.xlsx/.xls bytes, persists the raw file under data/uploads/,
records the upload row, and returns a preview + sheet list. No type coercion
assumptions at this stage.
"""

import io
import json
import re
import sqlite3
import uuid
from pathlib import Path

import pandas as pd

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_ROWS = 100_000  # hardening: cap rows so a huge file can't exhaust memory
UPLOAD_DIR = Path("./data/uploads")
_HEADER_SCAN_ROWS = 15  # real FP&A exports put the header within a handful of title rows

_PERIOD_IN_TEXT = re.compile(
    r"\d{1,2}[\s\-/](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-/]\d{2,4}"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-/]\d{2,4}"
    r"|\d{4}-\d{2}",
    re.I,
)


class UploadError(Exception):
    pass


def _row_looks_numeric(row: pd.Series) -> bool:
    """True if most of a row's non-null cells parse as a number — the signal
    that separates a data row from a header/label row, both of which can have
    equally high column-fill."""
    non_null = row.dropna()
    if non_null.empty:
        return False
    cleaned = (
        non_null.astype(str).str.strip()
        .str.replace(r"[\$€£,\s%]", "", regex=True)
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    )
    return pd.to_numeric(cleaned, errors="coerce").notna().mean() >= 0.6


def detect_header_row(raw_df: pd.DataFrame) -> int:
    """Find the header row in a sheet that may carry title rows, blank spacer
    rows, and section labels above the real table (a common FP&A export shape:
    a company/report title, a blank line, then 'Account | GL Code | Jan-25 | …').

    The header row is the first row whose non-null cell count is close to the
    sheet's max column-fill (title rows and section labels are sparse — one or
    two cells) AND whose cells are NOT themselves numeric data (which rules out
    matching a wide data row that happens to be fully populated). Falls back to
    row 0 — today's behaviour — when no row clears the bar, so a normal file
    with a real row-0 header is unaffected."""
    if raw_df.empty:
        return 0
    scan = raw_df.head(_HEADER_SCAN_ROWS)
    fill = scan.notna().sum(axis=1)
    max_fill = int(fill.max())
    if max_fill <= 1:
        return 0
    threshold = max(2, int(max_fill * 0.6))
    for i in range(len(scan)):
        if fill.iloc[i] >= threshold and not _row_looks_numeric(scan.iloc[i]):
            return i
    return 0


def detect_sheet_period(sheet_name: str | None, title_rows: list[str]) -> str | None:
    """Find the as-of period a snapshot sheet states once, outside the table —
    in its name ("AR Jun-25") or a title row ("… as of 30-Jun-2025"). Snapshot
    exports carry no period column; their date columns are transaction dates.
    Returns canonical 'YYYY-MM', or None when nothing states a period."""
    from app.ingestion.mapping import parse_period

    for text in [t for t in title_rows if t] + ([sheet_name] if sheet_name else []):
        for m in _PERIOD_IN_TEXT.finditer(str(text)):
            try:
                return parse_period(m.group(0))
            except Exception:
                continue
    return None


def sheet_title_rows(raw: bytes, filename: str, sheet: str | None, header_row: int) -> list[str]:
    """The non-empty text cells above the header row — where a snapshot export
    puts its report title and as-of date."""
    if not filename.lower().endswith(".xlsx") or header_row <= 0:
        return []
    probe = pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0, header=None,
                          dtype=str, engine="openpyxl", nrows=header_row)
    return [str(v) for v in probe.stack().dropna().tolist()]


def _read_frame(raw: bytes, filename: str, sheet: str | None = None,
                header_row: int | None = None) -> pd.DataFrame:
    name = filename.lower()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=True)
    if name.endswith(".xlsx"):
        if header_row is None:
            # Two-pass: read headerless to find the real header row, then
            # re-read with it applied. Small files (<10MB cap) — the extra
            # parse is cheap and keeps header detection out of every caller.
            probe = pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0, header=None,
                                  dtype=str, engine="openpyxl")
            header_row = detect_header_row(probe)
        return pd.read_excel(io.BytesIO(raw), sheet_name=sheet or 0, header=header_row,
                             dtype=str, engine="openpyxl")
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


def upload_sheet_period(conn: sqlite3.Connection, upload_id: str, sheet: str | None = None) -> str | None:
    """The as-of period stated by the chosen sheet's name/title rows, if any."""
    row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise UploadError(f"Upload {upload_id} not found")
    filename = row["filename"]
    if not filename.lower().endswith(".xlsx"):
        return None
    raw = Path(row["file_path"]).read_bytes()
    chosen = sheet or row["chosen_sheet"]
    probe = pd.read_excel(io.BytesIO(raw), sheet_name=chosen or 0, header=None,
                          dtype=str, engine="openpyxl")
    header_row = detect_header_row(probe)
    return detect_sheet_period(chosen, sheet_title_rows(raw, filename, chosen, header_row))


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
