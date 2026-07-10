from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import duckdb
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pdf_report import build_pdf
from storage import get_analysis, get_analysis_by_hash, list_analyses, save_analysis


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"


class AnalyzeRetentionRequest(BaseModel):
    file_path: str
    file_hash: str | None = None
    source_filename: str | None = None


def _get_connection() -> duckdb.DuckDBPyConnection:
    # Per-function in-memory connections keep the analytics isolated; reuse for very large files is future work.
    return duckdb.connect(database=":memory:")


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def _first_present(row: dict[str, Any], candidates: list[str]) -> Any:
    for key in candidates:
        if key in row:
            value = row.get(key)
            if value is not None and str(value).strip() != "":
                return value
    return None


def _parse_quantity(row: dict[str, Any]) -> float | None:
    direct = _first_present(row, ["total_quantity", "quantity", "total_quantity_ordered"])
    if direct is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            return None

    quantity_keys = [key for key in row if re.fullmatch(r"line_items\[\d+\]\.quantity", key)]
    total = 0.0
    found = False
    for key in quantity_keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            total += float(value)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def _parse_discount_type(row: dict[str, Any]) -> str:
    direct = _first_present(row, ["discount_type", "discount_code", "discount_name"])
    if direct is not None:
        return str(direct).strip()

    discount_keys = [key for key in row if re.fullmatch(r"discount_applications\[\d+\]\.title", key)]
    values = []
    for key in discount_keys:
        value = row.get(key)
        if value is None:
            continue
        cleaned = str(value).strip()
        if cleaned:
            values.append(cleaned)
    unique_values = list(dict.fromkeys(values))
    return " | ".join(unique_values)


