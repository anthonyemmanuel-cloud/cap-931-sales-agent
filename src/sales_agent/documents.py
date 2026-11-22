"""Read the optional product sheet or deck the sales rep uploads (PDF, Word, PowerPoint or text)."""

import io
import re

SUPPORTED = ("pdf", "docx", "pptx", "txt", "md")


def read_upload(name, data):
    """Return the plain text of an uploaded file. Raises ValueError for types we cannot read."""
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension not in SUPPORTED:
        raise ValueError(f"'{name}' is not supported. Please upload a {', '.join(SUPPORTED)} file.")
    if extension == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages[:20])
    elif extension == "docx":
        from docx import Document
        document = Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            parts += [" | ".join(cell.text for cell in row.cells) for row in table.rows]
        text = "\n".join(parts)
    elif extension == "pptx":
        from pptx import Presentation
        deck = Presentation(io.BytesIO(data))
        slides = []
        for number, slide in enumerate(deck.slides, start=1):
            words = [shape.text_frame.text for shape in slide.shapes if shape.has_text_frame]
            slides.append(f"Slide {number}: " + " / ".join(w for w in words if w.strip()))
        text = "\n".join(slides)
    else:
        text = data.decode("utf-8", errors="ignore")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError(f"No text could be read from '{name}'. If it is a scanned PDF, upload a text version.")
    return text
