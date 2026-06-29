"""Safety tests for the FileMates downloader's deletion path.

These lock down the invariants the /concilium audit flagged as load-bearing:
deletion is gated on verified filing, protection always wins, deletion is only ever
reversible, and an unscoped EXPUNGE can never fire by accident. Every test is designed
to FAIL if its guarded behavior regresses — and the IMAP fakes model the server's
OK/NO contract (and record the commands issued) rather than replacing the behavior
under test.
"""
from datetime import datetime

import fetch_attachments as fa


# --------------------------------------------------------------------------- #
# Fakes that model the server contract (record commands; return OK/NO).
# --------------------------------------------------------------------------- #
class RecordingIMAP:
    """Records every uid()/expunge() call and returns a caller-chosen status, so a
    test can assert WHICH IMAP commands were issued (the real deletion mechanism),
    not merely that a function returned a string."""

    def __init__(self, results=None, caps=()):
        self.calls = []
        self.capabilities = caps
        self._results = results or {}

    def uid(self, command, *args):
        self.calls.append((command, args))
        return self._results.get(command, "OK"), [b""]

    def expunge(self):
        self.calls.append(("EXPUNGE-FULL", ()))
        return "OK", [b""]


class ListIMAP:
    """Models LIST output for the Trash-folder discovery probe."""

    def __init__(self, caps=(), list_lines=None):
        self.capabilities = caps
        self._lines = list_lines

    def list(self):
        return ("OK", self._lines) if self._lines is not None else ("NO", None)


# --------------------------------------------------------------------------- #
# 1. The hard safety gate: a filing failure must ALWAYS spare the mail.
# --------------------------------------------------------------------------- #
def test_filing_failure_never_trashes():
    # msg_filed=False is the failure path the verify-before-trash promise rests on.
    assert fa.trash_eligible(False, True, "trash") is False


def test_no_trash_flag_spares_mail():
    assert fa.trash_eligible(True, False, "trash") is False


def test_protected_decision_never_trashes():
    assert fa.trash_eligible(True, True, "protect") is False


def test_only_filed_trash_decision_is_eligible():
    assert fa.trash_eligible(True, True, "trash") is True


# --------------------------------------------------------------------------- #
# 2. Policy: protection always wins; unknown/empty are kept (safe default).
# --------------------------------------------------------------------------- #
def test_protected_wins_over_delete_after():
    prot = {"berater@kanzlei.de"}
    da = {"berater@kanzlei.de"}  # same address on BOTH lists
    assert fa.decide_trash("Steuerberater <berater@kanzlei.de>", prot, da) == "protect"


def test_domain_level_protection_covers_subdomain_alias():
    prot = {"berater@kanzlei.de"}
    da = {"berater@mail.kanzlei.de"}  # alias on a sub-domain, also on delete list
    assert fa.decide_trash("Kanzlei <berater@mail.kanzlei.de>", prot, da) == "protect"


def test_delete_after_sender_trashes():
    assert fa.decide_trash("Shop <news@shop.de>", set(), {"news@shop.de"}) == "trash"


def test_unknown_sender_is_kept():
    assert fa.decide_trash("X <hello@unknown.io>", set(), {"news@shop.de"}) == "keep"


def test_empty_delete_after_trashes_nothing():
    assert fa.decide_trash("X <news@shop.de>", set(), set()) == "keep"


def test_unparseable_sender_is_kept():
    assert fa.decide_trash("garbled-no-address", {"a@b.de"}, {"a@b.de"}) == "keep"
    assert fa.decide_trash("", {"a@b.de"}, {"a@b.de"}) == "keep"


# --------------------------------------------------------------------------- #
# 3. The mechanism issues the right (reversible) IMAP commands.
# --------------------------------------------------------------------------- #
def test_gmail_trash_uses_label():
    imap = RecordingIMAP()
    assert fa.trash_one(imap, b"7", "gmail-trash", None) is True
    assert imap.calls == [("STORE", (b"7", "+X-GM-LABELS", "\\Trash"))]


