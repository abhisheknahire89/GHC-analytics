from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


TEAL = colors.HexColor("#005f73")
GREEN = colors.HexColor("#e7f5ed")
TEXT = colors.HexColor("#1d2939")


def _text(value: Any) -> str:
    return str(value if value is not None else "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(value: Any) -> str:
    return "-" if value is None else f"{float(value) * 100:.1f}%"


def _table(rows: list[list[Any]], widths: list[float] | None = None) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _cohort_table(rows: list[dict[str, Any]]) -> Table | None:
    if not rows:
        return None
    intervals = sorted({int(row["interval_index"]) for row in rows})
    labels = list(dict.fromkeys(row["cohort_label"] for row in rows))
    cells = {(row["cohort_label"], int(row["interval_index"])): row for row in rows}
    data = [["Cohort", *[f"M/W {interval}" for interval in intervals]]]
    for label in labels:
        data.append([label, *[
            (f"{cell['retained_count']} ({_pct(cell['retention_rate'])})" if (cell := cells.get((label, interval))) else "-")
            for interval in intervals
        ]])
    return _table(data)


def build_pdf(analysis_result: dict[str, Any], source_filename: str) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("ReportTitle", parent=styles["Title"], textColor=TEXT, fontSize=22, leading=26)
    heading = ParagraphStyle("ReportHeading", parent=styles["Heading2"], textColor=TEAL, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("ReportBody", parent=styles["BodyText"], fontSize=9, leading=13, textColor=TEXT)
    small = ParagraphStyle("ReportSmall", parent=body, fontSize=8, leading=10)
    report = analysis_result["plain_language_report"]
    divider = Table([[""],], colWidths=[182 * mm], rowHeights=[1])
    divider.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TEAL)]))
    story = [
        Paragraph("GHC Retention Analytics Report", title),
        Paragraph(f"Source: {_text(source_filename)}", small),
        Paragraph("Generated locally - no data leaves this environment", small),
        Spacer(1, 4),
        divider,
        Paragraph("Executive summary", heading),
        Paragraph(_text(report.get("what_is_happening", "")), body),
    ]
    for action in report.get("what_should_we_do_next", []):
        story.append(Paragraph(f"&bull; {_text(action)}", body))
    callout = Table([[Paragraph(_text(report.get("target_line", "")), body)]], colWidths=[182 * mm])
    callout.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GREEN),
        ("LINEBEFORE", (0, 0), (0, -1), 4, colors.HexColor("#2f855a")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#a7d7bb")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.extend([Paragraph("Recommended retention lever", heading), callout])
    quality = analysis_result.get("data_quality", {})
    quality_rows = [["Metric", "Value"], *[[key.replace("_", " ").title(), _text(value)] for key, value in quality.items()]]
    story.extend([Paragraph("Data quality", heading), _table(quality_rows, [92 * mm, 90 * mm])])
    cohort = _cohort_table(analysis_result.get("cohort_retention", []))
    if cohort:
        story.extend([Paragraph("Cohort retention", heading), cohort])
    rpr_rows = [["Window", "Repeat rate", "Repeat customers", "Total customers"]] + [[f"{row['window_days']} days", _pct(row["rate"]), _text(row["repeat_customers"]), _text(row["total_customers"])] for row in analysis_result.get("repeat_purchase_rates", [])]
    story.extend([Paragraph("Repeat Purchase Rate", heading), _table(rpr_rows)])
    segment_rows = [["Comparison", "Segment", "Customers", "Median", "P25", "P75"]] + [[_text(row["segment_group"]), _text(row["segment"]), _text(row["customers"]), f"{_text(row['median_days'])} days", f"{_text(row['p25_days'])} days", f"{_text(row['p75_days'])} days"] for row in analysis_result.get("time_to_second_segments", [])]
    story.extend([Paragraph("Time to second order", heading), _table(segment_rows)])
    discount_rows = [["Discount", "Customers", "30d", "60d", "90d", "Median days", "Avg orders / 90d"]] + [[_text(row["discount_type"]), _text(row["customers"]), _pct(row["rpr_30"]), _pct(row["rpr_60"]), _pct(row["rpr_90"]), _text(row["median_days_to_second_order"]), _text(row["avg_orders_90d"])] for row in analysis_result.get("retention_by_discount", [])]
    story.extend([Paragraph("Retention by first-order discount", heading), _table(discount_rows)])
    story.append(Paragraph("Key insights", heading))
    for insight in analysis_result.get("analytics_intelligence", {}).get("insights", []):
        card = Table([[Paragraph(f"<b>{_text(insight['title'])}</b> ({_text(insight['type'])})<br/>{_text(insight['metric_reference'])}<br/>{_text(insight['suggested_action'])}", body)]], colWidths=[182 * mm])
        card.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")), ("BACKGROUND", (0, 0), (-1, -1), colors.white), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        story.extend([KeepTogether(card), Spacer(1, 5)])

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(14 * mm, 8 * mm, "Generated locally, no data leaves this environment")
        canvas.drawRightString(196 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
