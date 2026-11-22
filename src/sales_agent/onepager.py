"""Turn a Brief into the one-page account brief, as Markdown for the screen and as a Per Scholas styled PDF."""

import copy
import datetime
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config

MAROON = colors.Color(97 / 255, 25 / 255, 64 / 255)
LIGHT = colors.Color(0.96, 0.93, 0.95)
SECTIONS = [  # (title, attribute, how to show an item)
    ("Company Strategy", "strategy", None),
    ("Signals from Job Postings", "job_signals", None),
    ("Why Now for This Product", "fit", None),
    ("Competitor Mentions", "competitors", "competitor"),
    ("Leadership", "leaders", "leader"),
    ("Product and Strategy Summary (10-K)", "filings", None),
    ("Talking Points for the Meeting", "talking_points", None),
]
TRIM_ORDER = ["fit", "job_signals", "strategy", "filings", "leaders", "competitors", "talking_points"]


def numbering(brief):
    """Give the cited sources simple numbers [1], [2], ... in the order they appear on the page."""
    import re
    order = list(dict.fromkeys(re.findall(r"S\d+", brief.headline)))
    for _, attribute, _ in SECTIONS:
        for item in getattr(brief, attribute):
            for source_id in item.get("sources", []):
                if source_id not in order:
                    order.append(source_id)
    return {source_id: number for number, source_id in enumerate(order, start=1)}


def ordered_sources(brief, numbers):
    return sorted(brief.cited_sources(), key=lambda source: numbers.get(source.id, 999))


def item_text(item, kind):
    if kind == "leader":
        return f"**{item['name']}**, {item['title']}. {item.get('relevance', '')}".strip()
    if kind == "competitor":
        return f"**{item['competitor']}**: {item['point']}"
    return item["point"]


def headline_text(brief, numbers):
    import re
    return re.sub(r"\(?\b(S\d+)\b\)?", lambda m: f"[{numbers[m.group(1)]}]" if m.group(1) in numbers else "",
                  brief.headline).strip()


def subtitle(brief):
    i = brief.inputs
    parts = [f"Product: {i.product_name}", f"Category: {brief.product_category}"]
    if i.target_customer:
        parts.append(f"Meeting: {i.target_customer}")
    parts.append(f"Prepared {datetime.date.today():%B %d, %Y}")
    return " | ".join(parts)


# ---------------------------------------------------------------- Markdown

def to_markdown(brief):
    numbers = numbering(brief)
    lines = [f"# Account Brief: {brief.research.company_name}", f"*{subtitle(brief)}*", ""]
    if brief.headline:
        lines += [f"> {headline_text(brief, numbers)}", ""]
    for title, attribute, kind in SECTIONS:
        items = getattr(brief, attribute)
        if not items and attribute not in ("competitors", "filings", "leaders"):
            continue
        lines.append(f"## {title}")
        for item in items:
            refs = "".join(f"[{numbers[s]}]" for s in item["sources"] if s in numbers)
            lines.append(f"- {item_text(item, kind)} {refs}")
        if attribute == "competitors" and brief.competitors_not_found:
            lines.append(f"- No public mention found for: {', '.join(brief.competitors_not_found)}")
        if attribute == "filings" and not items:
            lines.append("- No 10-K found. The company may be private.")
        if attribute == "leaders" and not items:
            lines.append("- No leaders could be confirmed from public sources. Check the company's leadership page.")
        lines.append("")
    if brief.next_steps:
        lines.append("## Next Steps")
        lines += [f"- {step}" for step in brief.next_steps] + [""]
    lines.append("## Sources")
    for source in ordered_sources(brief, numbers):
        meta = ", ".join(x for x in (source.publisher, source.date) if x)
        link = f"[{source.title}]({source.url})" if source.url else source.title
        lines.append(f"{numbers.get(source.id, '?')}. {link}" + (f" ({meta})" if meta else ""))
    return "\n".join(lines)


# ---------------------------------------------------------------- PDF

def styles():
    base = dict(fontName="Helvetica", fontSize=8.6, leading=10.6, textColor=colors.black, alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("title", **{**base, "fontName": "Helvetica-Bold", "fontSize": 17, "leading": 21,
                                            "textColor": MAROON}),
        "sub": ParagraphStyle("sub", **{**base, "fontSize": 8, "textColor": colors.Color(0.35, 0.35, 0.35)}),
        "head": ParagraphStyle("head", **{**base, "fontName": "Helvetica-Bold", "fontSize": 10, "leading": 12,
                                           "textColor": MAROON, "spaceBefore": 5, "spaceAfter": 1}),
        "body": ParagraphStyle("body", **base),
        "bullet": ParagraphStyle("bullet", **{**base, "leftIndent": 9, "bulletIndent": 0, "spaceAfter": 1.2}),
        "headline": ParagraphStyle("headline", **{**base, "fontSize": 9.4, "leading": 12}),
        "source": ParagraphStyle("source", **{**base, "fontSize": 6.8, "leading": 8.2, "leftIndent": 10,
                                               "firstLineIndent": -10}),
    }


