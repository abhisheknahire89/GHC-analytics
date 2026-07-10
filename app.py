from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"


class AnalyzeRetentionRequest(BaseModel):
    file_path: str


def _get_connection() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(database=":memory:")


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def load_and_clean(file_path: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = Path(file_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    con = _get_connection()
    raw = con.execute("SELECT * FROM read_csv_auto(?, header=true)", [str(path)]).fetch_df()
    raw.columns = [str(col).strip() for col in raw.columns]

    required = ["customer_id", "order_id", "created_at", "total_quantity", "discount_type"]
    missing_columns = [col for col in required if col not in raw.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")

    df = raw[required].copy()
    total_rows = len(df)
    original_discount_missing = df["discount_type"].isna().sum() + df["discount_type"].astype("string").str.strip().eq("").sum()

    df["customer_id"] = df["customer_id"].astype("string").str.strip()
    df["order_id"] = df["order_id"].astype("string").str.strip()
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True).dt.tz_localize(None)
    df["total_quantity"] = pd.to_numeric(df["total_quantity"], errors="coerce")
    df["discount_type"] = df["discount_type"].astype("string").fillna("").str.strip()
    df["discount_type"] = df["discount_type"].replace({"": "No discount", "nan": "No discount", "<NA>": "No discount"})

    quality = {
        "total_rows": total_rows,
        "missing_customer_id": int(df["customer_id"].isna().sum() + df["customer_id"].eq("").sum()),
        "missing_order_id": int(df["order_id"].isna().sum() + df["order_id"].eq("").sum()),
        "missing_timestamps": int(df["created_at"].isna().sum()),
        "missing_total_quantity": int(df["total_quantity"].isna().sum()),
        "missing_discount_type": int(original_discount_missing),
        "duplicate_order_ids": int(df["order_id"].duplicated().sum()),
    }

    clean = df.dropna(subset=["customer_id", "order_id", "created_at"]).copy()
    clean = clean[clean["customer_id"] != ""]
    clean = clean[clean["order_id"] != ""]
    clean["total_quantity"] = clean["total_quantity"].fillna(0)
    clean = clean.sort_values(["customer_id", "created_at", "order_id"]).reset_index(drop=True)

    quality["clean_rows"] = int(len(clean))
    quality["dropped_rows"] = int(total_rows - len(clean))
    return clean, quality


def compute_cohort_retention(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_frame(
            ["cohort_label", "interval_index", "retained_count", "cohort_size", "retention_rate", "granularity"]
        )

    span_days = (df["created_at"].max() - df["created_at"].min()).days
    use_weeks = span_days < 180
    interval_unit = "week" if use_weeks else "month"
    max_interval = 12 if use_weeks else 6

    con = _get_connection()
    con.register("transactions", df)
    return con.execute(
        f"""
        WITH customer_orders AS (
            SELECT
                customer_id,
                created_at::DATE AS order_date,
                MIN(created_at::DATE) OVER (PARTITION BY customer_id) AS cohort_start
            FROM transactions
        ),
        intervals AS (
            SELECT
                customer_id,
                cohort_start,
                date_diff('{interval_unit}', cohort_start, order_date) AS interval_index
            FROM customer_orders
        ),
        filtered AS (
            SELECT * FROM intervals
            WHERE interval_index BETWEEN 0 AND {max_interval}
        ),
        cohort_sizes AS (
            SELECT cohort_start, COUNT(DISTINCT customer_id) AS cohort_size
            FROM filtered
            WHERE interval_index = 0
            GROUP BY 1
        ),
        retention AS (
            SELECT
                cohort_start,
                interval_index,
                COUNT(DISTINCT customer_id) AS retained_count
            FROM filtered
            GROUP BY 1, 2
        )
        SELECT
            CASE
                WHEN '{interval_unit}' = 'week' THEN strftime(cohort_start, '%Y-W%W')
                ELSE strftime(cohort_start, '%Y-%m')
            END AS cohort_label,
            retention.interval_index,
            retention.retained_count,
            cohort_sizes.cohort_size,
            ROUND(retention.retained_count * 1.0 / NULLIF(cohort_sizes.cohort_size, 0), 4) AS retention_rate,
            '{interval_unit}' AS granularity
        FROM retention
        JOIN cohort_sizes USING (cohort_start)
        ORDER BY cohort_start, retention.interval_index
        """
    ).fetch_df()


def compute_repeat_purchase_rates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_frame(["window_days", "rate", "total_customers", "repeat_customers"])

    con = _get_connection()
    con.register("transactions", df)
    first_second = con.execute(
        """
        WITH ranked AS (
            SELECT
                customer_id,
                created_at,
                order_id,
                ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at, order_id) AS order_rank
            FROM transactions
        )
        SELECT
            customer_id,
            MIN(CASE WHEN order_rank = 1 THEN created_at END) AS first_order_at,
            MIN(CASE WHEN order_rank = 2 THEN created_at END) AS second_order_at
        FROM ranked
        GROUP BY 1
        """
    ).fetch_df()

    total_customers = int(len(first_second))
    rows: list[dict[str, Any]] = []
    for window_days in (30, 60, 90):
        repeat_customers = int(
            (
                first_second["second_order_at"].notna()
                & ((first_second["second_order_at"] - first_second["first_order_at"]).dt.days <= window_days)
            ).sum()
        )
        rows.append(
            {
                "window_days": window_days,
                "rate": round(repeat_customers / total_customers, 4) if total_customers else 0.0,
                "total_customers": total_customers,
                "repeat_customers": repeat_customers,
            }
        )
    return pd.DataFrame(rows)


def compute_time_to_second_segments(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_frame(["segment", "customers", "median_days", "p25_days", "p75_days"])

    con = _get_connection()
    con.register("transactions", df)
    first_orders = con.execute(
        """
        WITH ranked AS (
            SELECT
                customer_id,
                order_id,
                created_at,
                total_quantity,
                discount_type,
                ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at, order_id) AS order_rank
            FROM transactions
        )
        SELECT
            customer_id,
            MIN(CASE WHEN order_rank = 1 THEN created_at END) AS first_order_at,
            MIN(CASE WHEN order_rank = 2 THEN created_at END) AS second_order_at,
            MIN(CASE WHEN order_rank = 1 THEN total_quantity END) AS first_total_quantity,
            MIN(CASE WHEN order_rank = 1 THEN discount_type END) AS first_discount_type
        FROM ranked
        GROUP BY 1
        """
    ).fetch_df()

    repeaters = first_orders[first_orders["second_order_at"].notna()].copy()
    if repeaters.empty:
        return _empty_frame(["segment", "customers", "median_days", "p25_days", "p75_days"])

    median_basket = float(first_orders["first_total_quantity"].median())
    repeaters["days_to_second_order"] = (repeaters["second_order_at"] - repeaters["first_order_at"]).dt.days
    repeaters["discount_segment"] = repeaters["first_discount_type"].eq("No discount").map(
        {True: "no_discount_first_order", False: "discount_first_order"}
    )
    repeaters["basket_segment"] = repeaters["first_total_quantity"].ge(median_basket).map(
        {True: "large_basket", False: "small_basket"}
    )
    repeaters["segment"] = repeaters["discount_segment"] + "__" + repeaters["basket_segment"]

    stats = (
        repeaters.groupby("segment")["days_to_second_order"]
        .agg(
            customers="count",
            median_days="median",
            p25_days=lambda s: s.quantile(0.25),
            p75_days=lambda s: s.quantile(0.75),
        )
        .reset_index()
    )
    for col in ("median_days", "p25_days", "p75_days"):
        stats[col] = stats[col].round(2)
    return stats.sort_values("segment").reset_index(drop=True)


def compute_retention_by_discount(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_frame(
            ["discount_type", "customers", "rpr_30", "rpr_60", "rpr_90", "median_days_to_second_order", "avg_orders_90d"]
        )

    con = _get_connection()
    con.register("transactions", df)
    return con.execute(
        """
        WITH ranked AS (
            SELECT
                customer_id,
                order_id,
                created_at,
                discount_type,
                ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY created_at, order_id) AS order_rank
            FROM transactions
        ),
        first_orders AS (
            SELECT customer_id, created_at AS first_order_at, discount_type
            FROM ranked
            WHERE order_rank = 1
        ),
        second_orders AS (
            SELECT customer_id, created_at AS second_order_at
            FROM ranked
            WHERE order_rank = 2
        ),
        orders_90d AS (
            SELECT
                f.customer_id,
                COUNT(t.order_id) AS orders_in_90d
            FROM first_orders f
            JOIN transactions t
              ON t.customer_id = f.customer_id
             AND t.created_at <= f.first_order_at + INTERVAL 90 DAY
            GROUP BY 1
        )
        SELECT
            f.discount_type,
            COUNT(*) AS customers,
            ROUND(AVG(CASE WHEN s.second_order_at IS NOT NULL AND date_diff('day', f.first_order_at, s.second_order_at) <= 30 THEN 1.0 ELSE 0.0 END), 4) AS rpr_30,
            ROUND(AVG(CASE WHEN s.second_order_at IS NOT NULL AND date_diff('day', f.first_order_at, s.second_order_at) <= 60 THEN 1.0 ELSE 0.0 END), 4) AS rpr_60,
            ROUND(AVG(CASE WHEN s.second_order_at IS NOT NULL AND date_diff('day', f.first_order_at, s.second_order_at) <= 90 THEN 1.0 ELSE 0.0 END), 4) AS rpr_90,
            ROUND(MEDIAN(CASE WHEN s.second_order_at IS NOT NULL THEN date_diff('day', f.first_order_at, s.second_order_at) END), 2) AS median_days_to_second_order,
            ROUND(AVG(o.orders_in_90d), 2) AS avg_orders_90d
        FROM first_orders f
        LEFT JOIN second_orders s USING (customer_id)
        LEFT JOIN orders_90d o USING (customer_id)
        GROUP BY 1
        ORDER BY customers DESC, discount_type
        """
    ).fetch_df()


def _json_ready(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    ready = df.copy()
    for col in ready.columns:
        if pd.api.types.is_datetime64_any_dtype(ready[col]):
            ready[col] = ready[col].astype("string")
    return ready.where(pd.notnull(ready), None).to_dict(orient="records")


def _write_outputs(
    cohort_retention: pd.DataFrame,
    repeat_purchase_rates: pd.DataFrame,
    time_to_second_segments: pd.DataFrame,
    retention_by_discount: pd.DataFrame,
) -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "cohort_retention": OUTPUT_DIR / "cohort_retention.csv",
        "repeat_purchase_rates": OUTPUT_DIR / "repeat_purchase_rates.csv",
        "time_to_second_segments": OUTPUT_DIR / "time_to_second_segments.csv",
        "retention_by_discount": OUTPUT_DIR / "retention_by_discount.csv",
    }
    cohort_retention.to_csv(outputs["cohort_retention"], index=False)
    repeat_purchase_rates.to_csv(outputs["repeat_purchase_rates"], index=False)
    time_to_second_segments.to_csv(outputs["time_to_second_segments"], index=False)
    retention_by_discount.to_csv(outputs["retention_by_discount"], index=False)
    return {key: str(path.relative_to(BASE_DIR)) for key, path in outputs.items()}


def _run_analysis(file_path: str) -> dict[str, Any]:
    df, data_quality = load_and_clean(file_path)
    cohort_retention = compute_cohort_retention(df)
    repeat_purchase_rates = compute_repeat_purchase_rates(df)
    time_to_second_segments = compute_time_to_second_segments(df)
    retention_by_discount = compute_retention_by_discount(df)
    output_files = _write_outputs(
        cohort_retention,
        repeat_purchase_rates,
        time_to_second_segments,
        retention_by_discount,
    )
    return {
        "data_quality": data_quality,
        "cohort_retention": _json_ready(cohort_retention),
        "repeat_purchase_rates": _json_ready(repeat_purchase_rates),
        "time_to_second_segments": _json_ready(time_to_second_segments),
        "retention_by_discount": _json_ready(retention_by_discount),
        "output_files": output_files,
    }


app = FastAPI(title="GHC Retention Analytics")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/upload-csv")
async def upload_csv(file: UploadFile = File(...)) -> dict[str, str]:
    suffix = Path(file.filename or "transactions.csv").suffix or ".csv"
    filename = f"{uuid4().hex}{suffix}"
    file_path = UPLOAD_DIR / filename
    content = await file.read()
    file_path.write_bytes(content)
    return {"file_path": str(file_path.relative_to(BASE_DIR))}


@app.post("/analyze-retention")
def analyze_retention(request: AnalyzeRetentionRequest) -> dict[str, Any]:
    try:
        return _run_analysis(request.file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc
