from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app
import storage


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    uploads = tmp_path / "uploads"
    output = tmp_path / "output"
    uploads.mkdir()
    output.mkdir()
    monkeypatch.setattr(app, "BASE_DIR", tmp_path)
    monkeypatch.setattr(app, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(app, "OUTPUT_DIR", output)
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "analyses.db")
    return TestClient(app.app)


def _csv(rows: list[str]) -> bytes:
    return ("customer_id,order_id,created_at,total_quantity,discount_type\n" + "\n".join(rows)).encode()


def _upload_and_analyze(client: TestClient, content: bytes, filename: str = "transactions.csv") -> dict:
    uploaded = client.post("/upload-csv", files={"file": (filename, io.BytesIO(content), "text/csv")})
    assert uploaded.status_code == 200
    analyzed = client.post("/analyze-retention", json=uploaded.json())
    assert analyzed.status_code == 200, analyzed.text
    return analyzed.json()


def test_duplicate_orders_do_not_change_retention_metrics(client: TestClient) -> None:
    source = _csv([
        "c1,o1,2025-01-01T00:00:00,2,WELCOME10",
        "c1,o2,2025-01-11T00:00:00,2,",
        "c2,o3,2025-01-01T00:00:00,1,",
    ])
    duplicated = _csv([
        "c1,o1,2025-01-01T00:00:00,2,WELCOME10",
        "c1,o2,2025-01-11T00:00:00,2,",
        "c1,o2,2025-01-11T00:00:00,2,",
        "c2,o3,2025-01-01T00:00:00,1,",
    ])
    expected = _upload_and_analyze(client, source)
    actual = _upload_and_analyze(client, duplicated)
    assert actual["data_quality"]["duplicate_order_ids"] == 1
    assert actual["repeat_purchase_rates"] == expected["repeat_purchase_rates"]
    assert actual["retention_by_discount"] == expected["retention_by_discount"]


@pytest.mark.parametrize("file_path", ["/etc/passwd", "../../etc/passwd"])
def test_analysis_rejects_paths_outside_uploads(client: TestClient, file_path: str) -> None:
    response = client.post("/analyze-retention", json={"file_path": file_path})
    assert response.status_code == 400
    assert "inside the uploads" in response.json()["detail"]


def test_shopify_bracket_columns_are_mapped(client: TestClient) -> None:
    content = (
        "customer.id,order_number,created_at,line_items[0].quantity,discount_applications[0].title\n"
        "123,#1001,2025-01-01T00:00:00,3,WELCOME\n"
        "123,#1002,2025-01-14T00:00:00,1,\n"
    ).encode()
    result = _upload_and_analyze(client, content, "shopify.csv")
    assert result["data_quality"]["clean_rows"] == 2
    assert result["repeat_purchase_rates"][0]["rate"] == 1.0
    assert result["retention_by_discount"][0]["discount_type"] == "WELCOME"


def test_upload_analysis_cache_history_and_pdf(client: TestClient) -> None:
    content = _csv([
        "c1,o1,2025-01-01T00:00:00,1,WELCOME10",
        "c1,o2,2025-01-15T00:00:00,1,",
        "c2,o3,2025-01-02T00:00:00,1,",
    ])
    first = _upload_and_analyze(client, content)
    second = _upload_and_analyze(client, content)
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["analysis_id"] == first["analysis_id"]
    assert "mixed products" not in first["plain_language_report"]["target_line"]
    assert first["analytics_intelligence"]["segment_to_watch"]["segment_group"] in {"basket_size", "discount_usage"}

    history = client.get("/analyses")
    assert history.status_code == 200
    assert history.json()[0]["id"] == first["analysis_id"]
    restored = client.get(f"/analyses/{first['analysis_id']}")
    assert restored.status_code == 200
    assert restored.json()["cohort_retention"] == first["cohort_retention"]
    pdf = client.get(f"/analyses/{first['analysis_id']}/export-pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF")


def test_sample_csv_end_to_end(client: TestClient) -> None:
    content = (Path(__file__).resolve().parents[1] / "samples" / "transactions_sample.csv").read_bytes()
    result = _upload_and_analyze(client, content, "transactions_sample.csv")
    rates = {row["window_days"]: row["rate"] for row in result["repeat_purchase_rates"]}
    assert rates == {30: 0.5, 60: 0.6, 90: 0.7}
    assert result["analysis_id"]
