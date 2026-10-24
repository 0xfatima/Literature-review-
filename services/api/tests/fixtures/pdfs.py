"""Real PDFs for tests, generated with PyMuPDF (the same engine the application uses), so parsing, page numbering,
OCR candidates, encryption and damage are exercised on genuine files, not on mocks."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pymupdf

BODY = pymupdf.Rect(72, 72, 523, 770)


def _page_text(page: pymupdf.Page, text: str) -> None:
    left = page.insert_textbox(BODY, text, fontsize=10)
    assert left >= 0, "test text does not fit on the page"


def make_pdf(
    pages: list[str | None],
    *,
    metadata: dict[str, str] | None = None,
    password: str | None = None,
    javascript: bool = False,
) -> bytes:
    """One page per entry; None = a blank page."""
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            _page_text(page, text)
    if metadata:
        doc.set_metadata(metadata)
    if javascript:
        doc.xref_set_key(doc.pdf_catalog(), "OpenAction", "<< /S /JavaScript /JS (app.alert\\(1\\)) >>")
    return _bytes(doc, password)


def scanned_page_pdf(text: str, *, before: list[str | None] | None = None) -> bytes:
    """A PDF whose LAST page is only a picture of `text` (no text layer), after optional ordinary pages."""
    src = pymupdf.open()
    sp = src.new_page()
    sp.insert_textbox(pymupdf.Rect(40, 60, 560, 400), text, fontsize=18)
    pix = sp.get_pixmap(dpi=150)
    doc = pymupdf.open()
    for t in before or []:
        page = doc.new_page()
        if t:
            _page_text(page, t)
    img_page = doc.new_page()
    img_page.insert_image(img_page.rect, pixmap=pix)
    return _bytes(doc, None)


def _bytes(doc: pymupdf.Document, password: str | None) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "t.pdf"
        if password:
            doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")
        else:
            doc.save(out, garbage=0, deflate=False)
        return out.read_bytes()


def corrupt_pdf() -> bytes:
    return b"%PDF-1.7\n" + b"1 0 obj\n<< /Type /Catalog /Pages 99 0 R >>\nthis is not a real pdf body\n" * 20


def truncated(data: bytes, keep: float = 0.5) -> bytes:
    return data[: max(16, int(len(data) * keep))]


PARA = (
    "Background. Depression is common in later life and exercise may help. We randomised {n} participants to an "
    "aerobic programme or usual care and measured symptoms at twelve weeks. Results are reported with confidence "
    "intervals. "
)


def paper_pdf(title: str, authors: str, year: int, doi: str | None, *, pages: int = 2, extra: str = "") -> bytes:
    """A plausible first page (title, authors, year, DOI line) followed by body text."""
    first = (
        f"{title}\n{authors}\nPublished {year}\n"
        + (f"https://doi.org/{doi}\n" if doi else "")
        + "\n"
        + PARA.format(n=120)
    )
    rest = [PARA.format(n=100 + i) * 3 + extra for i in range(pages - 1)]
    return make_pdf([first, *rest], metadata={"title": title})
