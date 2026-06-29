#!/usr/bin/env python3
"""
fetch-attachments.py — provider-agnostic attachment downloader for the
filemates skill.

Downloads document attachments (PDF/DOCX/XLSX) from any IMAP mailbox, renames
them to your scheme, files them into a target folder, and verifies they landed.
Deletion is conservative: mail is only ever moved to Trash (reversible), only for
senders YOU put on the delete-after-filing list, and only AFTER the attachment is
verified on disk. The protected-sender list always wins; disable trashing with --no-trash.

Why this exists: most Gmail/mail MCP connectors expose no attachment-download
tool (they return attachment IDs, not bytes). IMAP is a standard every provider
speaks (Gmail, Outlook/Microsoft 365, GMX, iCloud, Fastmail, self-hosted), so
the same code works everywhere — only the config changes.

Standard library only. No pip install required.

Credentials: the password is read ONLY from the env var MAIL_APP_PASSWORD.
Never hard-code it, never pass it on the command line. For Gmail / Microsoft 365
use an *app password* (normal password login is usually disabled).

Usage examples:
  export MAIL_APP_PASSWORD='xxxx xxxx xxxx xxxx'
  python3 tools/fetch-attachments.py --from rechnungonline@telekom.de \
      --since 2025-01-01 --type invoices --dry-run
  python3 tools/fetch-attachments.py --from noreply-mobility@enbw.com \
      --folder "~/Documents/Belege 2026/01_Januar"
  python3 tools/fetch-attachments.py --message-id '<abc@mail>' --type receipts

Config is read from skills/filemates/config.local.md (see config.example.md).
"""

from __future__ import annotations

import argparse
import email
import hashlib
import http.server
import imaplib
import json
import os
import re
import secrets
import io
import shutil
import signal
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

ALLOWED_EXT = {".pdf", ".docx", ".xlsx"}
# Encryption/password-protected PDFs are routed here (a sub-folder of the target) instead of
# being filed normally: their date can't be read and — importantly — opening them can break a
# downstream reader. Override via config 'protected_pdf_folder' or --protected-folder.
DEFAULT_PROTECTED_FOLDER = "_Passwortgeschuetzt"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

__version__ = "0.3.2"  # x-release-please-version

TOOLS_DIR = Path(__file__).resolve().parent


def _skill_dir() -> Path:
    """Find the filemates skill dir in both layouts: the cloned repo
    (repo/tools + repo/skills/filemates) and an installed copy
    (~/.claude/skills/filemates/tools)."""
    candidates = [
        TOOLS_DIR.parent / "skills" / "filemates",   # repo layout
        TOOLS_DIR.parent,                                # tools/ inside the skill dir
        TOOLS_DIR,
    ]
    for c in candidates:
        if (c / "config.local.md").exists() or (c / "config.example.md").exists():
            return c
    return candidates[0]


SKILL_DIR = _skill_dir()
DEFAULT_CONFIG = SKILL_DIR / "config.local.md"
DEFAULT_DELETE_RULES = SKILL_DIR / "reference" / "delete-rules.local.md"
DOWNLOAD_FALLBACK = TOOLS_DIR.parent / "downloads"

# OAuth (XOAUTH2) — for Google Workspace / accounts where app passwords are blocked.
# A Google Cloud "project" is just a free registration that yields a client id/secret;
# it stores none of your mail. Auth runs locally; tokens are saved git-ignored on disk.
OAUTH_TOKEN_FILE = TOOLS_DIR / "oauth-token.local.json"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_MAIL_SCOPE = "https://mail.google.com/"


# --------------------------------------------------------------------------- #
# Config parsing (lenient: matches the simple markdown config.example.md)
# --------------------------------------------------------------------------- #
def _strip(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value)          # drop inline comments
    return value.strip().strip("`").strip()


