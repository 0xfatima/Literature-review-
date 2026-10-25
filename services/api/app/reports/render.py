"""Render a draft version to Markdown, DOCX and CSV. Stdlib only (DOCX is written as WordprocessingML with zipfile,
so no extra dependency); deterministic: the same draft version yields the same bytes.

Every rendering starts with the verification notice; nothing here claims publication readiness."""

from __future__ import annotations

import csv
import io
import zipfile
from typing import Any
from xml.sax.saxutils import escape

from app.reports.validate import CITE

CERTAINTY_LABELS = {
    "supported": "supported by consistent evidence",
    "uncertain": "uncertain interpretation",
    "conflicting": "conflicting evidence",
    "insufficient": "insufficient evidence",
}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def notice(v: dict[str, Any]) -> str:
    s = v.get("validation", {}).get("summary", {})
    return (
        f"DRAFT v{v['version']} generated from the recorded review data. Status: {s.get('label', 'unverified draft')} "
        f"({s.get('errors', 0)} error(s), {s.get('warnings', 0)} warning(s), {s.get('sections_verified', 0)}/"
        f"{s.get('sections_total', 0)} sections verified by a person). This document is not publication-ready: the "
        "authors must verify every section, number and citation against the sources, and the review must undergo "
        "peer review."
    )


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(_cell(x) for x in v)
    return str(v)


def _inline(text: str) -> str:
    """[@key] -> [key] for display."""
    return CITE.sub(lambda m: f"[{m.group(1)}]", text or "")


def block_text(b: dict[str, Any]) -> str:
    text = _inline(b.get("text") or "")
    if b.get("type") == "statement" and b.get("certainty"):
        text += f" (Certainty: {CERTAINTY_LABELS.get(b['certainty'], b['certainty'])}.)"
    return text


def _safe_csv(v: Any) -> str:
    s = _cell(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def table_csv(table: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_safe_csv(c) for c in table.get("columns", [])])
    for row in table.get("rows", []):
        w.writerow([_safe_csv(c) for c in row])
    return buf.getvalue()


def references_csv(refs: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["key", "paper_id", "reference", "doi", "pmid", "year"])
    for r in refs:
        w.writerow(
            [
                r["key"],
                r["paper_id"],
                _safe_csv(r["formatted"]),
                r.get("doi") or "",
                r.get("pmid") or "",
                r.get("year") or "",
            ]
        )
    return buf.getvalue()


# ---------- Markdown ----------


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def render_markdown(v: dict[str, Any]) -> str:
    out = [f"# {v['title']}", "", f"> {notice(v)}", ""]
    for sec in v["sections"]:
        out += [f"## {sec['title']}", ""]
        for b in sec.get("blocks") or []:
            t = b.get("type")
            if t in ("paragraph", "statement"):
                out += [block_text(b), ""]
            elif t == "table":
                tb = b["table"]
                out += [f"**{tb.get('caption', '')}**", ""]
                cols = tb.get("columns", [])
                if cols:
                    out.append("| " + " | ".join(_md_escape(_cell(c)) for c in cols) + " |")
                    out.append("|" + "---|" * len(cols))
                    for row in tb.get("rows", []):
                        out.append("| " + " | ".join(_md_escape(_cell(c)) for c in row) + " |")
                if not tb.get("rows"):
                    out.append("_No rows recorded._")
                out.append("")
            elif t == "references":
                for r in v.get("references") or []:
                    out.append(f"- [{r['key']}] {r['formatted']}")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------- DOCX ----------

_CT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)
_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/></Relationships>'
)
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _style(sid: str, name: str, size: int, bold: bool = True) -> str:
    b = "<w:b/>" if bold else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
        f'<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>'
        f'</w:pPr><w:rPr>{b}<w:sz w:val="{size}"/></w:rPr></w:style>'
    )


_STYLES = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/>'
    "</w:rPr></w:rPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="120"/></w:pPr></w:style>'
    + _style("Title", "Title", 36)
    + _style("Heading1", "heading 1", 30)
    + _style("Heading2", "heading 2", 24)
    + '<w:style w:type="paragraph" w:styleId="Notice"><w:name w:val="Notice"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:pBdr><w:top w:val="single" w:sz="8" w:space="4" w:color="C00000"/>'
    '<w:bottom w:val="single" w:sz="8" w:space="4" w:color="C00000"/></w:pBdr></w:pPr>'
    '<w:rPr><w:i/><w:color w:val="C00000"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="Grid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
    + "".join(
        f'<w:{e} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
        for e in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    + "</w:tblBorders></w:tblPr></w:style></w:styles>"
)


def _p(text: str, style: str | None = None, bold: bool = False) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:p>{ppr}<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _tbl(table: dict[str, Any]) -> str:
    def row(cells: list[Any], header: bool = False) -> str:
        tc = "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>{_p(_cell(c), bold=header)}</w:tc>' for c in cells
        )
        hdr = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
        return f"<w:tr>{hdr}{tc}</w:tr>"

    cols = table.get("columns", [])
    body = row(cols, True) + "".join(row(r) for r in table.get("rows", []))
    return f'<w:tbl><w:tblPr><w:tblStyle w:val="Grid"/><w:tblW w:w="5000" w:type="pct"/></w:tblPr>{body}</w:tbl>'


def render_docx(v: dict[str, Any]) -> bytes:
    parts = [_p(v["title"], "Title"), _p(notice(v), "Notice")]
    for sec in v["sections"]:
        parts.append(_p(sec["title"], "Heading1"))
        for b in sec.get("blocks") or []:
            t = b.get("type")
            if t in ("paragraph", "statement"):
                parts.append(_p(block_text(b)))
            elif t == "table":
                tb = b["table"]
                parts.append(_p(tb.get("caption", ""), bold=True))
                if tb.get("columns"):
                    parts.append(_tbl(tb))
                if not tb.get("rows"):
                    parts.append(_p("No rows recorded."))
            elif t == "references":
                parts.extend(_p(f"[{r['key']}] {r['formatted']}") for r in v.get("references") or [])
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W}><w:body>{"".join(parts)}'
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
        'w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>'
    )
    return zip_bytes(
        {
            "[Content_Types].xml": _CT,
            "_rels/.rels": _RELS,
            "word/document.xml": document,
            "word/styles.xml": _STYLES,
            "word/_rels/document.xml.rels": _DOC_RELS,
        }
    )


def zip_bytes(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    return buf.getvalue()
