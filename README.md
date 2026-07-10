# GHC Retention Analytics Studio

Small, local-first analytics software for turning transaction CSV files into a retention dashboard. Upload a file, review repeat-purchase behaviour, and export the underlying tables for downstream reporting.

## What it shows

- Cohort retention by first purchase month (months 0-6), or by week (weeks 0-12) when the data covers less than six months.
- Repeat Purchase Rate: customers with a second order within 30, 60, and 90 days.
- Time to second order, compared by first-order discount usage and first basket size.
- Retention by first-order discount: 30-day repeat rate, time to second order, and average orders in 90 days.
- Plain-language findings, practical next actions, and a recommended retention target.
- CSV downloads for every major analysis table.

## Stack

- Backend: Python, FastAPI, Pandas, DuckDB
- Frontend: React, Vite, Material UI
- Storage: local `uploads/` and generated `output/` files

The current insight layer is rules-based, not LLM-powered. It keeps calculations reproducible and does not transmit customer data. An optional future LLM layer can consume only the aggregated response JSON.

## Run locally

Requirements: Python 3.11+ and Node.js 18+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). The API health endpoint is [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

## CSV input

The service accepts the standard normalized columns:

```text
customer_id, order_id, created_at, total_quantity, discount_type
```

It also maps common Shopify export fields, including `customer.id`, `order_number`, `created_at`, `line_items[*].quantity`, and `discount_applications[*].title`. Rows with malformed field counts are tolerated where possible and reported in the data-quality summary.

`created_at` must be a timestamp that Pandas can parse. `customer_id` and `order_id` identify customers and orders. Blank discounts are treated as `No discount`.

## API

`POST /upload-csv` accepts a multipart CSV file and returns a local `file_path`.

`POST /analyze-retention` accepts:

```json
{ "file_path": "uploads/transactions.csv" }
```

It returns JSON containing:

```text
data_quality
cohort_retention
repeat_purchase_rates
time_to_second_segments
retention_by_discount
ui_explanations
analytics_intelligence
plain_language_report
output_files
analysis_id
cached
```

`GET /analyses` lists saved runs with filename, timestamp, and 30-day repeat rate.

`GET /analyses/{id}` returns the complete stored result. `GET /analyses/{id}/export-pdf` streams a presentation-ready PDF of that result.

Generated files are written locally to `output/`:

```text
cohort_retention.csv
repeat_purchase_rates.csv
time_to_second_segments.csv
retention_by_discount.csv
```

## Privacy note

Keep uploaded transaction data free of unnecessary personal or protected health information. The app runs locally and the baseline analytics do not call an external model or service. `analyses.db` contains stored analysis results and must remain out of version control.