def parse_config(path: Path) -> dict:
    """Read `key: value` lines and a 'type -> folder' markdown table."""
    cfg: dict = {"folder_map": {}}
    if not path.exists():
        return cfg
    for line in path.read_text(encoding="utf-8").splitlines():
        # Strip inline HTML comments BEFORE the table-row check, so a legit
        # `key: value <!-- a | b | c -->` line is not misread as a markdown
        # table row just because its comment contains a pipe.
        uncommented = re.sub(r"<!--.*?-->", "", line)
        # key: value   (also matches "- `imap_host:` value")
        m = re.match(r"\s*-?\s*`?([A-Za-z_]+)`?\s*:\s*`?(.+)", line)
        if m and "|" not in uncommented:
            cfg[m.group(1).lower()] = _strip(m.group(2))
            continue
        # markdown table row:  | tax documents | ~/Documents/.../Tax/ |
        if uncommented.count("|") >= 2 and "---" not in uncommented:
            cells = [c.strip() for c in uncommented.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[1].startswith(("~", "/", ".")):
                cfg["folder_map"][cells[0].lower()] = cells[1]
    return cfg


def _is_section_heading(line: str) -> bool:
    """A delete-rules section boundary is a Markdown ATX heading ONLY — a line whose
    first non-space character is '#' (any level: #, ##, ###). Everything else is
    section *content*: bold sub-labels like '**Senders (never delete):**', bullets,
    prose, blockquotes and comments. This is the load-bearing rule that stops a line
    which merely *mentions* a section marker (e.g. a junk-sender comment reading
    '...das ist delete-after-filing oben', or 'Unknown senders' prose that names the
    protected list) from flipping the parser into the wrong section and pulling the
    addresses beneath it into the live delete-after / protected set."""
    return line.lstrip().startswith("#")


def _senders_in_section(start_markers: tuple, allow_example_fallback: bool = True) -> set[str]:
    """Collect e-mail addresses listed under a delete-rules section whose HEADING
    contains one of `start_markers`; the section runs until the next heading. Only an
    ATX ('#') heading ever starts or ends a section — never a comment, sub-label or
    prose line that happens to contain a marker word (see `_is_section_heading`). Both
    shipped layouts (local + example) introduce every section with a '#' heading and use
    bold lines only as in-section sub-labels, so heading-only boundaries lose nothing.
    Setext headings (an `===`/`---` underline instead of a leading '#') are intentionally
    NOT treated as boundaries; neither shipped file uses them, and an unrecognised
    boundary fails safe — the parser stays in its current section rather than spuriously
    opening a delete-after section.

    `allow_example_fallback`: if the local file is missing/empty, also read the shipped
    delete-rules.example.md. Safe for the PROTECTED list (extra protection can't hurt),
    but MUST be False for the delete-after list — otherwise the example's placeholder
    addresses would become live deletion rules on a fresh install."""
    paths = [DEFAULT_DELETE_RULES]
    if allow_example_fallback:
        paths.append(DEFAULT_DELETE_RULES.with_name("delete-rules.example.md"))
    senders: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        in_section = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if _is_section_heading(line):
                in_section = any(m in line.lower() for m in start_markers)
                continue
            if in_section:
                low = line.lower()
                if "[at]" in low or "(example" in low:   # skip placeholder/example lines
                    continue
                for addr in re.findall(r"[\w.+-]+@[\w.-]+", line):
                    a = addr.lower()
                    if a.endswith("example.com") or a.endswith("example.org"):
                        continue                          # never treat doc placeholders as real
                    senders.add(a)
        if senders:
            break
    return senders


def load_delete_after_senders() -> set[str]:
    """Senders the delete-rules mark as 'delete-after-filing'. NEVER falls back to the
    example file — deletion rules must come only from the user's own local file."""
    return _senders_in_section(("delete-after-filing",), allow_example_fallback=False)


def load_protected_senders() -> set[str]:
    """Senders that must NEVER be deleted (the PROTECTED / 'never deleted' list).
    Example fallback is allowed here because it only ever ADDS protection (fails safe)."""
    return _senders_in_section(("protected", "never deleted", "nie löschen", "nie loeschen"),
                               allow_example_fallback=True)


# --------------------------------------------------------------------------- #
# Deletion policy (PURE — no I/O, unit-testable). The IMAP execution lives in
# main(); this only decides protect/trash/keep for one sender.
# --------------------------------------------------------------------------- #
def parse_sender_addr(from_header: str) -> str:
    """Normalized sender email (lowercased) via email.utils.parseaddr — robust to
    'Name <addr>' and odd headers. Returns '' when no real address is present."""
    addr = parseaddr(from_header or "")[1].strip().lower()
    return addr if "@" in addr else ""


def _domain(addr: str) -> str:
    return addr.rsplit("@", 1)[1] if "@" in addr else ""


def is_protected(addr: str, protected: set[str]) -> bool:
    """Protection check. Exact-address match, PLUS a domain-level safety net: an
    address whose domain equals — or is a sub-domain of — a protected entry's
    domain is also protected (covers a tax advisor who also writes from an
    alias/sub-domain). Over-protection fails safe: it can only ever PREVENT a
    deletion, never cause one."""
    if not addr:
        return False
    if addr in protected:
        return True
    dom = _domain(addr)
    if not dom:
        return False
    for p in protected:
        pdom = _domain(p)
        if pdom and (dom == pdom or dom.endswith("." + pdom)):
            return True
    return False


def decide_trash(from_header: str, protected: set[str], delete_after: set[str]) -> str:
    """Pure deletion-policy decision for one mail. Returns:
      'protect' — sender is on the protected list (NEVER delete; protection always wins)
      'trash'   — sender is on the user's delete-after-filing list (and not protected)
      'keep'    — anything else (the safe default; unknown senders are never auto-deleted)
    No filing/verification state is consulted here — the caller must additionally
    require verified filing before acting on a 'trash' decision."""
    addr = parse_sender_addr(from_header)
    if not addr:
        return "keep"
    if is_protected(addr, protected):
        return "protect"
    if addr in delete_after:
        return "trash"
    return "keep"


def trash_eligible(msg_filed: bool, trash_enabled: bool, decision: str) -> bool:
    """The hard safety gate, as a pure function so it is unit-testable and shared with
    main(). A mail may be trashed ONLY when its attachment was verified-filed
    (msg_filed), trashing is enabled (not --no-trash), AND the policy says 'trash'.
    A filing failure (msg_filed=False) therefore ALWAYS spares the mail."""
    return bool(msg_filed) and bool(trash_enabled) and decision == "trash"


def plan_expunge(delete_mode: str, caps, has_deleted_uids: bool) -> str:
    """Decide HOW (if at all) to expunge after the loop — pure + testable. Returns:
      'uid-scoped' — UID EXPUNGE limited to exactly this run's UIDs (needs UIDPLUS)
      'full'       — unscoped EXPUNGE (removes ANY \\Deleted mail) — ONLY for the
                     user-forced 'expunge' mode without UIDPLUS
      'skip'       — do NOT expunge (copy-trash without UIDPLUS: copies are safe in
                     Trash; never risk an unscoped expunge of unrelated \\Deleted mail)
      'none'       — nothing flagged / mode needs no expunge
    """
    if not has_deleted_uids or delete_mode not in ("copy-trash", "expunge"):
        return "none"
    if "UIDPLUS" in (caps or ()):
        return "uid-scoped"
    return "full" if delete_mode == "expunge" else "skip"


def execute_expunge(imap: imaplib.IMAP4_SSL, plan: str, deleted_uids):
    """Run the expunge decided by plan_expunge — extracted from main() so the EXECUTION
    (not just the decision) is unit-testable. Returns (ok, note). It issues a UID-scoped
    EXPUNGE for 'uid-scoped', an unscoped EXPUNGE for 'full' (forced permanent only), and
    issues NOTHING for 'skip'/'none'. The unscoped imap.expunge() is unreachable here unless
    plan == 'full' — i.e. the user passed --force-expunge."""
    if plan == "none":
        return True, ""
    if plan == "skip":
        return True, ("server lacks UIDPLUS — source messages left flagged (their copies are "
                      "already in Trash); skipping unscoped EXPUNGE for safety")
    if plan == "uid-scoped":
        typ, _ = imap.uid("EXPUNGE", ",".join(deleted_uids))
        return typ == "OK", f"uid-scoped EXPUNGE of {len(deleted_uids)} message(s)"
    if plan == "full":
        typ, _ = imap.expunge()
        return typ == "OK", ("unscoped EXPUNGE (--force-expunge): removes ALL \\Deleted-flagged "
                             "mail in this mailbox, not only this run's")
    return True, ""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def decoded(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def imap_date(yyyy_mm_dd: str) -> str:
    d = datetime.strptime(yyyy_mm_dd, "%Y-%m-%d")
    return f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}"


def sender_short(from_header: str) -> str:
    m = re.search(r"[\w.+-]+@([\w.-]+)", from_header or "")
    if not m:
        return "mail"
    domain = m.group(1).split(".")[-2] if "." in m.group(1) else m.group(1)
    return re.sub(r"[^A-Za-z0-9]", "", domain) or "mail"


DEFAULT_NAMING = "<prefix>_<type>_<sender>_<date>"


def render_name(scheme: str, prefix: str, kind: str, sender: str,
                when: datetime, original_stem: str, ext: str) -> str:
    """Render a user-configurable naming scheme. Placeholders:
    <prefix> <type> <sender> <original>, plus dates:
    <date> / <YYYY-MM-DD> (ISO) and <DD-MM-YYYY> / <TT-MM-JJJJ> (day-first/German).
    The result is sanitised to safe chars (no path separators) — path escape stays impossible."""
    date = when.strftime("%Y-%m-%d") if when else "undated"
    date_de = when.strftime("%d-%m-%Y") if when else "undated"   # German day-first (TT-MM-JJJJ)
    name = scheme or DEFAULT_NAMING
    repl = {
        "<prefix>": prefix, "<type>": kind, "<sender>": sender,
        "<date>": date, "<YYYY-MM-DD>": date,
        "<DD-MM-YYYY>": date_de, "<TT-MM-JJJJ>": date_de,
        "<original>": original_stem,
    }
    for ph, val in repl.items():
        name = name.replace(ph, val or "")
    name = re.sub(r"<[A-Za-z0-9_-]+>", "", name)          # drop any unknown placeholders
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-") or "doc"
    return name + ext


def _file_md5(p: Path) -> str:
    """md5 of a file's bytes, streamed (for byte-level de-dup)."""
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# OCR-stable de-dup: OCR rewrites a filed PDF *in place*, so its on-disk bytes
# no longer match the original e-mail payload. Without a stable fingerprint the
# next run would miss the de-dup and pile up _vN copies (and re-trash). We record
# the PRE-OCR payload md5 in a tiny sidecar next to the filed PDF and de-dup
# against THAT, so OCR'd files stay idempotent across re-runs.
# --------------------------------------------------------------------------- #
def _payload_md5_sidecar(p: Path) -> Path:
    """Path of the hidden sidecar that stores a filed PDF's original payload md5."""
    return p.with_name(f".{p.name}.payload-md5")


def write_payload_md5(p: Path, payload_md5: str) -> None:
    """Persist the original e-mail-payload md5 of a filed file (best-effort)."""
    try:
        _payload_md5_sidecar(p).write_text(payload_md5, encoding="ascii")
    except OSError:
        pass            # sidecar is an optimisation; never block filing on it


def read_payload_md5(p: Path) -> str | None:
    """Read back the original payload md5 of a filed file, or None if absent."""
    sidecar = _payload_md5_sidecar(p)
    try:
        return sidecar.read_text(encoding="ascii").strip() or None
    except (OSError, ValueError):
        return None


def matches_payload(existing: Path, payload_md5: str) -> bool:
    """True if `existing` is the SAME attachment as `payload_md5` — either its
    current bytes match (not yet OCR'd) OR its recorded pre-OCR payload md5 matches
    (OCR rewrote it in place, so the on-disk bytes diverged but identity is stable)."""
    if not existing.exists():
        return False
    if read_payload_md5(existing) == payload_md5:
        return True
    return _file_md5(existing) == payload_md5


def find_filed_by_payload(folder: Path, payload_md5: str) -> Path | None:
    """Any document already filed in `folder` whose recorded payload fingerprint matches,
    REGARDLESS of its filename. This is the name-independent de-dup: a re-processed
    attachment whose chosen name drifted between runs (e.g. a payroll list labelled by
    different providers) is still recognised as the same document and not re-filed.

    Reads only the tiny `.<name>.payload-md5` sidecars that every tool-filed document
    carries (cheap) — never hashes whole files here. Legacy files without a sidecar are
    handled by the name-based fallback at the call site."""
    try:
        sidecars = list(folder.glob(".*.payload-md5"))
    except OSError:
        return None
    suffix = ".payload-md5"
    for sc in sidecars:
        try:
            if sc.read_text(encoding="ascii").strip() != payload_md5:
                continue
        except OSError:
            continue
        filed = sc.with_name(sc.name[1:-len(suffix)])   # ".<name>.payload-md5" -> "<name>"
        if filed.exists():
            return filed
    return None


# --------------------------------------------------------------------------- #
# Optional feature: name files by a date read FROM the PDF (e.g. the payment /
# charge date), not the mail date. Configurable via `date_source`. Text layer
# extraction degrades gracefully: poppler `pdftotext` -> `pypdf` -> none.
# --------------------------------------------------------------------------- #
DE_MONTHS = {"januar": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
             "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
             "oktober": 10, "november": 11, "dezember": 12}
EN_MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
             "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
             "november": 11, "december": 12}

