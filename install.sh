#!/usr/bin/env bash
# Install the filemates skill into ~/.claude/skills/ and create local config
# from the templates. Safe to re-run: it never overwrites your *.local.md files.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}/skills/filemates"

echo "Installing filemates -> $DEST"
mkdir -p "$DEST/reference" "$DEST/tools"
cp "$REPO_DIR/skills/filemates/SKILL.md" "$DEST/SKILL.md"
cp "$REPO_DIR/skills/filemates/config.example.md" "$DEST/config.example.md"
cp "$REPO_DIR/skills/filemates/reference/delete-rules.example.md" "$DEST/reference/delete-rules.example.md"
# the attachment downloader must live with the skill so SKILL.md's tools/ path resolves
# and the tool finds config.local.md next to it
cp "$REPO_DIR/tools/fetch-attachments.py" "$DEST/tools/fetch-attachments.py"
[ -f "$REPO_DIR/tools/ocr-folder.py" ] && cp "$REPO_DIR/tools/ocr-folder.py" "$DEST/tools/ocr-folder.py"
[ -f "$REPO_DIR/tools/macos-ocr.swift" ] && cp "$REPO_DIR/tools/macos-ocr.swift" "$DEST/tools/macos-ocr.swift"

# Create local config from templates only if missing (never clobber real data)
[ -f "$DEST/config.local.md" ] || { cp "$DEST/config.example.md" "$DEST/config.local.md"; echo "created config.local.md (edit it!)"; }
[ -f "$DEST/reference/delete-rules.local.md" ] || { cp "$DEST/reference/delete-rules.example.md" "$DEST/reference/delete-rules.local.md"; echo "created delete-rules.local.md (edit it!)"; }

# Optional OCR (for ocr: auto/yes — make scanned PDFs searchable). Best-effort, never fails install.
# macOS: use Apple's built-in Vision (no extra deps) — just compile the helper binary.
# Linux: use ocrmypdf + tesseract. Skip everything with NO_OCR=1.
if [ "${NO_OCR:-0}" != "1" ]; then
  echo
  if [ "$(uname -s)" = "Darwin" ]; then
    if command -v xcrun >/dev/null 2>&1; then
      echo "OCR (macOS, Apple Vision): compiling helper…"
      if xcrun swiftc -O "$REPO_DIR/tools/macos-ocr.swift" -o "$DEST/tools/macos-ocr" 2>/dev/null; then
        echo "  built $DEST/tools/macos-ocr (no Homebrew/tesseract needed)."
      else
        echo "  (compile failed — the tool will run the .swift via 'xcrun swift' instead; slightly slower.)"
      fi
    else
      echo "OCR (macOS): Xcode Command Line Tools not found. Run 'xcode-select --install' to enable OCR."
    fi
  else
    if ! command -v ocrmypdf >/dev/null 2>&1; then
      echo "OCR (Linux): installing ocrmypdf + tesseract…"
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y ocrmypdf tesseract-ocr-deu tesseract-ocr-eng \
          || echo "  (apt install failed — install ocrmypdf manually if you want OCR)"
      else
        echo "  Install 'ocrmypdf' + 'tesseract' via your package manager to enable OCR (see README -> OCR)."
      fi
    fi
  fi
fi

echo "Done. Edit $DEST/config.local.md and $DEST/reference/delete-rules.local.md, then restart Claude Code."
echo "Downloader installed at $DEST/tools/fetch-attachments.py (it reads the config.local.md next to it)."
