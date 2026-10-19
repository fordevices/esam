"""Run logs (crew/terminal_log.py): the aidev mechanism, terminal.log with ANSI
codes + terminal.html + error.log, moved into the run folder a command makes."""

import pytest

pytest.importorskip("ansi2html")

from crew.terminal_log import logged  # noqa: E402


def _command(tmp_path, run, behaviour):
    @logged("ask", run, tmp_path / "logs")
    def cmd(name):
        folder = tmp_path / "runs" / name
        folder.mkdir(parents=True, exist_ok=True)
        run.update(run_id=name, folder=folder)
        print("\x1b[32mAgent: ESAM Triage Engineer\x1b[0m")
        if behaviour == "raise":
            raise RuntimeError("database down")
        if behaviour == "exit":
            raise SystemExit("NOT READY: no reasoning model")
        return 0
    return cmd


def test_log_and_html_land_in_the_run_folder(tmp_path):
    run = {}
    assert _command(tmp_path, run, "ok")("r1") == 0
    folder = tmp_path / "runs" / "r1"
    assert "\x1b[32mAgent: ESAM Triage Engineer" in (folder / "terminal.log").read_text()   # ANSI kept
    html = (folder / "terminal.html").read_text()
    assert "Agent: ESAM Triage Engineer" in html and "<style" in html
    assert not (folder / "error.log").exists()
    assert not list((tmp_path / "logs").glob("*.log"))             # moved, not copied


def test_a_second_command_on_the_same_run_keeps_the_first_log(tmp_path):
    run = {}
    _command(tmp_path, run, "ok")("r2")
    _command(tmp_path, run, "ok")("r2")
    assert (tmp_path / "runs" / "r2" / "terminal.log").exists() and (tmp_path / "runs" / "r2" / "terminal_ask.log").exists()


def test_failure_writes_error_log_and_reraises(tmp_path):
    run = {}
    with pytest.raises(RuntimeError):
        _command(tmp_path, run, "raise")("r3")
    assert "database down" in (tmp_path / "runs" / "r3" / "error.log").read_text()
    with pytest.raises(SystemExit):
        _command(tmp_path, run, "exit")("r4")
    assert "NOT READY" in (tmp_path / "runs" / "r4" / "error.log").read_text()


def test_stdout_is_restored(tmp_path):
    import sys
    before = sys.stdout
    _command(tmp_path, {}, "ok")("r5")
    assert sys.stdout is before
