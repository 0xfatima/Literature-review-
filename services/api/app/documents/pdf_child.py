"""Runs INSIDE a resource-limited child process. Untrusted PDFs are parsed here, never in the worker itself, so a
parser crash, hang or memory bomb cannot take the worker down or loop through redelivery.

Standalone on purpose: it imports only the standard library and PyMuPDF, never the `app` package, so it cannot read
application settings or secrets. It is started with `python -I` and a minimal environment (see runner.py).

Usage:
  pdf_child.py extract PDF --max-pages N --max-chars N --mem-mb M --cpu-s S
  pdf_child.py render PDF OUTDIR --pages 1,2,3 --dpi D --max-pixels N --mem-mb M --cpu-s S

Output: JSON lines on stdout, one object each, always ending with {"type": "done"} or {"type": "error", ...}.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

ACTIVE_CONTENT = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA)\b")
META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate", "format")


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def fail(code: str, message: str, **extra: object) -> NoReturn:
    emit({"type": "error", "code": code, "message": message, **extra})
    sys.exit(0)  # a reported failure is a normal exit; only a crash or timeout exits abnormally


def load_pymupdf() -> Any:
    """PyMuPDF has no complete type information; it is used through `Any` on purpose."""
    import pymupdf

    return pymupdf


def limit_resources(mem_mb: int, cpu_s: int) -> None:
    try:
        import resource
    except ImportError:  # not POSIX: the parent's timeout is then the only limit
        return
    if mem_mb > 0:
        limit = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    if cpu_s > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def active_content(path: Path) -> list[str]:
    """Heuristic only: names that commonly mark scripts, launch actions or embedded files. Not a malware scan."""
    found: set[str] = set()
    carry = b""
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            data = carry + chunk
            found.update(m.group(1).decode("ascii") for m in ACTIVE_CONTENT.finditer(data))
            carry = data[-32:]
    return sorted(found)


def has_eof_marker(path: Path) -> bool:
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - 1024))
        return b"%%EOF" in f.read()


def image_cover(page: Any, rect_area: float) -> float:
    try:
        total = 0.0
        for info in page.get_image_info():
            x0, y0, x1, y1 = info["bbox"]
            total += max(0.0, min(x1, page.rect.x1) - max(x0, page.rect.x0)) * max(
                0.0, min(y1, page.rect.y1) - max(y0, page.rect.y0)
            )
        return min(1.0, total / rect_area) if rect_area > 0 else 0.0
    except Exception:  # noqa: BLE001  a damaged image table must not fail the page's text
        return 0.0


def open_pdf(pymupdf: Any, path: str) -> Any:
    try:
        pymupdf.TOOLS.mupdf_display_errors(False)
        doc = pymupdf.open(path, filetype="pdf")  # force the PDF parser: never sniff other formats
    except Exception as e:  # noqa: BLE001
        fail("corrupt", f"The PDF could not be opened ({type(e).__name__}). The file may be damaged or truncated.")
    if doc.needs_pass:
        fail("password_protected", "The PDF is password-protected. Upload a copy without a password.")
    return doc


def extract(args: argparse.Namespace) -> None:
    pymupdf = load_pymupdf()
    doc = open_pdf(pymupdf, args.pdf)
    n = doc.page_count
    if n == 0:
        fail("no_pages", "No readable pages were found. The file is probably damaged or is not a real PDF.")
    if n > args.max_pages:
        fail("too_many_pages", f"The PDF has {n} pages; the limit is {args.max_pages}.", page_count=n)
    meta = {k: str(doc.metadata.get(k) or "")[:500] for k in META_KEYS}
    emit(
        {
            "type": "header",
            "page_count": n,
            "metadata": meta,
            "is_encrypted": bool(doc.is_encrypted),
            "is_repaired": bool(getattr(doc, "is_repaired", False)),
            "eof_marker": has_eof_marker(Path(args.pdf)),
            "embedded_files": int(doc.embfile_count()) if hasattr(doc, "embfile_count") else 0,
            "active_content": active_content(Path(args.pdf)),
        }
    )
    chars = 0
    for i in range(n):
        try:
            page = doc.load_page(i)
            text = page.get_text("text")
            images = len(page.get_images(full=False))
            area = abs(page.rect.width * page.rect.height)
            cover = image_cover(page, area) if images else 0.0
            chars += len(text)
            if chars > args.max_chars:
                fail("text_too_large", "The PDF contains more text than the extraction limit allows.")
            emit({"type": "page", "page_no": i + 1, "text": text, "image_count": images, "image_cover": cover})
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001  one bad page must not lose the others
            emit(
                {
                    "type": "page",
                    "page_no": i + 1,
                    "text": "",
                    "image_count": 0,
                    "image_cover": 0.0,
                    "error": type(e).__name__,
                }
            )
    warnings = pymupdf.TOOLS.mupdf_warnings() or ""
    emit({"type": "done", "pages": n, "engine_warnings": warnings.splitlines()[:20]})


def render(args: argparse.Namespace) -> None:
    pymupdf = load_pymupdf()
    doc = open_pdf(pymupdf, args.pdf)
    out = Path(args.outdir)
    for n in [int(p) for p in args.pages.split(",") if p]:
        try:
            page = doc.load_page(n - 1)
            zoom = args.dpi / 72.0
            w, h = page.rect.width * zoom, page.rect.height * zoom
            if w * h > args.max_pixels:
                zoom *= math.sqrt(args.max_pixels / (w * h))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
            target = out / f"page-{n}.png"
            pix.save(str(target))
            emit({"type": "rendered", "page_no": n, "path": str(target)})
        except Exception as e:  # noqa: BLE001
            emit({"type": "rendered", "page_no": n, "error": type(e).__name__})
    emit({"type": "done"})


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("extract", "render"):
        p = sub.add_parser(name)
        p.add_argument("pdf")
        p.add_argument("--mem-mb", type=int, default=0)
        p.add_argument("--cpu-s", type=int, default=0)
    sub.choices["extract"].add_argument("--max-pages", type=int, required=True)
    sub.choices["extract"].add_argument("--max-chars", type=int, required=True)
    sub.choices["render"].add_argument("outdir")
    sub.choices["render"].add_argument("--pages", required=True)
    sub.choices["render"].add_argument("--dpi", type=int, required=True)
    sub.choices["render"].add_argument("--max-pixels", type=int, required=True)
    args = parser.parse_args()
    limit_resources(args.mem_mb, args.cpu_s)
    extract(args) if args.mode == "extract" else render(args)


if __name__ == "__main__":
    main()