def test_move_trash_uses_uid_move():
    imap = RecordingIMAP()
    assert fa.trash_one(imap, b"7", "move-trash", "Trash") is True
    assert imap.calls == [("MOVE", (b"7", '"Trash"'))]


def test_copy_trash_copies_then_flags_source():
    imap = RecordingIMAP()
    assert fa.trash_one(imap, b"7", "copy-trash", "Papierkorb") is True
    assert imap.calls == [
        ("COPY", (b"7", '"Papierkorb"')),
        ("STORE", (b"7", "+FLAGS", "\\Deleted")),
    ]


def test_copy_trash_aborts_if_copy_fails_no_orphan_delete():
    # If COPY fails, the source must NOT be flagged \Deleted (else the mail would be
    # lost with no copy in Trash). The test fails if a STORE is ever issued here.
    imap = RecordingIMAP(results={"COPY": "NO"})
    assert fa.trash_one(imap, b"7", "copy-trash", "Trash") is False
    assert imap.calls == [("COPY", (b"7", '"Trash"'))]  # COPY only — no STORE


# --------------------------------------------------------------------------- #
# 4. The unscoped-EXPUNGE footgun can never fire by accident.
# --------------------------------------------------------------------------- #
def test_copy_trash_without_uidplus_never_unscoped_expunge():
    # THE irreversible-data-loss guard: without UIDPLUS we must SKIP expunge for
    # copy-trash (the copies already live in Trash). Reverting the guard fails this.
    assert fa.plan_expunge("copy-trash", (), True) == "skip"


def test_copy_trash_with_uidplus_is_scoped():
    assert fa.plan_expunge("copy-trash", ("UIDPLUS",), True) == "uid-scoped"


def test_forced_expunge_without_uidplus_is_full_only_when_forced():
    assert fa.plan_expunge("expunge", (), True) == "full"
    assert fa.plan_expunge("expunge", ("UIDPLUS",), True) == "uid-scoped"


def test_no_expunge_when_nothing_flagged_or_reversible_mode():
    assert fa.plan_expunge("copy-trash", (), False) == "none"
    assert fa.plan_expunge("gmail-trash", ("UIDPLUS",), True) == "none"
    assert fa.plan_expunge("move-trash", (), True) == "none"


# --------------------------------------------------------------------------- #
# 5. Server probe picks a reversible mode; refuses permanent by default.
# --------------------------------------------------------------------------- #
def test_probe_gmail_is_label_mode():
    mode, _, trash = fa.probe_delete_mode(ListIMAP(), "imap.gmail.com")
    assert (mode, trash) == ("gmail-trash", None)


def test_probe_special_use_trash_with_move():
    imap = ListIMAP(caps=("MOVE",), list_lines=[b'(\\HasNoChildren \\Trash) "/" "Trash"'])
    mode, _, trash = fa.probe_delete_mode(imap, "imap.fastmail.com")
    assert (mode, trash) == ("move-trash", "Trash")


def test_probe_named_trash_without_move_is_copy():
    imap = ListIMAP(list_lines=[b'(\\HasNoChildren) "/" "Papierkorb"'])
    mode, _, trash = fa.probe_delete_mode(imap, "imap.gmx.net")
    assert (mode, trash) == ("copy-trash", "Papierkorb")


def test_probe_move_capability_but_no_trash_folder_refuses():
    # MOVE advertised but no recoverable Trash folder -> must still refuse, not delete.
    imap = ListIMAP(caps=("MOVE",), list_lines=[b'(\\HasNoChildren) "/" "INBOX"'])
    assert fa.probe_delete_mode(imap, "imap.x")[0] == "refuse"


