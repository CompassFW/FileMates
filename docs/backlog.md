# FileMates — Backlog

Captured ideas / open architectural questions, not yet scheduled. Newest first.

---

## BL-1 — Cross-LLM / no-LLM portability (the orchestration dependency)

**Captured:** 2026-06-04 (maintainer) · **Priority:** high (real adoption blocker) · **Status:** open

### Problem
Today FileMates is driven by an LLM agent (Claude Code) that orchestrates the tool: it
classifies mail (A/B/C/D), writes the `Wer-Was-Warum`, drives Gmail-MCP + osascript, and calls
the Python helpers. Other users may have **no LLM** or a **different LLM** — so the current setup
is a strong portability constraint.

### Current state (honest)
Two layers:
- **Deterministic core** (`tools/reminder-helper.py`, `tools/fetch-attachments.py`) — pure Python,
  **no LLM needed**. Dedup, anchors, attachment download/file/verify, reversible rule-based trash.
  This split already exists precisely to make the mechanical guarantees LLM-independent. ✅
- **Judgment layer** (is this mail actionable? which label? crisp title? junk?) + driving the
  Gmail-MCP/osascript boundary — **bound to an LLM agent runtime**.

So:
- **No-LLM user:** can use the Python CLIs directly (download/file by sender+type, create reminders
  from JSON, trash by rule) — but loses the automatic triage/classification.
- **Different LLM (GPT/Gemini/local):** SKILL.md is mostly LLM-agnostic prose + CLI, but it assumes
  a Claude-Code-style harness — MCP tool names, the skill format, the Claude-in-Chrome fallback.
  Theoretically portable, **practically untested**.

**Conclusion:** runs with Claude (the "at least with you" bar is met). Full cross-LLM portability is
**NOT solved**: the judgment layer + the harness are Claude-Code-bound.

### Options to explore (not decided)
1. **Harness-agnostic core CLI** — push more of the orchestration into a deterministic `filemates`
   CLI (scan → classify-stub → plan → apply) so an LLM only supplies the *classification verdicts*
   over a thin, documented JSON contract (mail in → {bucket, who/what/why, label, topic} out).
   Any LLM (or even rules/regex for simple cases) could fill that contract.
2. **Provider-agnostic mail/task adapters** — abstract Gmail-MCP behind an interface (IMAP for
   read/label/trash is already provider-agnostic in fetch-attachments; the *MCP* dependency is the
   gap) so it doesn't need a specific connector.
3. **Document a "no-LLM / bring-your-own-LLM" mode** in the README: exactly which CLI calls give
   value without any LLM, and the JSON contract an external LLM would produce.
4. **Decide the realistic target:** "Claude-first, others best-effort" vs. true multi-LLM. Pick the
   bar before investing.

### Why it matters
Adoption: an LLM-locked tool only serves users on that one stack. The core is already portable;
the gap is the judgment layer + harness assumptions.
