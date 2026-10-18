"""CLI menu: permission filtering and a full add-survey flow."""
import builtins
import contextlib
import io

import reefwatch_app as rw


def run_menu(svc, role, inputs):
    answers = iter(inputs)
    builtins.input = lambda *_: next(answers)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rw.run_menu(svc, role)
    return out.getvalue()


def test_staff_cannot_reach_admin_options(seeded):
    out = run_menu(seeded, "field_staff", ["9", "12", "q"])
    assert out.count("Unknown option") == 2 and "Generate daily summary" not in out


def test_admin_menu_lists_everything(seeded):
    out = run_menu(seeded, "admin", ["q"])
    assert "Generate daily summary" in out and "Generate survey trend charts" in out


def test_add_survey_through_menu(seeded, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # PDFs land in ./reports under the temp dir
    before = seeded.cosmos.c["surveys"].count_documents({})
    out = run_menu(seeded, "field_staff",
                   ["1", "Maya", "", "", "Cod Hole", "", "Parrotfish:3", "1:1", "250", "note", "q"])
    assert "Saved survey" in out and seeded.cosmos.c["surveys"].count_documents({}) == before + 1
    assert (tmp_path / "reports").exists()


def test_invalid_input_does_not_crash(seeded):
    out = run_menu(seeded, "admin", ["10", "q"])
    assert "Date" in out
