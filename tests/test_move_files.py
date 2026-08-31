# --------------------------------------------------------------------------- #
# Verified file moves as a TOOL, not as improvised shell.
#
# WHY: the scheduled runs file documents by moving them. A raw `mv` is asked for
# every single time — measured across six scheduled runs, every `mv` blocked on a
# permission prompt (496 s, 720 s, 864 s, 1 733 s, 13 908 s, and once 152 419 s =
# 42 hours), while `ls`, `md5` and `python3 tools/*.py` never did. An unattended
# run that stalls for two days is not unattended.
#
# Moving the moves into a tool does more than dodge the prompt: the guarantees
# that used to live in SKILL prose — never overwrite, never delete, never invent
# a folder outside the parking area, verify after the move, carry the hidden
# payload-md5 sidecar along — become executable and testable here.
#
# PII: all paths are tmp_path fixtures; no real user data.
# --------------------------------------------------------------------------- #
import json

import move_files as mf


def _mv(src, dst):
    return json.dumps([{"from": str(src), "to": str(dst)}])


def _run(argv, capsys=None):
    rc = mf.main(argv)
    return rc, (capsys.readouterr().out if capsys else "")


def _file(p, text="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------------ happy path


def test_moves_and_verifies(tmp_path, capsys):
    src = _file(tmp_path / "dl" / "a.pdf", "hello")
    dst = tmp_path / "ziel" / "Anbieter_Rechnung_01-08-2026.pdf"
    dst.parent.mkdir()
    rc, out = _run(["--json", _mv(src, dst), "--root", str(tmp_path / "dl")], capsys)
    assert rc == 0
    assert dst.read_text(encoding="utf-8") == "hello"
    assert not src.exists()                         # a move, never a copy
    assert "moved:" in out


def test_reports_nothing_to_do_for_empty_list(tmp_path, capsys):
    rc, out = _run(["--json", "[]", "--root", str(tmp_path)], capsys)
    assert rc == 0
    assert "moved: 0" in out.lower() or "moved" in out.lower()


# ------------------------------------------------------------------- guardrails


def test_never_overwrites_an_existing_target(tmp_path, capsys):
    src = _file(tmp_path / "dl" / "a.pdf", "neu")
    dst = _file(tmp_path / "ziel" / "a.pdf", "alt")
    rc, out = _run(["--json", _mv(src, dst), "--root", str(tmp_path / "dl")], capsys)
    assert rc == 0                                  # a skip is not a failure
    assert dst.read_text(encoding="utf-8") == "alt"  # untouched
    assert src.exists()                             # source kept, nothing lost
    assert "skipped: 1" in out.lower()


def test_source_outside_root_is_refused(tmp_path, capsys):
    src = _file(tmp_path / "woanders" / "a.pdf")
    dst = tmp_path / "ziel" / "a.pdf"
    dst.parent.mkdir()
    rc, out = _run(["--json", _mv(src, dst), "--root", str(tmp_path / "dl")], capsys)
    assert rc == 1
    assert src.exists() and not dst.exists()
    assert "outside" in out.lower()


def test_symlink_cannot_smuggle_a_file_out_of_root(tmp_path, capsys):
    outside = _file(tmp_path / "geheim" / "steuer.pdf", "vertraulich")
    root = tmp_path / "dl"
    root.mkdir()
    link = root / "harmlos.pdf"
    link.symlink_to(outside)
    dst = tmp_path / "ziel" / "harmlos.pdf"
    dst.parent.mkdir()
    rc, _ = _run(["--json", _mv(link, dst), "--root", str(root)], capsys)
    assert rc == 1
    assert outside.exists()                         # the real file never moved


def test_missing_target_folder_is_refused_by_default(tmp_path, capsys):
    src = _file(tmp_path / "dl" / "a.pdf")
    dst = tmp_path / "gibtsnicht" / "a.pdf"
    rc, out = _run(["--json", _mv(src, dst), "--root", str(tmp_path / "dl")], capsys)
    assert rc == 1
    assert not dst.parent.exists()                  # no folder invented
    assert src.exists()
    assert "does not exist" in out.lower()


def test_folder_is_created_only_under_the_parking_area(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    dst = root / "_Vorschlaege" / "Neuer Ordner" / "a.pdf"
    rc, out = _run(["--json", _mv(src, dst), "--root", str(root),
                    "--mkdir-under", str(root / "_Vorschlaege")], capsys)
    assert rc == 0
    assert dst.exists()
    assert "created folder" in out.lower()


def test_deleting_via_trash_destination_is_refused(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    trash = tmp_path / ".Trash"
    trash.mkdir()
    rc, out = _run(["--json", _mv(src, trash / "a.pdf"), "--root", str(root),
                    "--trash-dir", str(trash)], capsys)
    assert rc == 1
    assert src.exists()
    assert "trash" in out.lower()


def test_same_source_twice_reports_the_second_as_a_problem(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    payload = json.dumps([{"from": str(src), "to": str(ziel / "a.pdf")},
                          {"from": str(src), "to": str(ziel / "b.pdf")}])
    rc, out = _run(["--json", payload, "--root", str(root)], capsys)
    assert rc == 1
    assert (ziel / "a.pdf").exists() and not (ziel / "b.pdf").exists()
    assert "problems: 1" in out.lower()


# ---------------------------------------------------------------- sidecar logic


def test_payload_md5_sidecar_travels_with_the_file(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "Alt.pdf", "inhalt")
    _file(root / ".Alt.pdf.payload-md5", "abc123")
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    dst = ziel / "Neu.pdf"
    rc, out = _run(["--json", _mv(src, dst), "--root", str(root)], capsys)
    assert rc == 0
    assert (ziel / ".Neu.pdf.payload-md5").read_text(encoding="utf-8") == "abc123"
    assert not (root / ".Alt.pdf.payload-md5").exists()   # not left behind, not crossed
    assert "moved sidecar" in out.lower()


def test_absent_sidecar_is_not_a_problem(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    rc, out = _run(["--json", _mv(src, ziel / "b.pdf"), "--root", str(root)], capsys)
    assert rc == 0
    assert "moved sidecar" not in out.lower()   # tmp_path name contains "sidecar"


def test_sidecar_is_not_overwritten_at_the_target(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "Alt.pdf")
    _file(root / ".Alt.pdf.payload-md5", "neu")
    ziel = tmp_path / "ziel"
    _file(ziel / ".Neu.pdf.payload-md5", "alt")
    rc, out = _run(["--json", _mv(src, ziel / "Neu.pdf"), "--root", str(root)], capsys)
    assert (ziel / ".Neu.pdf.payload-md5").read_text(encoding="utf-8") == "alt"
    assert rc == 1                                   # a crossed sidecar must be loud


# -------------------------------------------------------------------- dry-run


def test_dry_run_writes_nothing(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    rc, out = _run(["--json", _mv(src, ziel / "b.pdf"), "--root", str(root),
                    "--dry-run"], capsys)
    assert rc == 0
    assert src.exists() and not (ziel / "b.pdf").exists()
    assert "would move" in out.lower()


def test_dry_run_creates_no_folder(tmp_path, capsys):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    dst = root / "_Vorschlaege" / "Neu" / "a.pdf"
    rc, _ = _run(["--json", _mv(src, dst), "--root", str(root),
                  "--mkdir-under", str(root / "_Vorschlaege"), "--dry-run"], capsys)
    assert rc == 0
    assert not dst.parent.exists()


# ------------------------------------------------------------- input validation


def test_malformed_json_is_usage_exit_2(tmp_path):
    rc, _ = _run(["--json", "{not json", "--root", str(tmp_path)])
    assert rc == 2


def test_non_list_json_is_usage_exit_2(tmp_path):
    rc, _ = _run(["--json", '{"from":"a","to":"b"}', "--root", str(tmp_path)])
    assert rc == 2


def test_missing_key_is_usage_exit_2(tmp_path):
    rc, _ = _run(["--json", '[{"from":"a"}]', "--root", str(tmp_path)])
    assert rc == 2


def test_non_string_field_is_usage_exit_2(tmp_path):
    rc, _ = _run(["--json", '[{"from":1,"to":"b"}]', "--root", str(tmp_path)])
    assert rc == 2


def test_relative_path_is_usage_exit_2(tmp_path):
    # absolute paths only — a relative path would resolve against the CWD of an
    # unattended run, which is not something the caller can reason about.
    rc, _ = _run(["--json", '[{"from":"a.pdf","to":"/tmp/b.pdf"}]', "--root", str(tmp_path)])
    assert rc == 2


def test_nothing_moves_when_validation_fails(tmp_path):
    root = tmp_path / "dl"
    src = _file(root / "a.pdf")
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    payload = json.dumps([{"from": str(src), "to": str(ziel / "a.pdf")},
                          {"from": str(src), "to": 5}])
    rc, _ = _run(["--json", payload, "--root", str(root)])
    assert rc == 2
    assert src.exists() and not (ziel / "a.pdf").exists()   # batch refused as a whole
