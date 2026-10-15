"""
Job-level fraud investigation PDF report.

On-demand generate hota hai (kabhi store nahi karte — guest-job orphan rows
jaisa ek aur "kabhi clean na hone wala file" problem nahi banana), aur
login-required hai, kyunki guest ke predictions kabhi DB mein save hi nahi
hote, isliye unka report banane ke liye data maujood hi nahi hota.
"""

import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _xml_escape

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------- Font setup ----------
# CRITICAL: ReportLab ke built-in fonts (Helvetica etc.) mein Rupee symbol
# (U+20B9) ka glyph hi nahi hota — woh ek solid black box bankar print hota
# hai. Isliye ek Unicode-capable TTF font embed karte hain, system pe depend
# kiye bina (taaki Render jaisे kisi bhi Linux host pe guaranteed kaam kare).
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_REGULAR_PATH = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_BOLD_PATH = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")

if "ReportFont" not in pdfmetrics.getRegisteredFontNames():
    pdfmetrics.registerFont(TTFont("ReportFont", _REGULAR_PATH))
    pdfmetrics.registerFont(TTFont("ReportFont-Bold", _BOLD_PATH))

# Site ke actual CSS/templates se confirmed colors (landing.html, dashboard_*.html
# se nikale gaye hex codes) — fraud/review ke exact dashboard.css variable values
# dekhe nahi hain (woh file nahi mili), isliye unke liye in dono ke saath
# harmonize karte hue reasonable editorial-red/amber chune hain.
BRAND_TEAL = colors.HexColor("#1B6A7E")
BORDER_GREY = colors.HexColor("#E5E4DE")
PANEL_BG = colors.HexColor("#F1F0EC")
TEXT_PRIMARY = colors.HexColor("#111111")
TEXT_MUTED = colors.HexColor("#75736D")

RISK_COLORS = {
    "FRAUD": colors.HexColor("#B5433A"),
    "REVIEW": colors.HexColor("#B8860B"),
    "NORMAL": colors.HexColor("#3F9A5C"),  # dashboard_check.html/dashboard_job.html se confirmed exact match
}

MAX_DETAIL_ROWS = 100  # Render ke free-tier RAM/time budget ke andar rehne ke liye


def _p(text) -> str:
    """
    reportlab ka Paragraph ek mini-XML/HTML markup parser hai — bare
    &, <, > ko entity/tag ki tarah samajh leta hai. CSV se aaye account
    names (nameOrig/nameDest waise hi user-controlled hain jaise HTML
    templates mein, jahan escapeHtml() use hota hai) mein agar "&" ho
    (jaise "AT&T_Corp"), to escape kiye bina text silently CORRUPT ho
    jaata hai (verified: "AT&T_Corp" -> "AT&T;_Corp"). Har Paragraph()
    mein jaane wale user-controlled text ko isi se guzarna zaroori hai.
    """
    return _xml_escape(str(text) if text is not None else "")


def _inr(v) -> str:
    try:
        return f"\u20b9{float(v):,.0f}"
    except (TypeError, ValueError):
        return "\u2014"


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle("title", parent=base["Title"], fontName="ReportFont-Bold", fontSize=18, textColor=BRAND_TEAL),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName="ReportFont-Bold",
                                   fontSize=12, spaceBefore=14, spaceAfter=6, textColor=BRAND_TEAL),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName="ReportFont", fontSize=9.5, leading=13, textColor=TEXT_PRIMARY),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontName="ReportFont", fontSize=9, textColor=TEXT_MUTED),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontName="ReportFont", fontSize=8.5, leading=11, textColor=TEXT_PRIMARY),
        "cell_bold": ParagraphStyle("cell_bold", parent=base["Normal"], fontName="ReportFont-Bold", fontSize=8.5, textColor=TEXT_PRIMARY),
        "center": ParagraphStyle("center", parent=base["Normal"], fontName="ReportFont", fontSize=9.5, alignment=TA_CENTER, textColor=TEXT_PRIMARY),
    }
    return styles


def _risk_bar(label: str, count: int, total: int, color) -> Table:
    """Ek chhoti horizontal bar — pure ReportLab shapes se, full charting library ke bina."""
    pct = (count / total * 100) if total else 0
    bar_width_pts = max(2, pct / 100 * 300)
    t = Table(
        [[Paragraph(f"{label}", _styles()["cell_bold"]),
          Table([[""]], colWidths=[bar_width_pts], rowHeights=[10], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), color)])),
          Paragraph(f"{count:,}  ({pct:.1f}%)", _styles()["cell"])]],
        colWidths=[70, 310, 110],
    )
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    return t