SPECIAL_CHARACTERS = {"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-", "\u00a0": " ", "\u202f": " ",
                      "\u2009": " ", "\u200b": ""}


def pdf_safe(text):
    """The PDF font can only draw common characters. AI answers often contain special hyphens and spaces,
    which would show as black boxes, so swap them for plain ones and drop anything else the font cannot draw."""
    for special, plain in SPECIAL_CHARACTERS.items():
        text = text.replace(special, plain)
    return text.encode("cp1252", errors="ignore").decode("cp1252")


def rich(text):
    """Escape text for reportlab and turn **bold** into <b>bold</b>."""
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(pdf_safe(text)))


def story(brief, s):
    numbers = numbering(brief)
    parts = [Paragraph(f"Account Brief: {rich(brief.research.company_name)}", s["title"]),
             Paragraph(rich(subtitle(brief)), s["sub"]), Spacer(1, 6)]
    if brief.headline:
        box = Table([[Paragraph(rich(headline_text(brief, numbers)), s["headline"])]], colWidths=[7.0 * inch])
        box.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("LINEBEFORE", (0, 0), (0, -1), 3, MAROON),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 5),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        parts.append(box)
    for title, attribute, kind in SECTIONS:
        items = getattr(brief, attribute)
        extra = []
        if attribute == "competitors" and brief.competitors_not_found:
            extra.append(f"No public mention found for {', '.join(brief.competitors_not_found)}.")
        if attribute == "filings" and not items:
            extra.append("No 10-K found. The company may be private.")
        if attribute == "leaders" and not items:
            extra.append("No leaders could be confirmed from public sources. Check the company's leadership page.")
        if not items and not extra:
            continue
        parts.append(Paragraph(title, s["head"]))
        for item in items:
            refs = "".join(f"[{numbers[x]}]" for x in item["sources"] if x in numbers)
            parts.append(Paragraph(f"{rich(item_text(item, kind))} <font color='#611940'>{refs}</font>",
                                   s["bullet"], bulletText="•"))
        parts += [Paragraph(rich(e), s["bullet"], bulletText="•") for e in extra]
    if brief.next_steps:
        parts.append(Paragraph("Next Steps", s["head"]))
        parts += [Paragraph(rich(step), s["bullet"], bulletText="•") for step in brief.next_steps]
    parts.append(Paragraph("Sources", s["head"]))
    for source in ordered_sources(brief, numbers):
        meta = ", ".join(rich(x) for x in (source.publisher, source.date) if x)
        title = rich(source.title)
        if source.url:
            title = f"<link href='{escape(source.url)}' color='#611940'>{title}</link>"
        parts.append(Paragraph(f"[{numbers.get(source.id, '?')}] {title}" + (f" ({meta})" if meta else ""), s["source"]))
    return parts


def page_frame(canvas, doc):
    width, height = letter
    canvas.saveState()
    canvas.setFillColor(MAROON)
    canvas.rect(0, height - 10, width, 10, stroke=0, fill=1)
    canvas.rect(0, 0, width, 14, stroke=0, fill=1)
    logo = config.ASSETS / "perscholas_logo.png"
    if logo.exists():
        canvas.drawImage(str(logo), 0.75 * inch, height - 0.62 * inch, width=1.35 * inch, height=0.19 * inch,
                         mask="auto")
    canvas.setFont("Helvetica", 6.5)
    canvas.setFillColor(colors.white)
    canvas.drawString(0.75 * inch, 4, "Generated by the Sales Assistant Agent from public web sources. "
                                      "Check key facts before the meeting.")
    canvas.restoreState()


def render(brief):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.8 * inch, bottomMargin=0.45 * inch, title=f"Account Brief: {brief.research.company_name}",
                            author="Sales Assistant Agent")
    doc.build(story(brief, styles()), onFirstPage=page_frame, onLaterPages=page_frame)
    return buffer.getvalue()


def page_count(pdf):
    from pypdf import PdfReader
    return len(PdfReader(io.BytesIO(pdf)).pages)


def to_pdf(brief):
    """Build the PDF. If it runs past one page, drop the least important last points until it fits."""
    brief = copy.copy(brief)
    for attribute in TRIM_ORDER:
        setattr(brief, attribute, list(getattr(brief, attribute)))
    pdf = render(brief)
    for _ in range(200):
        if page_count(pdf) <= 1:
            break
        for attribute in TRIM_ORDER:
            if len(getattr(brief, attribute)) > 1:
                getattr(brief, attribute).pop()
                break
        else:
            break
        pdf = render(brief)
    return pdf
