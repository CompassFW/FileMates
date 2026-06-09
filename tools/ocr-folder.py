#!/usr/bin/env python3
"""Bulk-OCR a folder of scanned PDFs: add a searchable text layer in place.

For people with lots of scans (e.g. meal/expense receipts) that have no text layer.
Only touches PDFs WITHOUT an existing text layer (pages that already have text are left
untouched by ocrmypdf --skip-text). Safe to re-run.

Usage:
    python3 tools/ocr-folder.py <folder> [--lang deu+eng] [--recursive] [--dry-run]

Requires: ocrmypdf + tesseract (see README -> OCR). Does nothing (with a clear message)
if they are not installed. Never deletes anything; only adds a text layer.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_VISION_LANG = {"deu": "de-DE", "eng": "en-US", "fra": "fr-FR", "spa": "es-ES",
                "ita": "it-IT", "nld": "nl-NL", "por": "pt-BR"}


def _vision_cmd(pdf: Path, lang: str):
    """Command to OCR in place via Apple Vision (macOS), or None if unavailable here."""
    if sys.platform != "darwin":
        return None
    here = Path(__file__).resolve().parent
    vlang = ",".join(_VISION_LANG.get(p, p) for p in re.split(r"[+,]", lang) if p)
    binp = here / "macos-ocr"
    if binp.exists() and os.access(binp, os.X_OK):
        return [str(binp), str(pdf), str(pdf), vlang]
    if (here / "macos-ocr.swift").exists() and shutil.which("xcrun"):
        return ["xcrun", "swift", str(here / "macos-ocr.swift"), str(pdf), str(pdf), vlang]
    return None


def ocr_in_place(pdf: Path, lang: str) -> bool:
    """OCR a PDF in place. Prefers Apple Vision (macOS), else ocrmypdf. Returns success."""
    cmd = _vision_cmd(pdf, lang)
    if cmd is None:
        if not shutil.which("ocrmypdf"):
            return False
        cmd = ["ocrmypdf", "--skip-text", "--optimize", "0", "-l", lang, str(pdf), str(pdf)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode != 0:
            print(f"  FAIL {pdf.name}: {r.stderr.decode('utf-8','replace')[:120]}")
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  FAIL {pdf.name}: {e}")
        return False


def _backend_available() -> bool:
    return _vision_cmd(Path("x.pdf"), "eng") is not None or shutil.which("ocrmypdf") is not None


def has_text_layer(pdf: Path) -> bool:
    """True if the PDF already has extractable text (so OCR can be skipped)."""
    try:
        r = subprocess.run(["pdftotext", "-q", "-l", "2", str(pdf), "-"],
                           capture_output=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            return True
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Add a searchable text layer to scanned PDFs in a folder.")
    ap.add_argument("folder", help="folder containing PDFs")
    ap.add_argument("--lang", default="deu+eng", help="OCR languages (default: deu+eng)")
    ap.add_argument("--recursive", action="store_true", help="also process sub-folders")
    ap.add_argument("--dry-run", action="store_true", help="list what would be OCR'd, change nothing")
    args = ap.parse_args()

    root = Path(args.folder).expanduser()
    if not root.is_dir():
        sys.exit(f"ERROR: not a folder: {root}")
    if not _backend_available() and not args.dry_run:
        sys.exit("ERROR: no OCR backend. On macOS this uses Apple Vision (needs Xcode CLT for "
                 "swift, or the prebuilt tools/macos-ocr); otherwise install 'ocrmypdf'. See README -> OCR.")

    pdfs = sorted((root.rglob("*.pdf") if args.recursive else root.glob("*.pdf")))
    done, skipped, failed = [], [], []
    for pdf in pdfs:
        if has_text_layer(pdf):
            skipped.append(pdf)
            continue
        if args.dry_run:
            done.append(pdf)
            continue
        (done if ocr_in_place(pdf, args.lang) else failed).append(pdf)

    verb = "would OCR" if args.dry_run else "OCR'd"
    print(f"\n{verb}: {len(done)} | already had text (skipped): {len(skipped)} | failed: {len(failed)}")
    for p in done:
        print(f"  {verb}: {p}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