def _load_rows(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    raw_row_count = 0
    malformed_rows = 0

    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV header is missing or unreadable.")

        for row in reader:
            raw_row_count += 1
            if None in row:
                malformed_rows += 1
            rows.append(row)

    return rows, raw_row_count, malformed_rows


def _resolve_upload_path(file_path: str) -> Path:
    requested = Path(file_path)
    candidate = (UPLOAD_DIR / requested).resolve() if not requested.is_absolute() else requested.resolve()
    try:
        candidate.relative_to(UPLOAD_DIR.resolve())
    except ValueError as exc:
        raise ValueError("file_path must point to a file inside the uploads directory.") from exc
    return candidate


def load_and_clean(file_path: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = _resolve_upload_path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    raw_rows, raw_row_count, malformed_rows = _load_rows(path)
    if not raw_rows:
        raise ValueError("CSV contains no data rows.")

    normalized_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        normalized_rows.append(
            {
                "customer_id": _first_present(row, ["customer_id", "customer.id", "customer", "customerid"]),
                "order_id": _first_present(row, ["order_id", "order_number", "order.name", "name"]),
                "created_at": _first_present(row, ["created_at", "created_at_order", "order_date", "processed_at"]),
                "total_quantity": _parse_quantity(row),
                "discount_type": _parse_discount_type(row),
            }
        )

    df = pd.DataFrame(normalized_rows, columns=["customer_id", "order_id", "created_at", "total_quantity", "discount_type"])
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
        "defaulted_total_quantity": int(df["total_quantity"].isna().sum()),
        "total_quantity_note": "Missing total_quantity values were defaulted to 0.",
        "missing_discount_type": int(original_discount_missing),
        "duplicate_order_ids": int(df["order_id"].duplicated().sum()),
        "malformed_rows": int(malformed_rows),
    }

    clean = df.dropna(subset=["customer_id", "order_id", "created_at"]).copy()
    clean = clean[clean["customer_id"] != ""]
    clean = clean[clean["order_id"] != ""]
    clean = clean.drop_duplicates(subset=["order_id"], keep="first")
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
        return _empty_frame(["segment_group", "segment", "customers", "median_days", "p25_days", "p75_days"])

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
        return _empty_frame(["segment_group", "segment", "customers", "median_days", "p25_days", "p75_days"])

    median_basket = float(first_orders["first_total_quantity"].median())
    repeaters["days_to_second_order"] = (repeaters["second_order_at"] - repeaters["first_order_at"]).dt.days
    repeaters["discount_segment"] = repeaters["first_discount_type"].eq("No discount").map(
        {True: "no_discount_first_order", False: "discount_first_order"}
    )
    repeaters["basket_segment"] = repeaters["first_total_quantity"].ge(median_basket).map(
        {True: "large_basket", False: "small_basket"}
    )

    frames = []
    for segment_group, column in [("discount_usage", "discount_segment"), ("basket_size", "basket_segment")]:
        stats = (
            repeaters.groupby(column)["days_to_second_order"]
            .agg(
                customers="count",
                median_days="median",
                p25_days=lambda s: s.quantile(0.25),
                p75_days=lambda s: s.quantile(0.75),
            )
            .reset_index()
            .rename(columns={column: "segment"})
        )
        stats.insert(0, "segment_group", segment_group)
        frames.append(stats)

    result = pd.concat(frames, ignore_index=True)
    for col in ("median_days", "p25_days", "p75_days"):
        result[col] = result[col].round(2)
    return result.sort_values(["segment_group", "segment"]).reset_index(drop=True)


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


def build_ui_explanations(
    cohort_retention: pd.DataFrame,
    repeat_purchase_rates: pd.DataFrame,
    time_to_second_segments: pd.DataFrame,
    retention_by_discount: pd.DataFrame,
) -> dict[str, Any]:
    cohort_summary = [
        "No cohort retention data is available yet.",
        "Upload more repeat-order history to compare cohort durability.",
    ]
    repeat_summary = [
        "Repeat purchase windows are not available.",
        "Additional repeat behavior is needed for early renewal analysis.",
    ]
    segment_summary = [
        "No time-to-second-order segment data is available.",
        "Second-order timing will appear once members place repeat orders.",
    ]
    discount_summary = [
        "No discount retention data is available.",
        "Discount-level refill behavior will appear after valid uploads.",
    ]
    primary_insight = "Upload data to generate the primary retention insight."
    metric_tags = ["needs_data"]

    if not cohort_retention.empty:
        cohort_rates = cohort_retention[cohort_retention["interval_index"] > 0]
        if not cohort_rates.empty:
            best = cohort_rates.sort_values("retention_rate", ascending=False).iloc[0]
            worst = cohort_rates.sort_values("retention_rate", ascending=True).iloc[0]
            cohort_summary = [
                f"Cohort {best['cohort_label']} shows the strongest follow-on retention at interval {int(best['interval_index'])} with rate {best['retention_rate']:.2f}.",
                f"Cohort {worst['cohort_label']} has the weakest follow-on retention at interval {int(worst['interval_index'])} with rate {worst['retention_rate']:.2f}.",
            ]

    if not repeat_purchase_rates.empty:
        rpr = repeat_purchase_rates.sort_values("window_days")
        first = rpr.iloc[0]
        last = rpr.iloc[-1]
        lift = float(last["rate"] - first["rate"])
        repeat_summary = [
            f"Within {int(first['window_days'])} days, {int(first['repeat_customers'])} of {int(first['total_customers'])} members reordered for a rate of {first['rate']:.2f}.",
            f"By {int(last['window_days'])} days, repeat rate reaches {last['rate']:.2f}, a {lift:.2f} lift versus the earliest window.",
        ]

    if not time_to_second_segments.empty:
        fastest = time_to_second_segments.sort_values("median_days").iloc[0]
        slowest = time_to_second_segments.sort_values("median_days", ascending=False).iloc[0]
        segment_summary = [
            f"Segment {fastest['segment']} returns fastest with median {fastest['median_days']:.1f} days to second order.",
            f"Segment {slowest['segment']} returns slowest with median {slowest['median_days']:.1f} days, making it the clearest follow-up target.",
        ]

    if not retention_by_discount.empty:
        best_discount = retention_by_discount.sort_values("rpr_90", ascending=False).iloc[0]
        worst_discount = retention_by_discount.sort_values("rpr_30", ascending=True).iloc[0]
        discount_summary = [
            f"Discount type {best_discount['discount_type']} shows the strongest 90-day retention with repeat rate {best_discount['rpr_90']:.2f}.",
            f"Discount type {worst_discount['discount_type']} has the weakest 30-day repeat rate at {worst_discount['rpr_30']:.2f}.",
        ]
        primary_insight = (
            f"{best_discount['discount_type']} is the strongest near-term retention lever based on repeat behavior in this file."
        )

    tags: list[str] = []
    if not repeat_purchase_rates.empty and float(repeat_purchase_rates.loc[repeat_purchase_rates["window_days"] == 30, "rate"].iloc[0]) < 0.35:
        tags.append("early_churn")
    if not retention_by_discount.empty and retention_by_discount["discount_type"].astype(str).str.contains("WELCOME", case=False).any():
        tags.append("welcome_discount_signal")
    if not time_to_second_segments.empty:
        tags.append("second_order_timing")
    if not cohort_retention.empty:
        granularity = str(cohort_retention.iloc[0]["granularity"])
        tags.append(f"{granularity}_cohorts")
    if not retention_by_discount.empty and float(retention_by_discount["rpr_90"].max()) >= 0.6:
        tags.append("high_potential_segment")
    metric_tags = tags[:6] or metric_tags

    return {
        "cohort_summary": " ".join(cohort_summary),
        "repeat_purchase_summary": " ".join(repeat_summary),
        "segment_summary": " ".join(segment_summary),
        "discount_summary": " ".join(discount_summary),
        "primary_insight": primary_insight,
        "metric_tags": metric_tags,
    }


def build_analytics_intelligence(
    repeat_purchase_rates: pd.DataFrame,
    time_to_second_segments: pd.DataFrame,
    retention_by_discount: pd.DataFrame,
) -> dict[str, Any]:
    insights: list[dict[str, str]] = []
    followup_queries: list[str] = []
    segment_to_watch = {
        "segment_name": "insufficient_data",
        "segment_group": "insufficient_data",
        "reason": "Upload more repeat-order history to identify the strongest intervention segment.",
        "suggested_experiment": "Test reminder timing after first order once repeat data is available.",
    }

    if not repeat_purchase_rates.empty:
        rpr30 = repeat_purchase_rates.loc[repeat_purchase_rates["window_days"] == 30].iloc[0]
        rpr90 = repeat_purchase_rates.loc[repeat_purchase_rates["window_days"] == 90].iloc[0]
        insights.append(
            {
                "title": "Early renewal leakage",
                "type": "risk",
                "metric_reference": f"RPR 30d {rpr30['rate']:.2f} versus RPR 90d {rpr90['rate']:.2f}.",
                "impact_area": "refill_retention",
                "suggested_action": "Trigger refill reminders and care nudges before day 30 for first-order members.",
            }
        )
        followup_queries.append("Which product or care pathway has the lowest 30-day repeat purchase rate?")

    if not time_to_second_segments.empty:
        fastest = time_to_second_segments.sort_values("median_days").iloc[0]
        slowest_by_group = time_to_second_segments.loc[
            time_to_second_segments.groupby("segment_group")["median_days"].idxmax()
        ].sort_values("median_days", ascending=False)
        slowest = slowest_by_group.iloc[0]
        insights.append(
            {
                "title": "Timing gap across segments",
                "type": "pattern",
                "metric_reference": f"{fastest['segment']} median {fastest['median_days']:.1f}d; slowest by {slowest['segment_group']} is {slowest['segment']} at {slowest['median_days']:.1f}d.",
                "impact_area": "diagnostics_followup",
                "suggested_action": "Use segment-specific reminder timing instead of one fixed outreach schedule.",
            }
        )
        segment_to_watch = {
            "segment_name": str(slowest["segment"]),
            "segment_group": str(slowest["segment_group"]),
            "reason": f"Slowest by {slowest['segment_group']}: this segment has a median return of {slowest['median_days']:.1f} days, suggesting higher churn risk.",
            "suggested_experiment": "A/B test an earlier reminder plus a small incentive 7 days before its typical reorder window.",
        }
        followup_queries.append("Do slower second-order segments differ by clinic, geography, or acquisition channel?")

    if not retention_by_discount.empty:
        best = retention_by_discount.sort_values("rpr_90", ascending=False).iloc[0]
        worst = retention_by_discount.sort_values("rpr_30").iloc[0]
        insights.append(
            {
                "title": f"{best['discount_type']} drives loyalty",
                "type": "opportunity",
                "metric_reference": f"Top 90d repeat rate is {best['rpr_90']:.2f}.",
                "impact_area": "plan_renewals",
                "suggested_action": f"Expand {best['discount_type']} to similar members with matched first-order baskets.",
            }
        )
        insights.append(
            {
                "title": f"{worst['discount_type']} needs review",
                "type": "hypothesis",
                "metric_reference": f"Lowest 30d repeat rate is {worst['rpr_30']:.2f}.",
                "impact_area": "member_activation",
                "suggested_action": "Review offer design, eligibility, and follow-up messaging for this discount path.",
            }
        )
        followup_queries.append("How does discount performance change by basket size or first-order quantity band?")
        followup_queries.append("Which discount types lead to higher 90-day order frequency without margin erosion?")

    return {
        "insights": insights[:6],
        "followup_queries": followup_queries[:5],
        "segment_to_watch": segment_to_watch,
    }


def build_plain_language_report(
    repeat_purchase_rates: pd.DataFrame,
    time_to_second_segments: pd.DataFrame,
    retention_by_discount: pd.DataFrame,
    analytics_intelligence: dict[str, Any],
) -> dict[str, Any]:
    happening_lines = [
        "We do not have enough data yet to explain what is happening.",
    ]
    next_steps = [
        "Upload a larger file so we can see who comes back and when.",
    ]
    target_line = "Target returning customers whose first order used No discount for a reminder message after 30 days."

    if not repeat_purchase_rates.empty:
        rpr30 = repeat_purchase_rates.loc[repeat_purchase_rates["window_days"] == 30].iloc[0]
        rpr60 = repeat_purchase_rates.loc[repeat_purchase_rates["window_days"] == 60].iloc[0]
        rpr90 = repeat_purchase_rates.loc[repeat_purchase_rates["window_days"] == 90].iloc[0]
        happening_lines = [
            (
                f"Most people are coming back slowly. {int(rpr30['repeat_customers'])} of "
                f"{int(rpr30['total_customers'])} customers buy again within 30 days."
            ),
            f"By 60 days, {int(rpr60['repeat_customers'])} customers have bought again, and by 90 days that grows to {int(rpr90['repeat_customers'])}.",
        ]

        next_steps = [
            "Send a refill or reorder reminder before day 30, because many people are not coming back quickly.",
            "Use a second reminder between day 45 and day 60 for people who still have not returned.",
        ]

    if not time_to_second_segments.empty:
        fastest = time_to_second_segments.sort_values("median_days").iloc[0]
        slowest = time_to_second_segments.sort_values("median_days", ascending=False).iloc[0]
        happening_lines.append(
            f"People in {fastest['segment']} come back faster, while {slowest['segment']} takes the longest to return."
        )
        next_steps.append(
            f"Give extra follow-up to {slowest['segment']}, because this group is the slowest to come back."
        )

    if not retention_by_discount.empty:
        best = retention_by_discount.sort_values("rpr_90", ascending=False).iloc[0]
        worst = retention_by_discount.sort_values("rpr_30").iloc[0]
        happening_lines.append(
            f"The offer {best['discount_type']} seems to keep people coming back best, while {worst['discount_type']} looks weakest early on."
        )
        next_steps.append(
            f"Repeat {best['discount_type']} with similar customers and review {worst['discount_type']} to see why people are not returning."
        )

        watch = analytics_intelligence.get("segment_to_watch", {})
        segment_name = watch.get("segment_name", "returning customers")
        segment_group = watch.get("segment_group", "")
        reminder_days = "35"
        if not time_to_second_segments.empty:
            slowest = time_to_second_segments.loc[
                time_to_second_segments.groupby("segment_group")["median_days"].idxmax()
            ].sort_values("median_days", ascending=False).iloc[0]
            reminder_days = str(max(int(float(slowest["median_days"])) - 7, 7))
        target_line = (
            f"Target {segment_name} customers (slowest by {segment_group}) whose first order used "
            f"{best['discount_type']} for a refill reminder after {reminder_days} days."
        )

    return {
        "what_is_happening": " ".join(happening_lines[:4]),
        "what_should_we_do_next": next_steps[:5],
        "target_line": target_line,
    }


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
    ui_explanations = build_ui_explanations(
        cohort_retention,
        repeat_purchase_rates,
        time_to_second_segments,
        retention_by_discount,
    )
    analytics_intelligence = build_analytics_intelligence(
        repeat_purchase_rates,
        time_to_second_segments,
        retention_by_discount,
    )
    plain_language_report = build_plain_language_report(
        repeat_purchase_rates,
        time_to_second_segments,
        retention_by_discount,
        analytics_intelligence,
    )
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
        "ui_explanations": ui_explanations,
        "analytics_intelligence": analytics_intelligence,
        "plain_language_report": plain_language_report,
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
    return {
        "file_path": str(file_path.relative_to(UPLOAD_DIR)),
        "file_hash": hashlib.sha256(content).hexdigest(),
        "source_filename": Path(file.filename or "transactions.csv").name,
    }


@app.post("/analyze-retention")
def analyze_retention(request: AnalyzeRetentionRequest) -> dict[str, Any]:
    try:
        path = _resolve_upload_path(request.file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        cached = get_analysis_by_hash(file_hash)
        if cached:
            return {**cached["result"], "analysis_id": cached["id"], "cached": True}
        result = _run_analysis(str(path))
        record = save_analysis(
            uuid4().hex,
            request.source_filename or path.name,
            file_hash,
            result,
        )
        return {**result, "analysis_id": record["id"], "cached": False}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@app.get("/analyses")
def get_past_analyses() -> list[dict[str, Any]]:
    return list_analyses()


@app.get("/analyses/{analysis_id}/export-pdf")
def export_pdf(analysis_id: str) -> Response:
    record = get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return Response(
        content=build_pdf(record["result"], record["source_filename"]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="retention_report_{analysis_id}.pdf"'},
    )


@app.get("/analyses/{analysis_id}")
def get_analysis_full(analysis_id: str) -> dict[str, Any]:
    record = get_analysis(analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return {**record["result"], "analysis_id": record["id"], "cached": True}