def test_find_trash_folder_handles_malformed_list_lines():
    # Garbage / non-bytes / empty LIST output must not crash and must find no Trash.
    assert fa.find_trash_folder(ListIMAP(list_lines=[b"garbage", b"", "(\\X) / INBOX"])) is None
    assert fa.find_trash_folder(ListIMAP(list_lines=None)) is None  # list() returns NO


def test_resolve_cached_refuse_stays_refuse():
    assert fa.resolve_delete_mode(ListIMAP(), "imap.x", {"delete_mode": "refuse"})[0] == "refuse"


def test_probe_refuses_when_no_recoverable_trash():
    imap = ListIMAP(list_lines=[b'(\\HasNoChildren) "/" "INBOX"'])
    mode, _, _ = fa.probe_delete_mode(imap, "imap.weird.host")
    assert mode == "refuse"


def test_probe_permanent_only_with_force():
    imap = ListIMAP(list_lines=[b'(\\HasNoChildren) "/" "INBOX"'])
    mode, _, _ = fa.probe_delete_mode(imap, "imap.weird.host", force_expunge=True)
    assert mode == "expunge"


# --------------------------------------------------------------------------- #
# 6. Config-first resolution: no per-run probe when recorded; guards intact.
# --------------------------------------------------------------------------- #
def test_gmail_host_forces_label_over_cached_destructive_mode():
    # Note-3 guard: a cached/edited destructive mode must NEVER apply to a Gmail mailbox
    # (a config copied from another account could otherwise \Deleted+expunge Gmail).
    broken = ListIMAP()
    mode, _, trash = fa.resolve_delete_mode(broken, "imap.GMAIL.com",
                                            {"delete_mode": "expunge", "trash_folder": "X"})
    assert (mode, trash) == ("gmail-trash", None)


def test_resolve_cached_copy_keeps_trash_folder():
    broken = ListIMAP()
    res = fa.resolve_delete_mode(broken, "x", {"delete_mode": "copy-trash", "trash_folder": "Papierkorb"})
    assert res[0] == "copy-trash" and res[2] == "Papierkorb"


def test_resolve_cached_expunge_needs_force():
    broken = ListIMAP()
    assert fa.resolve_delete_mode(broken, "x", {"delete_mode": "expunge"})[0] == "refuse"
    assert fa.resolve_delete_mode(broken, "x", {"delete_mode": "expunge"}, force_expunge=True)[0] == "expunge"


def test_resolve_cached_move_without_folder_reprobes():
    # cached mode needs a folder but none recorded -> re-probe rather than guess.
    good = ListIMAP(caps=("MOVE",), list_lines=[b'(\\Trash) "/" "Trash"'])
    res = fa.resolve_delete_mode(good, "x", {"delete_mode": "move-trash"})
    assert res[0] == "move-trash" and res[2] == "Trash"


def test_resolve_no_cache_falls_back_to_probe():
    good = ListIMAP(caps=("MOVE",), list_lines=[b'(\\Trash) "/" "Trash"'])
    assert fa.resolve_delete_mode(good, "x", {})[0] == "move-trash"