def _graph_flag_summary(predictions: list) -> dict:
    """Job ke flagged rows mein kaunse graph patterns kitni baar mile — counts."""
    counts = {"round_trip": 0, "fan_out": 0, "fan_in": 0, "structuring": 0}
    for p in predictions:
        for f in (p.get("graph_flags") or []):
            pattern = f.get("pattern") if isinstance(f, dict) else None
            if pattern in counts:
                counts[pattern] += 1
    return counts


def _top_reason(p: dict) -> str:
    """Ek row ke liye ek-line summary — SHAP ka top feature, warna graph ka pehla flag, warna khaali."""
    explanation = p.get("explanation") or {}
    top_features = explanation.get("top_features") or []
    if top_features:
        return top_features[0].get("reason", "")
    flags = p.get("graph_flags") or []
    if flags and isinstance(flags[0], dict):
        return f"Graph pattern: {flags[0].get('pattern', '')}"
    return "\u2014"


def generate_job_report_pdf(job: dict, predictions: list, output_path: str) -> None:
    """
    job: get_job() ka row (dict)
    predictions: get_predictions_by_job() ki list
    output_path: PDF kahan likhni hai (caller hi cleanup karega bhejne ke baad)
    """
    styles = _styles()
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    story = []

    # ---------- Header ----------
    story.append(Paragraph("Fund Flow Fraud Detection", styles["title"]))
    story.append(Paragraph("Financial Transaction Risk Analysis Report", styles["meta"]))
    story.append(Spacer(1, 10))
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    meta_table = Table([
        ["Source file:", Paragraph(_p(job.get("filename") or "\u2014"), styles["body"])],
        ["Report ID:", Paragraph(_p(job.get("job_id", "\u2014")), styles["body"])],
        ["Generated on:", generated_at],
    ], colWidths=[90, 400])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "ReportFont-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "ReportFont"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", color=BORDER_GREY, thickness=1))

    # ---------- 01 Executive Summary ----------
    import json as _json
    summary = {}
    if job.get("result_summary"):
        try:
            summary = _json.loads(job["result_summary"])
        except (ValueError, TypeError):
            summary = {}
    total = summary.get("total", len(predictions))
    fraud = summary.get("fraud", sum(1 for p in predictions if p.get("risk_level") == "FRAUD"))
    review = summary.get("review", sum(1 for p in predictions if p.get("risk_level") == "REVIEW"))
    normal = summary.get("normal", sum(1 for p in predictions if p.get("risk_level") == "NORMAL"))

    story.append(Paragraph("01 \u2014 Executive Summary", styles["section"]))
    summary_table = Table([
        ["Total transactions", "Flagged for fraud", "Needs review", "Normal"],
        [f"{total:,}", f"{fraud:,}", f"{review:,}", f"{normal:,}"],
    ], colWidths=[130, 130, 130, 130])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "ReportFont"),
        ("FONTNAME", (0, 1), (-1, 1), "ReportFont-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("FONTSIZE", (0, 1), (-1, 1), 15),
        ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_MUTED),
        # Dashboard ke stat-cards jaisa hi color-coding: Total=brand teal,
        # Fraud/Review/Normal apne risk-color mein
        ("TEXTCOLOR", (0, 1), (0, 1), BRAND_TEAL),
        ("TEXTCOLOR", (1, 1), (1, 1), RISK_COLORS["FRAUD"]),
        ("TEXTCOLOR", (2, 1), (2, 1), RISK_COLORS["REVIEW"]),
        ("TEXTCOLOR", (3, 1), (3, 1), RISK_COLORS["NORMAL"]),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER_GREY),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
        ("BACKGROUND", (0, 0), (-1, 0), PANEL_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(summary_table)

    # ---------- 02 Risk Distribution ----------
    story.append(Paragraph("02 \u2014 Risk Distribution", styles["section"]))
    story.append(_risk_bar("FRAUD", fraud, total, RISK_COLORS["FRAUD"]))
    story.append(Spacer(1, 4))
    story.append(_risk_bar("REVIEW", review, total, RISK_COLORS["REVIEW"]))
    story.append(Spacer(1, 4))
    story.append(_risk_bar("NORMAL", normal, total, RISK_COLORS["NORMAL"]))

    # ---------- 03 Financial Summary (flagged transactions only) ----------
    flagged = [p for p in predictions if p.get("risk_level") in ("FRAUD", "REVIEW")]
    story.append(Paragraph("03 \u2014 Financial Summary", styles["section"]))
    if flagged:
        amounts = [float(p.get("amount_inr") or 0) for p in flagged]
        fin_table = Table([
            ["Total value of flagged transactions", "Average flagged amount", "Highest flagged amount"],
            [_inr(sum(amounts)), _inr(sum(amounts) / len(amounts)), _inr(max(amounts))],
        ], colWidths=[180, 180, 180])
        fin_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "ReportFont"),
            ("FONTNAME", (0, 1), (-1, 1), "ReportFont-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("FONTSIZE", (0, 1), (-1, 1), 12),
            ("TEXTCOLOR", (0, 0), (-1, 0), TEXT_MUTED),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER_GREY),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(fin_table)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Note: this is the total value of transactions flagged for review, not a confirmed fraud-loss figure.",
            styles["meta"],
        ))
    else:
        story.append(Paragraph("No transactions were flagged in this job.", styles["body"]))

    # ---------- 04 Graph Findings ----------
    story.append(Paragraph("04 \u2014 Fund-Flow Graph Findings", styles["section"]))
    gcounts = _graph_flag_summary(predictions)
    if any(gcounts.values()):
        labels = {
            "round_trip": "Round-trip fund flow (A \u2192 B \u2192 ... \u2192 A)",
            "fan_out": "Fan-out (one account \u2192 many receivers)",
            "fan_in": "Fan-in (many senders \u2192 one account)",
            "structuring": "Structuring (repeated sub-threshold amounts)",
        }
        rows = [[labels[k], str(v)] for k, v in gcounts.items() if v]
        g_table = Table([["Pattern", "Occurrences"]] + rows, colWidths=[380, 100])
        g_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "ReportFont-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "ReportFont"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), PANEL_BG),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER_GREY),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(g_table)
    else:
        story.append(Paragraph("No fund-flow graph patterns (round-trip, fan-out, fan-in, structuring) were detected in this job.", styles["body"]))

    # ---------- 05 Detailed Flagged Transactions ----------
    story.append(Paragraph("05 \u2014 Flagged Transaction Details", styles["section"]))
    if flagged:
        shown = flagged[:MAX_DETAIL_ROWS]
        header = ["Sender", "Receiver", "Type", "Amount", "Risk", "Top reason"]
        rows = [header]
        for p in shown:
            rows.append([
                Paragraph(_p(p.get("nameOrig", "")), styles["cell"]),
                Paragraph(_p(p.get("nameDest", "")), styles["cell"]),
                Paragraph(_p(p.get("type", "")), styles["cell"]),
                Paragraph(_p(_inr(p.get("amount_inr"))), styles["cell"]),
                Paragraph(_p(p.get("risk_level", "")), styles["cell_bold"]),
                Paragraph(_p(_top_reason(p)), styles["cell"]),
            ])
        detail_table = Table(rows, colWidths=[78, 78, 42, 58, 50, 192], repeatRows=1)
        style_cmds = [
            ("FONTNAME", (0, 0), (-1, 0), "ReportFont-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8.5),
            ("BACKGROUND", (0, 0), (-1, 0), PANEL_BG),
            ("GRID", (0, 0), (-1, -1), 0.4, BORDER_GREY),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for i, p in enumerate(shown, start=1):
            color = RISK_COLORS.get(p.get("risk_level"), colors.black)
            style_cmds.append(("TEXTCOLOR", (4, i), (4, i), color))
        detail_table.setStyle(TableStyle(style_cmds))
        story.append(detail_table)
        if len(flagged) > MAX_DETAIL_ROWS:
            story.append(Spacer(1, 6))
            story.append(Paragraph(
                f"Showing the first {MAX_DETAIL_ROWS} of {len(flagged)} flagged transactions.",
                styles["meta"],
            ))
    else:
        story.append(Paragraph("No flagged transactions to display.", styles["body"]))

    # ---------- 06 Methodology & Disclaimer ----------
    story.append(Paragraph("06 \u2014 Methodology & Disclaimer", styles["section"]))
    story.append(Paragraph(
        "Risk scores combine an XGBoost + Isolation Forest ensemble with a fund-flow graph "
        "analysis engine (round-trip, fan-out, fan-in, and structuring pattern detection). "
        "Risk flags indicate statistical and behavioral signals, not confirmed proof of fraud, "
        "and should be reviewed by a human analyst before any action is taken. This system was "
        "developed and validated on a PaySim-derived dataset with an Indian regulatory context "
        "overlay, not live banking data.",
        styles["body"],
    ))

    doc.build(story)