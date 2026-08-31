#!/usr/bin/env python3
"""FileMates — verified file moves.

Filing a document is a move: out of the inbox folder, into its place, under its
proper name. Doing that with a raw `mv` in an unattended run does not work — a
shell `mv` is asked for every time. Measured across six scheduled runs, EVERY
`mv` blocked on a permission prompt (496 s, 720 s, 864 s, 1 733 s, 13 908 s and
once 152 419 s = 42 hours), while `ls`, `md5` and `python3 tools/*.py` never
did. A run that stalls for two days is not unattended.

So the move becomes a tool — and with it, the guarantees that used to live in
prose become executable:

  * moves, never copies; verifies the result before calling it done
  * NEVER overwrites an existing target (that is a skip, reported, not an error)
  * NEVER deletes: a destination inside the Trash is refused outright
  * sources must sit inside an allowed root (symlinks resolved, so nothing can
    be smuggled out of it)
  * creates a folder only underneath an explicitly allowed parking area
  * carries the hidden `.<name>.payload-md5` sidecar along on a rename, so the
    downloader's de-dup fingerprint never points at a name that no longer exists

Usage:
  python3 tools/move-files.py --json '[{"from":"/abs/a.pdf","to":"/abs/b.pdf"}]'
  python3 tools/move-files.py --json '[…]' --root ~/Downloads --dry-run

Exit codes: 0 = done (skips are fine), 1 = at least one problem, 2 = bad input.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

__version__ = "0.6.1"  # x-release-please-version

DEFAULT_ROOT = Path.home() / "Downloads"
DEFAULT_TRASH = Path.home() / ".Trash"
PARKING_DIRNAME = "_Vorschlaege"


def sidecar_for(path: Path) -> Path:
    """The hidden payload fingerprint FileMates writes next to a filed file."""
    return path.parent / f".{path.name}.payload-md5"


def _real(path: Path) -> Path:
    """Resolve symlinks — also for paths that do not exist yet."""
    return Path(os.path.realpath(str(path)))


def _inside(path: Path, roots) -> bool:
    return any(_real(path).is_relative_to(_real(r)) for r in roots)


def parse_moves(raw: str):
    """Validate the WHOLE batch up front. Returns (moves, error_message).

    All-or-nothing on purpose: a malformed batch means the caller's model of the
    world is wrong, and half-applying it would leave a state nobody planned."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return None, f"not valid JSON: {exc}"
    if not isinstance(payload, list):
        return None, 'JSON must be a list of {"from": …, "to": …} objects'
    moves = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            return None, f"entry {i} is not an object"
        for key in ("from", "to"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                return None, f"entry {i}: {key!r} must be a non-empty string"
            if not value.startswith("/"):
                return None, (f"entry {i}: {key!r} must be an ABSOLUTE path "
                              f"({value!r}) — a relative path would resolve against "
                              f"the working directory of an unattended run")
        moves.append((Path(item["from"]), Path(item["to"])))
    return moves, None


def plan_move(src: Path, dst: Path, roots, mkdir_under, trash_dirs):
    """Decide what happens to one file. Pure: returns (verdict, detail).

    verdict is 'move' | 'skip' | 'problem'; for 'move', detail says whether the
    target folder has to be created and whether a sidecar travels along."""
    if not src.is_symlink() and not src.exists():
        return "problem", f"source does not exist: {src}"
    if not _inside(src, roots):
        return "problem", (f"source is outside the allowed root(s): {src} "
                           f"(allowed: {', '.join(str(r) for r in roots)})")
    if any(_real(dst).is_relative_to(_real(t)) for t in trash_dirs):
        return "problem", (f"refusing to move into the Trash: {dst} — this tool "
                           f"never deletes; deletion stays a human decision")
    if dst.exists():
        return "skip", f"target exists, source kept: {dst}"

    needs_folder = not dst.parent.exists()
    if needs_folder and not _inside(dst.parent, mkdir_under):
        return "problem", (f"target folder does not exist: {dst.parent} — and it is "
                           f"not inside an allowed parking area, so it is not created")

    src_side, dst_side = sidecar_for(src), sidecar_for(dst)
    has_sidecar = src_side.exists()
    if has_sidecar and dst_side.exists():
        return "problem", (f"a different payload-md5 sidecar already sits at "
                           f"{dst_side} — moving would cross the fingerprints; "
                           f"resolve by hand")
    return "move", {"needs_folder": needs_folder, "sidecar": has_sidecar}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Move files into place — verified, never overwriting, never deleting.",
        allow_abbrev=False)
    ap.add_argument("--json", dest="payload", required=True,
                    help='moves as inline JSON: [{"from":"/abs/a","to":"/abs/b"}, …]')
    ap.add_argument("--root", action="append", default=None,
                    help="allowed source root (repeatable; default: ~/Downloads)")
    ap.add_argument("--mkdir-under", action="append", default=None, dest="mkdir_under",
                    help="folders may be created below this path (repeatable; "
                         "default: <root>/_Vorschlaege)")
    ap.add_argument("--trash-dir", action="append", default=None, dest="trash_dirs",
                    help="destinations below this path are refused (default: ~/.Trash)")
    ap.add_argument("--dry-run", action="store_true", help="plan only, write nothing")
    ap.add_argument("--version", action="version", version=f"FileMates move-files {__version__}")
    args = ap.parse_args(argv)

    roots = [Path(os.path.expanduser(r)) for r in (args.root or [str(DEFAULT_ROOT)])]
    mkdir_under = [Path(os.path.expanduser(p)) for p in
                   (args.mkdir_under or [str(r / PARKING_DIRNAME) for r in roots])]
    trash_dirs = [Path(os.path.expanduser(p)) for p in
                  (args.trash_dirs or [str(DEFAULT_TRASH)])]

    moves, err = parse_moves(args.payload)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        print("Nothing was moved — the batch is refused as a whole.", file=sys.stderr)
        return 2

    if args.dry_run:
        print("DRY-RUN: nothing will be written or moved.")

    moved, skipped, problems = [], [], []
    for src, dst in moves:
        verdict, detail = plan_move(src, dst, roots, mkdir_under, trash_dirs)
        if verdict == "problem":
            problems.append(detail)
            continue
        if verdict == "skip":
            skipped.append(detail)
            continue

        if args.dry_run:
            if detail["needs_folder"]:
                moved.append(f"would create folder: {dst.parent}")
            moved.append(f"would move: {src}  ->  {dst}")
            if detail["sidecar"]:
                moved.append(f"would move sidecar: {sidecar_for(src).name}"
                             f"  ->  {sidecar_for(dst).name}")
            continue

        size = src.stat().st_size
        if detail["needs_folder"]:
            dst.parent.mkdir(parents=True, exist_ok=True)
            moved.append(f"created folder: {dst.parent}")
        try:
            shutil.move(str(src), str(dst))
        except OSError as exc:
            problems.append(f"move failed: {src} -> {dst}: {exc}")
            continue
        # verify before calling it filed — the whole point of doing this in a tool
        if not dst.exists() or dst.stat().st_size != size or src.exists():
            problems.append(f"verify failed after move: {dst}")
            continue
        moved.append(f"moved: {src}  ->  {dst}")
        if detail["sidecar"]:
            try:
                shutil.move(str(sidecar_for(src)), str(sidecar_for(dst)))
                moved.append(f"moved sidecar: {sidecar_for(src).name}"
                             f"  ->  {sidecar_for(dst).name}")
            except OSError as exc:
                problems.append(f"sidecar move failed for {dst.name}: {exc}")

    def block(title, items):
        print(f"\n{title}: {len(items)}")
        for item in items:
            print(f"  - {item}")

    block("Moved", moved)
    block("Skipped", skipped)
    block("Problems", problems)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