# Default keyword sets per date_source (German + English). User-overridable in
# config via `date_keywords_<source>:` (semicolon-separated). Order matters:
# the first keyword that yields a date wins, so put generic terms ("datum") last.
DEFAULT_DATE_KEYWORDS = {
    "abbuchung": ["buchen wir am", "eingezogen am", "abgebucht am", "abbuchung am",
                  "lastschrift am", "date paid", "bezahlt am", "gezahlt am",
                  "zahlung am", "zahlungseingang", "fällig am", "faellig am",
                  "fälligkeitstag", "faelligkeitstag", "fälligkeitsdatum", "faelligkeitsdatum"],
    "rechnung":  ["rechnungsdatum", "date of issue", "rechnung vom", "invoice date",
                  "belegdatum", "bestelldatum"],
    "leistung":  ["leistungszeitraum", "leistungsdatum", "lieferdatum", "leistung vom"],
}

_RX_DMY = re.compile(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b")
_RX_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RX_DE_TXT = re.compile(r"\b(\d{1,2})\.?\s+([A-Za-zäöüÄÖÜ]+)\s+(\d{4})\b")
_RX_EN_TXT = re.compile(r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b")


class _ExtractTimeout(Exception):
    pass


def _pypdf_text(payload: bytes, seconds: int = 20) -> str:
    """Extract text via pypdf from memory, bounded by SIGALRM (untrusted PDF DoS guard).
    Falls back to no-timeout if SIGALRM is unavailable (e.g. not main thread)."""
    import pypdf  # optional dependency: pip install 'pypdf>=4.0'

    def _read() -> str:
        reader = pypdf.PdfReader(io.BytesIO(payload))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)

    def _on_alarm(signum, frame):
        raise _ExtractTimeout()

    try:
        old = signal.signal(signal.SIGALRM, _on_alarm)
    except (ValueError, AttributeError):
        return _read()                                   # no signals here → unbounded best-effort
    signal.alarm(seconds)
    try:
        return _read()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def extract_pdf_text(payload: bytes) -> str:
    """Best-effort text layer, NO temp file (payload never touches disk):
    poppler `pdftotext` via stdin, then in-memory `pypdf`, then ''. Never raises."""
    try:
        r = subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", "-layout", "-", "-"],
                           input=payload, capture_output=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode("utf-8", "replace")
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        pass
    try:
        return _pypdf_text(payload)
    except Exception:
        return ""


def pdf_is_encrypted(payload: bytes) -> bool:
    """True if the PDF is password/encryption protected (can't be safely read or date-parsed,
    and opening it may corrupt a downstream reader). Uses pypdf when available; otherwise a
    best-effort raw scan for an /Encrypt trailer reference. Never raises."""
    try:
        import pypdf
        return bool(pypdf.PdfReader(io.BytesIO(payload)).is_encrypted)
    except ImportError:
        return b"/Encrypt" in payload          # heuristic fallback when pypdf is absent
    except Exception:
        # A reader that blows up on this file is itself a red flag → quarantine it.
        return b"/Encrypt" in payload


_VISION_LANG = {"deu": "de-DE", "eng": "en-US", "fra": "fr-FR", "spa": "es-ES",
                "ita": "it-IT", "nld": "nl-NL", "por": "pt-BR"}


def _to_vision_langs(lang: str) -> str:
    return ",".join(_VISION_LANG.get(p, p) for p in re.split(r"[+,]", lang) if p)


def _macos_vision_ocr(path: Path, lang: str):
    """OCR in place via Apple's built-in Vision (macOS). Returns (ok, note) or None if
    the macOS backend isn't available here (then the caller falls back to ocrmypdf)."""
    if sys.platform != "darwin":
        return None
    here = Path(__file__).resolve().parent
    binp = here / "macos-ocr"
    vlang = _to_vision_langs(lang)
    if binp.exists() and os.access(binp, os.X_OK):
        cmd = [str(binp), str(path), str(path), vlang]
    elif (here / "macos-ocr.swift").exists() and shutil.which("xcrun"):
        cmd = ["xcrun", "swift", str(here / "macos-ocr.swift"), str(path), str(path), vlang]
    else:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode == 0:
            return True, "ok-vision"
        return False, "vision rc=%d: %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[:140])
    except (OSError, subprocess.SubprocessError) as e:
        return False, "vision error: %s" % e


def ocr_pdf(path: Path, lang: str = "deu+eng") -> tuple[bool, str]:
    """Add a searchable text layer to a (scanned) PDF *in place*. Prefers Apple Vision on
    macOS (no extra deps), else ocrmypdf+tesseract. No-op-safe: returns (ok, note), never raises.
    Pages that already have text are left untouched, so it's safe on mixed/already-text PDFs."""
    res = _macos_vision_ocr(path, lang)
    if res is not None:
        return res
    exe = shutil.which("ocrmypdf")
    if not exe:
        return False, "ocrmypdf-not-installed"
    try:
        r = subprocess.run([exe, "--skip-text", "--optimize", "0", "-l", lang,
                            str(path), str(path)],
                           capture_output=True, timeout=900)
        if r.returncode == 0:
            return True, "ok"
        return False, "rc=%d: %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[:140])
    except (OSError, subprocess.SubprocessError) as e:
        return False, "error: %s" % e


def _dates_in(span: str):
    """All parseable dates in a text span, in order of appearance."""
    found = []
    for m in _RX_DMY.finditer(span):
        d, mo, y = map(int, m.groups())
        try:
            found.append((m.start(), datetime(y, mo, d)))
        except ValueError:
            pass
    for m in _RX_ISO.finditer(span):
        y, mo, d = map(int, m.groups())
        try:
            found.append((m.start(), datetime(y, mo, d)))
        except ValueError:
            pass
    for m in _RX_DE_TXT.finditer(span):
        if m.group(2).lower() in DE_MONTHS:
            try:
                found.append((m.start(), datetime(int(m.group(3)),
                              DE_MONTHS[m.group(2).lower()], int(m.group(1)))))
            except ValueError:
                pass
    for m in _RX_EN_TXT.finditer(span):
        if m.group(1).lower() in EN_MONTHS:
            try:
                found.append((m.start(), datetime(int(m.group(3)),
                              EN_MONTHS[m.group(1).lower()], int(m.group(2)))))
            except ValueError:
                pass
    found.sort(key=lambda t: t[0])
    return [d for _, d in found]


def parse_doc_date(text: str, source: str, keywords: dict):
    """Find the date for `source` near its keywords. Word-boundary keyword match
    (so 'leistung vom' won't fire inside 'Gesamtleistung vom'); the date is looked
    for on the keyword's own line first, then the next line (table layouts) — not in
    a blind 60-char window that could grab an unrelated later date. For 'leistung'
    (a period 'X - Y') the later date wins. Returns (datetime, keyword) or (None, None)."""
    if not text:
        return None, None
    for kw in keywords.get(source, []):
        for m in re.finditer(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
            tail = text[m.end():]
            lines = tail.split("\n")
            segments = [lines[0][:80]]                     # rest of the keyword's line
            if len(lines) > 1:
                segments.append(lines[1][:80])             # plus the next line (label-above-value)
            for seg in segments:
                dates = _dates_in(seg)
                if dates:
                    return (dates[-1] if source == "leistung" else dates[0]), kw
    return None, None


def resolve_date_keywords(cfg: dict) -> dict:
    """Defaults, with optional per-source overrides from config (`date_keywords_<source>:`)."""
    kw = {k: list(v) for k, v in DEFAULT_DATE_KEYWORDS.items()}
    for source in kw:
        raw = cfg.get(f"date_keywords_{source}")
        if raw:
            items = [s.strip().lower() for s in re.split(r"[;,]", raw) if s.strip()]
            if items:
                kw[source] = items
    return kw


def resolve_folder(args, cfg) -> Path:
    if args.folder:
        return Path(os.path.expanduser(args.folder))
    fmap = cfg.get("folder_map", {})
    if args.type:
        t = args.type.lower()
        if t in fmap:                                   # exact key wins
            return Path(os.path.expanduser(fmap[t]))
        for key, folder in fmap.items():                # then word-boundary match
            if re.search(rf"\b{re.escape(t)}\b", key):
                return Path(os.path.expanduser(folder))
    return DOWNLOAD_FALLBACK


# Dated folder placeholders — month-subfolder filing conventions like
# `Belege <YYYY>/<MM_Monat>/` (e.g. `Belege 2026/06_Juni/`). Resolved PER ATTACHMENT
# from the SAME date the filename uses (payment date when date_source=abbuchung, else
# the mail date), so file and folder always agree on the month.
GERMAN_MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
                 "August", "September", "Oktober", "November", "Dezember"]
_DATED_PLACEHOLDERS = ("<YYYY>", "<MM>", "<MM_Monat>")


def has_dated_placeholder(folder) -> bool:
    """True if the folder template contains a date placeholder (resolved per attachment)."""
    s = str(folder)
    return any(ph in s for ph in _DATED_PLACEHOLDERS)


def unknown_placeholders(folder) -> list:
    """Angle-bracket tokens in a folder path that are NOT a known date placeholder —
    a typo like `<JJJJ>` must error loudly instead of becoming a literal junk folder
    (with --mkdir it would otherwise be CREATED verbatim — the bogus-folder class)."""
    s = str(folder)
    for ph in _DATED_PLACEHOLDERS:
        s = s.replace(ph, "")
    return re.findall(r"<[A-Za-z0-9_-]+>", s)


def resolve_dated_folder(folder, when):
    """Resolve <YYYY>/<MM>/<MM_Monat> in a folder path from `when`. A template WITHOUT
    placeholders passes through unchanged. A template WITH placeholders but NO date
    returns None — the caller must keep the mail and report, never guess a month."""
    s = str(folder)
    if not has_dated_placeholder(s):
        return Path(s)
    if when is None:
        return None
    s = s.replace("<YYYY>", f"{when.year:04d}")
    s = s.replace("<MM_Monat>", f"{when.month:02d}_{GERMAN_MONTHS[when.month - 1]}")
    s = s.replace("<MM>", f"{when.month:02d}")
    return Path(s)


def _q(value: str) -> str:
    """IMAP-quote a search value (imaplib does not quote multi-word strings itself).
    Escape backslashes first, then strip embedded double-quotes, so neither a quote nor a
    trailing backslash can break out of the quoted string (mirrors reminder-helper._as_quote)."""
    return '"' + value.replace("\\", "\\\\").replace('"', "") + '"'


def build_search(args) -> list[str]:
    crit: list[str] = []
    if args.message_id:
        crit += ["HEADER", "Message-ID", _q(args.message_id)]
    if args.sender:
        crit += ["FROM", _q(args.sender)]
    if args.subject:
        crit += ["SUBJECT", _q(args.subject)]
    if args.since:
        crit += ["SINCE", imap_date(args.since)]
    if args.before:
        crit += ["BEFORE", imap_date(args.before)]
    return crit or ["ALL"]


# --------------------------------------------------------------------------- #
# OAuth (XOAUTH2) — used when app passwords are unavailable (e.g. Workspace)
# --------------------------------------------------------------------------- #
def _post_token(params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:               # Google returns a JSON error body
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


def oauth_setup(cfg: dict) -> int:
    """One-time loopback OAuth flow → saves a refresh token locally. Pure stdlib."""
    client_id = cfg.get("oauth_client_id")
    client_secret = cfg.get("oauth_client_secret")
    if not client_id or not client_secret:
        sys.exit("ERROR: oauth_client_id / oauth_client_secret missing in config "
                 "(create a free Google Cloud OAuth 'Desktop app' client — see README).")

    captured: dict = {}
    state = secrets.token_urlsafe(24)                  # CSRF protection for the loopback

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.urlparse(self.path).query
            captured.update(urllib.parse.parse_qs(q))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h2>Done - you can close this tab and return to the terminal.</h2>")

        def log_message(self, *a):  # silence
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}"
    auth_url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_MAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    print("Opening your browser for Google consent…\nIf it doesn't open, paste this URL:\n" + auth_url + "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    server.handle_request()   # blocks until Google redirects once
    server.server_close()

    if (captured.get("state") or [None])[0] != state:  # reject forged/foreign callbacks
        sys.exit("ERROR: OAuth state mismatch — aborting (possible CSRF / stray request).")
    code = (captured.get("code") or [None])[0]
    if not code:
        sys.exit(f"ERROR: no authorization code received ({captured}).")
    tok = _post_token({
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    if "refresh_token" not in tok:
        sys.exit("ERROR: no refresh_token returned. Revoke prior access at "
                 "myaccount.google.com/permissions and rerun --auth-setup.")
    OAUTH_TOKEN_FILE.write_text(json.dumps({"refresh_token": tok["refresh_token"]}, indent=2))
    try:
        os.chmod(OAUTH_TOKEN_FILE, 0o600)
    except OSError:
        pass
    print(f"\n✓ Saved refresh token to {OAUTH_TOKEN_FILE} (git-ignored).")
    print("You can now run the script normally — it authenticates via OAuth.")
    return 0


def oauth_access_token(cfg: dict) -> str:
    if not OAUTH_TOKEN_FILE.exists():
        raise RuntimeError("no OAuth token — run with --auth-setup first.")
    try:
        refresh = json.loads(OAUTH_TOKEN_FILE.read_text())["refresh_token"]
    except (json.JSONDecodeError, KeyError, OSError):
        raise RuntimeError(f"OAuth token file unreadable ({OAUTH_TOKEN_FILE}); "
                           "rerun with --auth-setup.")
    tok = _post_token({
        "client_id": cfg.get("oauth_client_id"),
        "client_secret": cfg.get("oauth_client_secret"),
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if "access_token" not in tok:
        raise RuntimeError(f"token refresh failed ({tok.get('error', tok)}); "
                           "the refresh token may be revoked — rerun --auth-setup.")
    return tok["access_token"]


def imap_login(imap: imaplib.IMAP4_SSL, cfg: dict, user: str) -> str:
    """Pick auth automatically. Returns the method used ('app-password'|'XOAUTH2')."""
    password = os.environ.get("MAIL_APP_PASSWORD")
    if password:
        imap.login(user, password)
        return "app-password"
    if OAUTH_TOKEN_FILE.exists():
        token = oauth_access_token(cfg)
        auth = f"user={user}\x01auth=Bearer {token}\x01\x01".encode()
        imap.authenticate("XOAUTH2", lambda _=None: auth)
        return "XOAUTH2"
    sys.exit("ERROR: no credentials. Either set MAIL_APP_PASSWORD (app password), "
             "or run `--auth-setup` once for OAuth (Workspace/business accounts).")


def find_gmail_all_mail(imap: imaplib.IMAP4_SSL):
    """Locale-independent: Gmail flags its All-Mail folder with \\All (e.g. German
    '[Gmail]/Alle Nachrichten')."""
    typ, boxes = imap.list()
    if typ != "OK" or not boxes:
        return None
    for b in boxes:
        line = b.decode(errors="replace") if isinstance(b, bytes) else str(b)
        if "\\All" in line:
            m = re.search(r'"([^"]+)"\s*$', line) or re.search(r'([^ ]+)\s*$', line)
            if m:
                return m.group(1)
    return None


def select_mailbox(imap: imaplib.IMAP4_SSL, mailbox: str, host: str, readonly: bool) -> str:
    """Select mailbox; for Gmail, fall back to the \\All folder if the given name fails."""
    typ, _ = imap.select(f'"{mailbox}"', readonly=readonly)
    if typ == "OK":
        return mailbox
    if "gmail" in (host or "").lower():
        allm = find_gmail_all_mail(imap)
        if allm:
            typ, _ = imap.select(f'"{allm}"', readonly=readonly)
            if typ == "OK":
                return allm
    raise RuntimeError(f"could not select mailbox {mailbox!r} (server said {typ}).")


# --------------------------------------------------------------------------- #
# Server deletion-capability probe — run BEFORE any deletion so we NEVER delete
# irreversibly by accident. The rule: only ever move mail into a recoverable
# Trash; a permanent EXPUNGE requires an explicit --force-expunge opt-in.
# --------------------------------------------------------------------------- #
TRASH_NAMES = ("trash", "deleted messages", "deleted items", "papierkorb",
               "bin", "[gmail]/trash", "inbox.trash")


def find_trash_folder(imap: imaplib.IMAP4_SSL):
    """Return the server's recoverable Trash folder name, or None. Prefers the
    RFC 6154 \\Trash special-use flag; falls back to common (localized) names."""
    try:
        typ, lines = imap.list()
    except Exception:
        return None
    if typ != "OK" or not lines:
        return None
    fallback = None
    for raw in lines:
        s = raw.decode(errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        m = re.search(r'"([^"]*)"\s*$', s) or re.search(r"(\S+)\s*$", s)
        if not m:
            continue
        name = m.group(1).strip('"')
        if "\\trash" in s.lower():        # special-use flag is authoritative
            return name
        if fallback is None and name.lower() in TRASH_NAMES:
            fallback = name
    return fallback


def probe_delete_mode(imap: imaplib.IMAP4_SSL, host: str, force_expunge: bool = False):
    """Probe the server to pick a REVERSIBLE deletion mode. Returns
    (mode, detail, trash_folder):
      'gmail-trash' — add the \\Trash label (Gmail; 30-day recoverable, no expunge)
      'move-trash'  — UID MOVE into the Trash folder (reversible; needs MOVE cap)
      'copy-trash'  — COPY into Trash, then flag source \\Deleted + scoped expunge
                      (reversible: the copy lives on in Trash)
      'expunge'     — \\Deleted + EXPUNGE, PERMANENT — only when force_expunge=True
      'refuse'      — no recoverable path and not forced → DO NOT delete
    """
    if "gmail" in (host or "").lower():
        return ("gmail-trash", "Gmail label \\Trash (30-day recoverable)", None)
    caps = getattr(imap, "capabilities", ()) or ()
    trash = find_trash_folder(imap)
    if trash and "MOVE" in caps:
        return ("move-trash", f"UID MOVE -> {trash!r} (recoverable)", trash)
    if trash:
        return ("copy-trash", f"COPY -> {trash!r} + expunge source (recoverable)", trash)
    if force_expunge:
        return ("expunge", "PERMANENT \\Deleted + EXPUNGE (--force-expunge)", None)
    return ("refuse",
            "no recoverable Trash folder found — deletion DISABLED for safety "
            "(pass --force-expunge to permanently delete anyway)", None)


DELETE_MODES = ("gmail-trash", "move-trash", "copy-trash", "expunge", "refuse")


def resolve_delete_mode(imap: imaplib.IMAP4_SSL, host: str, cfg: dict, force_expunge: bool = False):
    """Pick the deletion mode WITHOUT a live probe when the user's provider was already
    detected once at setup and recorded in config (`delete_mode` / `trash_folder`) — the
    provider doesn't change between runs, so we skip the per-run server round-trip. Falls
    back to a live probe when no valid cached mode is present. Returns (mode, detail, trash)."""
    # Gmail is always the reversible label path — never honor a cached/edited mode that
    # would \Deleted+expunge a Gmail mailbox (e.g. a config copied from another account).
    if "gmail" in (host or "").lower():
        return ("gmail-trash", "Gmail label \\Trash (30-day recoverable)", None)
    cached = (cfg.get("delete_mode") or "").strip().lower()
    if cached in DELETE_MODES:
        trash = (cfg.get("trash_folder") or "").strip() or None
        if cached in ("move-trash", "copy-trash") and not trash:
            # cached mode needs a folder but none recorded → re-probe rather than guess
            return probe_delete_mode(imap, host, force_expunge)
        if cached == "expunge" and not force_expunge:
            return ("refuse",
                    "config delete_mode=expunge is permanent — refused without --force-expunge",
                    None)
        return (cached, f"from config (delete_mode={cached})", trash)
    return probe_delete_mode(imap, host, force_expunge)


def trash_one(imap: imaplib.IMAP4_SSL, num, mode: str, trash_folder) -> bool:
    """Execute the deletion for one message UID via the probed mode. Returns True
    on success. Never expunges here — expunge is batched + UID-scoped in main()."""
    if mode == "gmail-trash":
        typ, _ = imap.uid("STORE", num, "+X-GM-LABELS", "\\Trash")
        return typ == "OK"
    if mode == "move-trash":
        typ, _ = imap.uid("MOVE", num, _q(trash_folder or ""))
        return typ == "OK"
    if mode == "copy-trash":
        typ, _ = imap.uid("COPY", num, _q(trash_folder or ""))
        if typ != "OK":
            return False                                   # no source flagged if copy failed
        typ, _ = imap.uid("STORE", num, "+FLAGS", "\\Deleted")
        return typ == "OK"
    if mode == "expunge":
        typ, _ = imap.uid("STORE", num, "+FLAGS", "\\Deleted")
        return typ == "OK"
    return False


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    # allow_abbrev=False: without it, argparse would accept `--force` as an unambiguous
    # abbreviation of --force-expunge — silently bypassing any permission deny-pattern
    # that matches only the spelled-out flag. Destructive flags must be typed in full.
    ap = argparse.ArgumentParser(description="Download + file mail attachments via IMAP.",
                                 allow_abbrev=False)
    ap.add_argument("--version", action="version", version=f"FileMates fetch-attachments {__version__}")
    ap.add_argument("--from", dest="sender", help="filter by sender address")
    ap.add_argument("--subject", help="filter by subject text")
    ap.add_argument("--since", help="only mail on/after this date (YYYY-MM-DD)")
    ap.add_argument("--before", help="only mail before this date (YYYY-MM-DD)")
    ap.add_argument("--message-id", help="fetch one specific Message-ID")
    ap.add_argument("--type", help="document type -> folder via config folder map (e.g. invoices)")
    ap.add_argument("--folder", help="explicit target folder (overrides --type)")
    ap.add_argument("--mailbox", help="IMAP mailbox to search (overrides config)")
    ap.add_argument("--name", dest="name_scheme",
                    help="override naming_scheme for this run (e.g. '<original>' to keep filenames)")
    ap.add_argument("--name-sender", dest="name_sender",
                    help="override the <sender> placeholder for NAMING only — the real vendor "
                         "when the mail arrives via a payment processor (e.g. an ElevenLabs "
                         "invoice sent by stripe.com). Folder choice stays with --type.")
    ap.add_argument("--name-type", dest="name_type",
                    help="override the <type> placeholder for NAMING only — e.g. the German "
                         "document type 'Rechnung'/'Beleg' instead of the English folder key.")
    ap.add_argument("--keep-all", action="store_true",
                    help="file every attachment, ignoring the config 'ignore_attachments' boilerplate skip-list for this run")
    ap.add_argument("--attachment",
                    help="file ONLY attachments whose original filename contains this substring "
                         "(case-insensitive). Lets a multi-document mail (e.g. a tax advisor's mail "
                         "with an invoice + a UStVA + a list) be filed one document at a time, each "
                         "with its own --type/--name. A selective fetch never trashes the mail.")
    ap.add_argument("--date-source", choices=["mail", "abbuchung", "rechnung", "leistung"],
                    help="which date the <date>/<TT-MM-JJJJ> placeholder uses: read it FROM the PDF "
                         "(abbuchung=payment/charge, rechnung=invoice, leistung=service period) or the "
                         "mail date. Default comes from config 'date_source' (else 'mail').")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to config.local.md")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, write nothing")
    ap.add_argument("--protected-folder",
                    help="sub-folder name for encrypted/password-protected PDFs "
                         f"(default: config 'protected_pdf_folder' or '{DEFAULT_PROTECTED_FOLDER}')")
    ap.add_argument("--ocr", choices=["no", "auto", "yes"],
                    help="add a searchable text layer to scanned PDFs after filing "
                         "(macOS: Apple Vision; Linux: ocrmypdf). "
                         "auto=only PDFs without a text layer; yes=all PDFs; no=off (default config 'ocr' or no)")
    ap.add_argument("--ocr-lang", help="OCR languages, e.g. 'deu+eng' (auto-mapped to BCP-47 for Apple Vision; "
                         "default config 'ocr_lang' or deu+eng)")
    ap.add_argument("--mkdir", action="store_true",
                    help="allow creating the target folder if it does not exist (else: error)")
    ap.add_argument("--no-trash", action="store_true",
                    help="never delete mail (default: delete-after-filing senders are trashed once filed)")
    ap.add_argument("--trash-if-rule", action="store_true",
                    help="(deprecated, no-op: trashing delete-after-filing senders is now the default)")
    ap.add_argument("--force-expunge", action="store_true",
                    help="allow PERMANENT deletion when the server offers no recoverable Trash "
                         "(default: refuse to delete irreversibly). Use with care.")
    ap.add_argument("--detect-delete", action="store_true",
                    help="one-time: probe the server for the safe (reversible) deletion mode and "
                         "print config-ready 'delete_mode:'/'trash_folder:' lines to record in "
                         "config.local.md, so later runs skip the per-run probe.")
    ap.add_argument("--auth-setup", action="store_true",
                    help="one-time OAuth setup (for Workspace / when app passwords are blocked)")
    args = ap.parse_args()

    cfg = parse_config(Path(args.config))

    if args.auth_setup:                       # one-time OAuth; needs only client id/secret
        return oauth_setup(cfg)

    host = cfg.get("imap_host")
    user = cfg.get("imap_user") or cfg.get("account_email")
    try:
        port = int(cfg.get("imap_port", "993"))
    except (TypeError, ValueError):
        sys.exit(f"ERROR: imap_port must be a number (got {cfg.get('imap_port')!r}).")
    mailbox = args.mailbox or cfg.get("imap_mailbox") or "INBOX"
    prefix = cfg.get("file_prefix", "DOC")
    scheme = args.name_scheme or cfg.get("naming_scheme") or DEFAULT_NAMING
    date_source = (args.date_source or cfg.get("date_source") or "mail").lower()
    date_keywords = resolve_date_keywords(cfg)
    # Boilerplate skip-list: attachment filenames containing any of these (case-insensitive)
    # are not filed (e.g. standard privacy sheets, T&Cs). Empty = keep everything.
    ignore_patterns = [p.strip().lower() for p in re.split(r"[;,]", cfg.get("ignore_attachments", "") or "") if p.strip()]
    # Encrypted PDFs are quarantined into this sub-folder of the target (never read, never trashed).
    protected_folder = (args.protected_folder or cfg.get("protected_pdf_folder") or DEFAULT_PROTECTED_FOLDER).strip()
    # OCR: add a searchable text layer to scanned PDFs after filing (needs ocrmypdf+tesseract).
    ocr_mode = (args.ocr or cfg.get("ocr") or "no").lower()         # no | auto | yes
    ocr_lang = (args.ocr_lang or cfg.get("ocr_lang") or "deu+eng").strip()

    if not host or not user:
        sys.exit("ERROR: imap_host / imap_user missing in config. See config.example.md.")

    target = resolve_folder(args, cfg)
    delete_after = load_delete_after_senders()
    # Naming labels: the agent may supply the REAL vendor + document type (convention
    # `Anbieter_Typ_…`) when the From-domain is just a payment processor. NAMING only —
    # the folder still comes from --type; render_name sanitises both values.
    kind = args.name_type if args.name_type else (args.type or "doc").lower()
    will_trash = not args.no_trash and not args.dry_run   # auto-trash delete-after-filing senders

    # Guardrail (lesson from the bogus-folder incident): never silently create a new
    # target folder — a typo'd/garbled path must error, not spawn a junk directory.
    # A dated template (<YYYY>/<MM_Monat>) is checked PER ATTACHMENT instead (the
    # concrete month folder depends on each document's date).
    bad_ph = unknown_placeholders(target)
    if bad_ph:
        sys.exit(f"ERROR: unknown folder placeholder(s) {', '.join(bad_ph)} in: {target}\n"
                 f"       (known: <YYYY> <MM> <MM_Monat> — fix the typo; refusing so the "
                 f"literal text can never become a junk folder)")
    if not args.dry_run and not args.detect_delete and not has_dated_placeholder(target) \
            and not target.exists() and not args.mkdir:
        sys.exit(f"ERROR: target folder does not exist: {target}\n"
                 f"       (create it, fix --folder/--type, or pass --mkdir to create it)")

    if args.dry_run:
        print("DRY-RUN: nothing will be written or moved.")

    ctx = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL(host, port, ssl_context=ctx)
    try:
        method = imap_login(imap, cfg, user)
    except imaplib.IMAP4.error as e:
        sys.exit(f"ERROR: IMAP login failed: {e}")
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    # read-only unless we may move to Trash; resolve Gmail All-Mail locale automatically
    try:
        mailbox = select_mailbox(imap, mailbox, host, readonly=not will_trash)
    except RuntimeError as e:
        sys.exit(f"ERROR: {e}")
    print(f"IMAP {user}@{host}:{port} ({method})  mailbox={mailbox!r}  target={target}\n")

    # One-time provider/delete-rule detection: probe once, print config-ready lines, exit.
    # Recording these in config.local.md lets later runs skip the per-run probe (the user's
    # provider doesn't change between runs).
    if args.detect_delete:
        mode, detail, trash = probe_delete_mode(imap, host, args.force_expunge)
        imap.logout()
        print(f"Detected deletion mode: {mode} — {detail}\n")
        print("Add these lines to config.local.md (later runs then skip the probe):")
        print(f"- `delete_mode:` {mode}")
        print(f"- `trash_folder:` {trash or ''}")
        return 0

    # Decide the deletion mode. Prefer the value recorded once at setup (config
    # delete_mode/trash_folder); only probe live when it isn't recorded yet. We only ever
    # move mail to a recoverable Trash; permanent EXPUNGE needs --force-expunge.
    trash_enabled = not args.no_trash
    delete_mode, delete_detail, trash_folder = ("disabled", "--no-trash set", None)
    if trash_enabled:
        delete_mode, delete_detail, trash_folder = resolve_delete_mode(imap, host, cfg, args.force_expunge)
        print(f"Deletion: {delete_mode} — {delete_detail}\n")
    can_delete = trash_enabled and delete_mode not in ("refuse", "disabled")

    # UID-based throughout: sequence numbers shift on expunge; UIDs are stable.
    typ, data = imap.uid("SEARCH", *build_search(args))
    ids = data[0].split() if data and data[0] else []
    print(f"Matched {len(ids)} message(s).\n")

    protected = load_protected_senders()               # hard 'never delete' override

    def _resolve_target(when_dt):
        """Per-attachment target dir for (possibly dated) `target`. Returns (Path, None)
        or (None, reason) — unresolvable date / missing folder without --mkdir. The
        caller keeps the mail (msg_has_unfiled) so it is never trashed half-filed."""
        eff = resolve_dated_folder(target, when_dt)
        if eff is None:
            return None, "dated target folder needs a document date, none found"
        if not eff.exists() and not args.mkdir:
            return None, f"target folder does not exist: {eff} (use --mkdir)"
        return eff, None

    filed, skipped, problems, trashed, quarantined, ocred = [], [], [], [], [], []
    ocr_failed = []
    ocr_missing = False
    deleted_uids = []   # UIDs flagged \Deleted on non-Gmail servers (for scoped UID EXPUNGE)
    delete_skipped = []  # delete-after senders left in place because no reversible mode (NOT an error)

    for num in ids:
        typ, msg_data = imap.uid("FETCH", num, "(RFC822)")
        if typ != "OK" or not msg_data or not msg_data[0]:
            problems.append(f"fetch failed for uid {num!r}")
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        from_h = decoded(msg.get("From"))
        subj = decoded(msg.get("Subject"))
        try:
            when = parsedate_to_datetime(msg.get("Date"))
        except Exception:
            when = None

        msg_filed = False
        # A recognised document part (right extension) that we could NOT write to disk:
        # inline-disposition, unreadable/empty payload, encrypted-quarantine, path-escape,
        # or verify-fail. If ANY such part exists, the mail is the only copy of an unfiled
        # document and must NEVER be auto-trashed — even if a sibling attachment filed fine.
        # (Intentionally-dropped boilerplate via ignore_attachments does NOT count.)
        #
        # A SELECTIVE fetch (--attachment) is partial BY DESIGN — it files one document and
        # leaves the mail's other documents untouched — so the mail is never complete and must
        # always be kept. Seed the guard True up front so the "never trashes the mail" contract
        # holds even when the selected document is the only recognised part in the mail.
        msg_has_unfiled = bool(args.attachment)
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disp = (part.get("Content-Disposition") or "").lower()
            fname = decoded(part.get_filename())
            if not fname:
                continue
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ALLOWED_EXT:
                continue
            if "inline" in disp and "attachment" not in disp:
                skipped.append(f"inline: {fname} ({subj[:40]})")
                msg_has_unfiled = True   # never filed -> mail must not be trashed
                continue
            if ignore_patterns and not args.keep_all and \
                    any(p in fname.lower() for p in ignore_patterns):
                skipped.append(f"boilerplate (ignore_attachments): {fname}")
                continue
            if args.attachment and args.attachment.lower() not in fname.lower():
                # selective per-document fetch: this attachment is not the one requested.
                # (The keep-the-mail guarantee is enforced once, up front, by seeding
                # msg_has_unfiled from args.attachment — see the loop preamble — so a
                # selective run never auto-trashes the mail, single- or multi-document.)
                skipped.append(f"not selected (--attachment {args.attachment!r}): {fname}")
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                problems.append(f"empty payload: {fname}")
                msg_has_unfiled = True   # recognised doc we couldn't read -> keep the mail
                continue

            # Encrypted/password-protected PDF: quarantine into a sub-folder, keep the original
            # name (no date is readable), and do NOT mark the mail filed → it is never auto-trashed
            # (the mail may be the only way to recover the password). Crucially, it is never read.
            if ext == ".pdf" and pdf_is_encrypted(payload):
                # quarantined, never marked filed -> the mail (which may hold the only
                # password hint) must never be auto-trashed, even in a mixed mail.
                msg_has_unfiled = True
                # encrypted PDFs are never read, so a dated target resolves from the
                # MAIL date (the only date available without opening the document).
                eff_q, q_err = _resolve_target(when)
                if q_err:
                    problems.append(f"{q_err} (quarantine: {fname})")
                    continue
                qdir = eff_q / protected_folder
                # sanitise the raw attachment filename (mirror render_name) so a crafted
                # name like ../../x cannot escape the quarantine folder
                safe_q = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(fname)).strip("._-") or "doc.pdf"
                qout = qdir / safe_q
                if eff_q.resolve() not in qout.resolve().parents:
                    problems.append(f"path escape blocked (quarantine): {fname}")
                    continue
                if args.dry_run:
                    quarantined.append(f"would quarantine (encrypted): {fname}  ->  {qout}")
                    continue
                qdir.mkdir(parents=True, exist_ok=True)
                qstem, n = qout, 1
                while qstem.exists():
                    qstem = qout.with_name(f"{qout.stem}_v{n}{qout.suffix}")
                    n += 1
                qstem.write_bytes(payload)
                if qstem.exists() and qstem.stat().st_size > 0:
                    quarantined.append(f"quarantined (encrypted): {fname}  ->  {qstem}")
                else:
                    problems.append(f"verify failed (quarantine): {qstem}")
                continue

            # Date for the filename: mail date by default, or read FROM the PDF
            # (payment/invoice/service date) when date_source is set. Falls back
            # to the mail date — and says so — so nothing is ever silently wrong.
            doc_when, date_note, pdf_text = when, "mail-Datum", None
            if date_source != "mail" and ext == ".pdf":
                pdf_text = extract_pdf_text(payload)
                d, kw = parse_doc_date(pdf_text, date_source, date_keywords)
                if d:
                    doc_when, date_note = d, f"{date_source} ('{kw}' -> {d.strftime('%d-%m-%Y')})"
                elif pdf_text:
                    date_note = f"{date_source}->mail-Datum (kein Datum im PDF erkannt)"
                else:
                    date_note = f"{date_source}->mail-Datum (kein PDF-Text lesbar)"

            # dated targets resolve from the SAME date the filename carries (doc_when),
            # so a payment-dated file lands in the matching month folder.
            eff_target, t_err = _resolve_target(doc_when)
            if t_err:
                problems.append(f"{t_err}: {fname}")
                msg_has_unfiled = True   # nothing written -> keep the mail
                continue
            sender_label = args.name_sender or sender_short(from_h)
            out = eff_target / render_name(scheme, prefix, kind, sender_label,
                                           doc_when, os.path.splitext(fname)[0], ext)
            # defense-in-depth: the name is reconstructed, but never let it escape target
            if eff_target.resolve() not in out.resolve().parents:
                problems.append(f"path escape blocked: {out}")
                msg_has_unfiled = True   # blocked write -> keep the mail
                continue
            if args.dry_run:
                filed.append(f"would file: {fname}  ->  {out}   [Datum: {date_note}]")
                msg_filed = True
                continue

            eff_target.mkdir(parents=True, exist_ok=True)
            # byte-level de-dup: if this exact attachment is already filed (same name or a
            # previous _vN), skip re-writing — re-runs must not pile up duplicate receipts.
            payload_md5 = hashlib.md5(payload).hexdigest()
            # Name-INDEPENDENT first: identical bytes already filed in this folder under ANY
            # name (the chosen name may have drifted between runs) — recognised via the
            # payload sidecars, so a re-processed attachment never piles up a renamed duplicate.
            dup = find_filed_by_payload(eff_target, payload_md5)
            if dup is None:
                # Name-based fallback for legacy files without a sidecar (same name or a
                # previous _vN); also covers an OCR-rewritten-in-place file (see S2 / sidecar).
                existing = [out, *out.parent.glob(f"{out.stem}_v*{out.suffix}")]
                dup = next((e for e in existing if matches_payload(e, payload_md5)), None)
            if dup:
                filed.append(f"already filed (identical, skipped): {fname}  ->  {dup.name}")
                msg_filed = True
                continue
            stem, n = out, 1
            while stem.exists():                       # never overwrite (different content)
                stem = out.with_name(f"{out.stem}_v{n}{out.suffix}")
                n += 1
            stem.write_bytes(payload)
            if stem.exists() and stem.stat().st_size > 0:   # verify
                filed.append(f"filed: {fname}  ->  {stem}   [Datum: {date_note}]")
                msg_filed = True
                # Record the original payload md5 BEFORE any in-place OCR rewrite, so a
                # later re-run de-dups on a stable fingerprint instead of the OCR'd bytes.
                write_payload_md5(stem, payload_md5)
                # OCR: add a searchable text layer to scanned PDFs (after verified filing).
                if ocr_mode != "no" and ext == ".pdf":
                    if ocr_mode == "yes":
                        need_ocr = True
                    else:                                   # auto: only if no text layer
                        txt = pdf_text if pdf_text is not None else extract_pdf_text(payload)
                        need_ocr = not txt.strip()
                    if need_ocr:
                        ok, note = ocr_pdf(stem, ocr_lang)
                        if ok and stem.exists() and stem.stat().st_size > 0:
                            ocred.append(str(stem))
                        elif note == "ocrmypdf-not-installed":
                            ocr_missing = True
                        else:
                            # Soft failure: the file is filed & intact, just not searchable.
                            # Reported, but does NOT gate the exit code (see report below).
                            ocr_failed.append(f"{stem.name} ({note})")
            else:
                problems.append(f"verify failed: {stem}")
                msg_has_unfiled = True   # write didn't verify -> keep the mail

        # Auto-trash (default): filed AND decide_trash()=='trash'. Hard gates: verified
        # filing (msg_filed) + the pure policy (protection ALWAYS wins). The mechanism
        # is whatever probe_delete_mode picked and is reversible unless --force-expunge.
        _eligible = trash_eligible(msg_filed, trash_enabled, decide_trash(from_h, protected, delete_after))
        if _eligible and msg_has_unfiled:
            # Data-loss guard: a recognised document part of THIS mail was never written
            # to disk (inline / unreadable / quarantined / blocked). Keep the mail — it is
            # the only remaining copy of that document. Reported, not an error.
            delete_skipped.append(f"{subj[:50]} (kept — a document part could not be filed)")
        elif _eligible:
            if args.dry_run:
                # Mirror the REAL run: with no recoverable Trash (mode refuse/disabled or
                # trash off) the real run KEEPS the mail, so the preview must say so too —
                # never promise a trash the real run wouldn't perform.
                if can_delete:
                    trashed.append(f"would trash via {delete_mode} (rule): {subj[:50]}")
                else:
                    delete_skipped.append(f"{subj[:50]} (would keep — no reversible Trash, mode: {delete_mode})")
            elif not can_delete:
                delete_skipped.append(f"{subj[:50]} (mode: {delete_mode})")
            else:
                uid = num.decode() if isinstance(num, bytes) else str(num)
                try:
                    if trash_one(imap, num, delete_mode, trash_folder):
                        trashed.append(f"trashed via {delete_mode} (rule): {subj[:50]}")
                        if delete_mode in ("copy-trash", "expunge"):
                            deleted_uids.append(uid)
                    else:
                        problems.append(f"trash not OK ({delete_mode}): {subj[:40]}")
                except Exception as e:
                    problems.append(f"trash failed {subj[:30]}: {e}")

    # Expunge ONLY for modes that flagged \Deleted on the source mailbox, ALWAYS
    # scoped to exactly the UIDs we touched. Without UIDPLUS a plain EXPUNGE would
    # hit ANY \Deleted mail in the mailbox, so: for the reversible copy-trash mode we
    # refuse the unscoped expunge (the copies already live safely in Trash); the
    # unscoped EXPUNGE is only ever run when the user explicitly passed --force-expunge.
    expunge_plan = plan_expunge(delete_mode, getattr(imap, "capabilities", ()) or (), bool(deleted_uids))
    try:
        ok, note = execute_expunge(imap, expunge_plan, deleted_uids)
        if note:
            print(f"Note: {note}.")
        if not ok:
            problems.append(f"expunge not OK ({expunge_plan})")
    except Exception as e:
        problems.append(f"expunge failed: {e}")
    imap.logout()

    # ---- report ----
    def block(title, items):
        print(f"\n{title}: {len(items)}")
        for it in items:
            print(f"  - {it}")

    block("Filed", filed)
    if ocred:
        block("OCR'd (searchable text layer added)", ocred)
    if ocr_failed:
        block("OCR failed (filed OK, just not searchable — does NOT affect exit code)", ocr_failed)
    if ocr_missing:
        print("\nNote: ocr=auto/yes set but no OCR backend is available — scanned PDFs were filed "
              "WITHOUT a text layer. See README: OCR (macOS uses Apple Vision; Linux uses ocrmypdf).")
    block("Skipped", skipped)
    if quarantined:
        block("Protected PDFs (encrypted -> quarantine folder, not read, mail kept)"
              + (" — preview" if args.dry_run else ""), quarantined)
    block("Problems", problems)
    if trashed:
        block("Trashed (delete-after-filing)" + (" — preview" if args.dry_run else ""), trashed)
    if delete_skipped:
        block("Delete-after senders KEPT (no reversible deletion available — safe, not an error)",
              delete_skipped)
        print("  → the server offers no recoverable Trash; mail was filed but left in place. "
              "Pass --force-expunge to permanently delete (irreversible).")
    if args.no_trash and filed and delete_after:
        print("\nNote: --no-trash set, mail kept. Without it, senders marked 'delete-after-filing' "
              "are trashed after verified filing.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
