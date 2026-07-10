from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "analyses.db"


def _connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY,
            source_filename TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            result_json TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_analyses_file_hash ON analyses(file_hash)")
    return con


def save_analysis(analysis_id: str, source_filename: str, file_hash: str, result: dict[str, Any]) -> dict[str, Any]:
    record = {
        "id": analysis_id,
        "source_filename": source_filename,
        "file_hash": file_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "result": result,
    }
    with _connection() as con:
        con.execute(
            "INSERT INTO analyses (id, source_filename, file_hash, created_at, result_json) VALUES (?, ?, ?, ?, ?)",
            (record["id"], record["source_filename"], record["file_hash"], record["created_at"], json.dumps(result)),
        )
    return record


def get_analysis(analysis_id: str) -> dict[str, Any] | None:
    with _connection() as con:
        row = con.execute("SELECT * FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    return _record(row) if row else None


def get_analysis_by_hash(file_hash: str) -> dict[str, Any] | None:
    with _connection() as con:
        row = con.execute(
            "SELECT * FROM analyses WHERE file_hash = ? ORDER BY created_at DESC LIMIT 1", (file_hash,)
        ).fetchone()
    return _record(row) if row else None


def list_analyses() -> list[dict[str, Any]]:
    with _connection() as con:
        rows = con.execute("SELECT * FROM analyses ORDER BY created_at DESC").fetchall()
    items = []
    for row in rows:
        record = _record(row)
        rpr30 = next((item for item in record["result"].get("repeat_purchase_rates", []) if item["window_days"] == 30), {})
        items.append(
            {
                "id": record["id"],
                "source_filename": record["source_filename"],
                "created_at": record["created_at"],
                "rpr_30": rpr30.get("rate"),
                "total_customers": rpr30.get("total_customers"),
            }
        )
    return items


def _record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source_filename": row["source_filename"],
        "file_hash": row["file_hash"],
        "created_at": row["created_at"],
        "result": json.loads(row["result_json"]),
    }
