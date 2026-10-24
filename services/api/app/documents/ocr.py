"""OCR behind an interface. OCR runs only for pages that have no usable text layer (pipeline.py decides), because it
costs CPU time and can be less accurate than a real text layer. The default engine is the Tesseract command line;
tests use a fake engine."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.core.config import Settings
from app.documents.runner import run_child


class OcrError(Exception):
    pass


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None  # mean word confidence, 0-100, or None when the engine does not report one


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognize(self, image: Path) -> OcrResult: ...


class NullOcr:
    name = "none"

    def available(self) -> bool:
        return False

    def recognize(self, image: Path) -> OcrResult:
        raise OcrError("OCR is not enabled.")


def parse_tsv(tsv: str) -> OcrResult:
    """Tesseract's TSV output: one row per element; level 5 rows are words with a confidence (0-100).
    Words are rebuilt into lines, and paragraphs are separated by a blank line."""
    lines: list[tuple[tuple[str, str, str], list[str]]] = []
    confs: list[float] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        if not word:
            continue
        key = (cols[2], cols[3], cols[4])  # block, paragraph, line
        if not lines or lines[-1][0] != key:
            lines.append((key, []))
        lines[-1][1].append(word)
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if conf >= 0:
            confs.append(conf)
    out: list[str] = []
    previous: tuple[str, str] | None = None
    for (block, par, _line), words in lines:
        if previous is not None and previous != (block, par):
            out.append("")
        out.append(" ".join(words))
        previous = (block, par)
    return OcrResult("\n".join(out), round(sum(confs) / len(confs), 1) if confs else None)


class TesseractOcr:
    name = "tesseract"

    def __init__(self, cmd: str, languages: str, timeout_s: float) -> None:
        self.cmd, self.languages, self.timeout_s = cmd, languages, timeout_s
        self._checked: bool | None = None

    def available(self) -> bool:
        """The binary exists and every configured language pack is installed (checked once per engine instance)."""
        if self._checked is None:
            self._checked = self._probe()
        return self._checked

    def _probe(self) -> bool:
        if shutil.which(self.cmd) is None:
            return False
        res = run_child([self.cmd, "--list-langs"], timeout_s=15, max_output_bytes=65536)
        if res.crashed:
            return False
        installed = {ln.strip() for ln in res.stdout.decode("utf-8", "replace").splitlines()[1:]}
        return all(lang in installed for lang in self.languages.split("+"))

    def recognize(self, image: Path) -> OcrResult:
        res = run_child(
            [self.cmd, str(image), "stdout", "-l", self.languages, "tsv"],
            timeout_s=self.timeout_s,
            max_output_bytes=8 * 1024 * 1024,
            env_extra={"OMP_THREAD_LIMIT": "1"},
        )
        if res.timed_out:
            raise OcrError("OCR timed out on this page.")
        if res.crashed:
            raise OcrError("The OCR engine failed on this page.")
        return parse_tsv(res.stdout.decode("utf-8", "replace"))


def build_ocr(settings: Settings) -> OcrEngine:
    if not settings.feature_ocr:
        return NullOcr()
    return TesseractOcr(settings.tesseract_cmd, settings.ocr_languages, settings.ocr_timeout_per_page_s)