# --------------------------------------------------------------------------- #
# 7. delete-after list never falls back to the shipped example file.
# --------------------------------------------------------------------------- #
def test_delete_after_never_uses_example_fallback(monkeypatch, tmp_path):
    # Pin the flag itself, not the placeholder filter: put a REAL (non-example) address in
    # the delete-after section of the *example* file, with NO local file present. The
    # delete-after loader must NOT pick it up (allow_example_fallback=False), while a
    # fallback-enabled read of the same section DOES — proving the address is genuinely
    # readable and that only the flag keeps it out. Flipping the loader to fall back to the
    # example file (the exact regression named here) makes the first assert fail.
    local = tmp_path / "delete-rules.local.md"           # intentionally absent
    example = tmp_path / "delete-rules.example.md"
    example.write_text(
        "## DELETE ALLOWED\n"
        "### 2a. Delete-after-filing\n"
        "- realvendor@gmx.de  utility invoices that also live in a portal\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fa, "DEFAULT_DELETE_RULES", local)
    assert "realvendor@gmx.de" not in fa.load_delete_after_senders()      # flag holds
    assert "realvendor@gmx.de" in fa._senders_in_section(
        ("delete-after-filing",), allow_example_fallback=True)            # not vacuous


# --------------------------------------------------------------------------- #
# 8. Naming: path-escape stays impossible (security boundary).
# --------------------------------------------------------------------------- #
def test_render_name_strips_path_separators():
    out = fa.render_name("<sender>", "DOC", "invoice", "../../etc/passwd",
                         datetime(2026, 6, 3), "orig", ".pdf")
    assert "/" not in out and ".." not in out
    assert out.endswith(".pdf")


# --------------------------------------------------------------------------- #
# 9. md5 building block for dedup (full re-run skip is integration-level).
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 10. execute_expunge — the EXECUTION layer (not just the planner).
# --------------------------------------------------------------------------- #
def test_execute_expunge_skip_issues_no_command():
    # THE footgun guard at the execution layer: 'skip' must NOT touch the server.
    imap = RecordingIMAP()
    ok, _ = fa.execute_expunge(imap, "skip", ["1", "2"])
    assert ok is True
    assert imap.calls == []  # no EXPUNGE of any kind


def test_execute_expunge_none_issues_no_command():
    imap = RecordingIMAP()
    fa.execute_expunge(imap, "none", [])
    assert imap.calls == []


def test_execute_expunge_uid_scoped_is_scoped_to_run_uids():
    imap = RecordingIMAP()
    ok, _ = fa.execute_expunge(imap, "uid-scoped", ["3", "5"])
    assert ok is True
    assert imap.calls == [("EXPUNGE", ("3,5",))]  # UID-scoped, never a bare expunge()


def test_execute_expunge_full_only_unscoped_path():
    imap = RecordingIMAP()
    ok, _ = fa.execute_expunge(imap, "full", ["3"])
    assert ok is True
    assert imap.calls == [("EXPUNGE-FULL", ())]


def test_execute_expunge_reports_server_failure():
    imap = RecordingIMAP(results={"EXPUNGE": "NO"})
    ok, _ = fa.execute_expunge(imap, "uid-scoped", ["3"])
    assert ok is False


# --------------------------------------------------------------------------- #
# 11. trash_folder is IMAP-quote-sanitized (no command injection via a folder name).
# --------------------------------------------------------------------------- #
def test_trash_folder_name_is_sanitized():
    imap = RecordingIMAP()
    # a hostile/odd folder name with an embedded quote must not break out of the quoted arg
    fa.trash_one(imap, b"7", "move-trash", 'Trash" UID STORE 1 +FLAGS \\Deleted')
    (cmd, args) = imap.calls[0]
    folder_arg = args[1]
    assert cmd == "MOVE"
    assert folder_arg.count('"') == 2          # exactly the wrapping quotes, none embedded
    assert folder_arg.startswith('"') and folder_arg.endswith('"')


def test_file_md5_distinguishes_content(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello")
    c.write_bytes(b"world")
    assert fa._file_md5(a) == fa._file_md5(b)   # identical bytes -> identical hash
    assert fa._file_md5(a) != fa._file_md5(c)   # different bytes -> different hash


# --------------------------------------------------------------------------- #
# 12. Section detection is heading-bound: a comment that merely MENTIONS a
#     section marker must never flip the parser into the wrong section.
#     (Regression: a 'this is delete-after-filing above' note in the junk-sender
#     section leaked its addresses into the live auto-trash set.)
# --------------------------------------------------------------------------- #
def test_marker_word_in_a_later_comment_does_not_leak_into_delete_after(monkeypatch, tmp_path):
    # The Delete-after-filing section ends at the NEXT heading. A later, unrelated
    # section that MENTIONS the literal words "delete-after-filing" on a comment line
    # must not pull the addresses BELOW that comment into the auto-trash list. (This is
    # exactly the real bug: the live delete-rules.local.md has two such notes in the
    # junk-sender section reading "...das ist delete-after-filing oben", with the
    # IHK/Minor-Hotels addresses underneath them silently becoming live delete-after-
    # filing rules. This fixture minimises that to one marker note + one clean address
    # below it.) Reverting the fix — letting any marker-bearing line restart the
    # section — makes the last assert fail.
    local = tmp_path / "delete-rules.local.md"
    local.write_text(
        "## DELETE ALLOWED (trash)\n"
        "### Delete-after-filing (auto-trash once the attachment is filed)\n"
        "- realvendor@portal.de              # utility invoices that also live in a portal\n"
        "\n"
        "### Sender delete-list (junk)\n"
        "> Note: these are NOT delete-after-filing senders (their receipt lives in a portal).\n"
        "- ads@newsletter.de                 # plain junk newsletter\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fa, "DEFAULT_DELETE_RULES", local)
    da = fa.load_delete_after_senders()
    assert "realvendor@portal.de" in da          # the genuine section still loads
    assert "ads@newsletter.de" not in da         # a marker word in a preceding comment must NOT leak it in


def test_protected_section_survives_bold_sublabel_and_does_not_bleed(monkeypatch, tmp_path):
    # Two guarantees in one realistic layout:
    #  (a) a real protected sender under a bold '**Senders (never delete):**' sub-label
    #      is still loaded (the sub-label is content, not a section boundary), and
    #  (b) a later 'Unknown senders' section whose prose mentions "protected" must NOT
    #      bleed its address into the PROTECTED set.
    # The (a) assert is the safety-critical one: if the fix ever dropped the section on a
    # bold line, a protected sender would silently lose its never-delete shield. The (b)
    # assert fails on the old code, where the prose line restarted the protected section.
    local = tmp_path / "delete-rules.local.md"
    local.write_text(
        "## PROTECTED -- never deleted (overrides everything)\n"
        "**Senders (never delete):**\n"
        "- advisor@kanzlei.de                # tax advisor\n"
        "\n"
        "## Unknown senders -- never auto-delete\n"
        "If a sender is on neither the protected list nor any rule, keep it and never delete it.\n"
        "- stranger@unknown.io               # just a stranger\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fa, "DEFAULT_DELETE_RULES", local)
    prot = fa.load_protected_senders()
    assert "advisor@kanzlei.de" in prot           # survives the bold sub-label (safety-critical)
    assert "stranger@unknown.io" not in prot       # prose mentioning a marker must NOT bleed in


def test_bold_marker_line_is_content_not_a_heading(monkeypatch, tmp_path):
    # The by-design invariant the other two tests only catch as a side effect: a
    # bold-only line (e.g. '**These are protected, never deleted:**') is CONTENT, not a
    # section boundary — even when it carries real marker words. Directly kills a
    # plausible "make bold lines headings too" mutant of _is_section_heading: under that
    # mutant the bold line would open a PROTECTED section and pull the junk address into
    # the never-delete set (1st assert) while ending the delete-after section early
    # (2nd assert). Heading-only boundaries keep both correct.
    local = tmp_path / "delete-rules.local.md"
    local.write_text(
        "## DELETE ALLOWED (trash)\n"
        "### Delete-after-filing (auto-trash once the attachment is filed)\n"
        "- realvendor@portal.de              # genuine delete-after sender\n"
        "**These are protected, never deleted:**\n"   # bold line carrying BOTH protected markers
        "- ads@newsletter.de                 # a bold marker line must NOT make this protected\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(fa, "DEFAULT_DELETE_RULES", local)
    assert "ads@newsletter.de" not in fa.load_protected_senders()   # bold marker line != heading
    assert "ads@newsletter.de" in fa.load_delete_after_senders()    # still inside the real section
    assert "realvendor@portal.de" in fa.load_delete_after_senders()  # genuine sender unaffected
