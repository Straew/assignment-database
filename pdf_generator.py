"""
PDF report generation (ReportLab).

Pure functions: they take plain dict data (a survey document / a summary row)
and return PDF bytes, so they can be unit-tested without any database.
"""
import io
import os

import config as cfg


def build_pdf(title, blocks):
    """
    Render a simple report and return the PDF as bytes.

    :param title: document title
    :param blocks: list of ``(heading, header_row_or_None, rows)`` - a table per block;
                   ``header_row=None`` renders a plain two-column key/value list.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    cell = styles["BodyText"]
    story = [Paragraph(title, styles["Title"])]
    for heading, header, rows in blocks:
        story.append(Paragraph(heading, styles["Heading2"]))
        if not rows:
            story.append(Paragraph("None recorded.", cell))
            continue
        data = [[Paragraph(f"<b>{h}</b>", cell) for h in header]] if header else []
        data += [[Paragraph(str(c), cell) for c in row] for row in rows]
        table = Table(data, hAlign="LEFT")
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        if header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d6ecf3")))
        table.setStyle(TableStyle(style))
        story += [table, Spacer(1, 10)]
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, title=title).build(story)
    return buffer.getvalue()


def survey_pdf(survey):
    """PDF report for one survey document (as stored in Cosmos DB)."""
    r = survey["researcher"]
    return build_pdf(f"Survey Report - {survey['survey_date']}", [
        ("Details", None, [("Survey ID", survey["_id"]), ("Site", survey["site"]),
                           ("Date", survey["survey_date"]),
                           ("Cost", f"${survey['survey_cost']:,.2f}"),
                           ("Notes", survey.get("notes") or "-")]),
        ("Researcher", None, [("Name", r["name"]), ("Email", r.get("email", "")),
                              ("Institution", r.get("institution", ""))]),
        ("Species observed", ["Species", "Count"],
         [(s["name"], s["count"]) for s in survey["species_observed"]]),
        ("Equipment used", ["Item", "Category", "Qty"],
         [(e["name"], e["category"], e["quantity"]) for e in survey["equipment_used"]]),
    ])


def summary_pdf(summary):
    """PDF report for one daily-summary row (as stored in Azure SQL)."""
    top = (f"{summary['top_species']} ({summary['top_species_sightings']} surveys)"
           if summary["top_species"] else "n/a")
    alerts = [a for a in (summary["low_equipment_alerts"] or "").split("; ") if a]
    return build_pdf(f"Daily Summary - {summary['summary_date']}", [
        ("Overview", None, [("Date", summary["summary_date"]),
                            ("Total surveys", summary["total_surveys"]),
                            ("Total cost", f"${float(summary['total_survey_cost']):,.2f}"),
                            ("Most observed species", top),
                            ("Generated (UTC)", summary["generated_at"])]),
        ("Low-equipment alerts", None, [(a,) for a in alerts]),
    ])


def write_report(data, filename, out_dir=None):
    """Save PDF bytes under the reports folder and return the file path."""
    out_dir = out_dir or cfg.REPORT_DIR
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, filename)
    with open(path, "wb") as fh:
        fh.write(data)
    return path
