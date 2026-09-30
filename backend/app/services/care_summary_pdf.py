"""Renders the Living Care Summary as a PDF a clinician can read in ten minutes.

The layout follows the order a clinician actually reads an appointment in: who
this person is, what changed, what is worrying the caregiver, and what is not
known. Sections that are empty say so rather than being dropped, because an
absent section reads as "reviewed and clear" and a missing medication timeline
is not that.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib import colors


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "T", parent=base["Title"], fontSize=19, leading=23, spaceAfter=2, alignment=TA_LEFT
        ),
        "sub": ParagraphStyle("S", parent=base["Normal"], fontSize=9.5, textColor=colors.HexColor("#555555")),
        "h": ParagraphStyle(
            "H", parent=base["Heading2"], fontSize=12.5, leading=15, spaceBefore=11, spaceAfter=4,
            textColor=colors.HexColor("#1a1a1a"),
        ),
        "body": ParagraphStyle("B", parent=base["Normal"], fontSize=9.3, leading=13, spaceAfter=3),
        "small": ParagraphStyle(
            "Sm", parent=base["Normal"], fontSize=8.1, leading=10.5, textColor=colors.HexColor("#666666")
        ),
        "cell": ParagraphStyle("C", parent=base["Normal"], fontSize=8.4, leading=10.8),
        "cellb": ParagraphStyle("Cb", parent=base["Normal"], fontSize=8.4, leading=10.8,
                                 textColor=colors.HexColor("#8a1f1f")),
    }


def _esc(value: Any) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _fmt_date(value: Optional[str]) -> str:
    if not value:
        return "date not recorded"
    try:
        return datetime.fromisoformat(value).strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(value)


def render_summary_pdf(summary: Dict[str, Any]) -> bytes:
    styles = _styles()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title="Care Summary - {}".format(summary.get("person", {}).get("name", "Person")),
        author="SolenOS",
    )

    story: List[Any] = []
    person = summary.get("person", {})

    # ---- Header ----
    story.append(Paragraph("Care Summary", styles["title"]))
    story.append(Paragraph(
        "{} &#183; generated {} &#183; last {} days of record".format(
            _esc(person.get("name")),
            _fmt_date(summary.get("generated_at")),
            _esc(summary.get("window_days")),
        ),
        styles["sub"],
    ))
    story.append(Spacer(1, 7))
    story.append(Paragraph(
        "<b>Not a medical device.</b> This document records what caregivers observed and what the "
        "record can and cannot establish. It makes no diagnosis and replaces no clinical judgement.",
        styles["small"],
    ))

    team = summary.get("care_team") or []
    if team:
        story.append(Spacer(1, 5))
        story.append(Paragraph(
            "Care team: " + _esc(", ".join(sorted(set(team.values())))), styles["small"]
        ))

    # ---- What the caregiver wants to raise ----
    points = summary.get("discussion_points") or []
    story.append(Paragraph("For discussion", styles["h"]))
    if points:
        for point in points:
            story.append(Paragraph("&bull; " + _esc(point), styles["body"]))
    else:
        story.append(Paragraph(
            "Nothing has been recorded to raise. This usually means the daily forms are not being "
            "completed rather than that nothing is happening.", styles["small"]
        ))

    # ---- Medication ----
    adherence = summary.get("medication_adherence") or []
    story.append(Paragraph("Medication", styles["h"]))
    if adherence:
        rows = [["Medication", "Taken", "Refused", "Missed", "Adherence", "Note"]]
        for item in adherence:
            rate = item.get("adherence_rate")
            rows.append([
                _esc(item.get("medication")),
                _esc(item.get("given")),
                _esc(item.get("refused")),
                _esc(item.get("missed")),
                "n/a" if rate is None else "{}%".format(int(round(rate * 100))),
                _esc(item.get("what_is_not_established") or ""),
            ])
        table = Table(rows, colWidths=[26 * mm, 15 * mm, 17 * mm, 15 * mm, 20 * mm, 77 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.4),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No medication has been recorded.", styles["small"]))

    # ---- What is not known ----
    gaps = summary.get("information_gaps") or []
    contradictions = summary.get("open_contradictions") or []
    if gaps or contradictions:
        story.append(Paragraph("What is not established", styles["h"]))
        for gap in gaps:
            text = "{} {}".format(_esc(gap.get("gap")), _esc(gap.get("why_it_matters")))
            # The gap text usually ends in a period, which would read as ".."
            # once the explanation is appended.
            story.append(KeepTogether(Paragraph(
                "<b>{}</b> {}".format(text.rstrip("."), "."), styles["body"]
            )))
        for c in contradictions:
            story.append(KeepTogether(Paragraph(
                "<b>Contradiction.</b> {} {}".format(
                    _esc(c.get("summary")),
                    ("Ask: {}".format(_esc(c.get("what_to_ask")))) if c.get("what_to_ask") else "",
                ),
                styles["body"],
            )))

    flagged = summary.get("vitals_out_of_range") or []
    if flagged:
        story.append(Paragraph("Readings outside the usual range", styles["h"]))
        for v in flagged:
            story.append(Paragraph(
                "{} = {}{} on {}, recorded by {}. Not a finding about a cause.".format(
                    _esc(v.get("vital", "").replace("_", " ")),
                    _esc(v.get("value")),
                    " {}".format(_esc(v.get("unit"))) if v.get("unit") else "",
                    _fmt_date(v.get("measured_at")),
                    _esc(v.get("recorded_by")),
                ),
                styles["body"],
            ))

    story.append(PageBreak())

    # ---- Timeline ----
    story.append(Paragraph("Observation timeline", styles["h"]))
    story.append(Paragraph(
        "Newest first. Every row is attributed to the caregiver who recorded it.", styles["small"]
    ))
    story.append(Spacer(1, 4))
    timeline = summary.get("timeline") or []
    if timeline:
        rows = [["When", "Recorded by", "What"]]
        for event in timeline:
            description = _esc(event.get("description") or event.get("title") or "")
            rows.append([
                _fmt_date(event.get("occurred_at")),
                _esc(event.get("recorded_by")),
                description,
            ])
        table = Table(rows, colWidths=[30 * mm, 26 * mm, 114 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.2),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("Nothing has been recorded in this window.", styles["small"]))

    scales = summary.get("structured_observations") or []
    if scales:
        story.append(Paragraph("Five-point observations", styles["h"]))
        rows = [["When", "Axis", "Score", "Scale", "Recorded by"]]
        for s in scales:
            rows.append([
                _fmt_date(s.get("observed_at")),
                _esc(s.get("axis", "").replace("_", " ")),
                _esc(s.get("score")),
                _esc(s.get("scale")),
                _esc(s.get("recorded_by")),
            ])
        table = Table(rows, colWidths=[30 * mm, 26 * mm, 14 * mm, 74 * mm, 26 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("FONTSIZE", (0, 0), (-1, -1), 8.2),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(table)

    open_questions = summary.get("open_questions") or []
    if open_questions:
        story.append(Paragraph("Open questions being re-checked", styles["h"]))
        for q in open_questions:
            story.append(Paragraph(
                "&bull; <b>{}</b> (due {})".format(
                    _esc(q.get("what_to_recheck")), _fmt_date(q.get("due_at"))
                ),
                styles["body"],
            ))
            if q.get("why_open"):
                story.append(Paragraph(_esc(q["why_open"]), styles["small"]))

    doc.build(story)
    return buffer.getvalue()
