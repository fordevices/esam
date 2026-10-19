"""
Run logs, the same mechanism as the aidev RTOS crew (aidev/crew/main.py):
everything a crew command prints, CrewAI's coloured agent and tool panels
included, goes to the terminal AND to the run folder:

    terminal.log     the raw output, ANSI colour codes kept
    terminal.html    the same, rendered (ansi2html, dark background)
    error.log        on a failure: the traceback, or the reason a command stopped

A command's run folder is only known part-way through it (the folder is
named after the run), so capture starts in work/logs/ and the files move
into the run folder when the command ends. A second command on the same run
(approve, undo) writes terminal_<command>.log next to the first. A command
that stops before making a run keeps its log in work/logs/.
"""

import functools
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

from ansi2html import Ansi2HTMLConverter


class TeeLogger:
    """Replaces sys.stdout and sys.stderr so all output goes to the original
    terminal (colours kept) and to a raw .log file (ANSI codes kept)."""

    def __init__(self, log_path: Path):
        self._stdout, self._stderr = sys.stdout, sys.stderr
        self._log = open(log_path, "w", encoding="utf-8")
        self.path = Path(log_path)
        sys.stdout = sys.stderr = self

    def write(self, message: str):
        self._stdout.write(message)
        self._log.write(message)

    def flush(self):
        self._stdout.flush()
        self._log.flush()

    def isatty(self):
        # True keeps Rich (CrewAI's console) producing colour codes for the log
        return self._stdout.isatty()

    @property
    def closed(self) -> bool:
        return self._log.closed

    def close(self):
        sys.stdout, sys.stderr = self._stdout, self._stderr
        self._log.flush()
        self._log.close()


def to_html(log_path: Path, html_path: Path, title: str = "ESAM crew run log") -> None:
    conv = Ansi2HTMLConverter(inline=False, dark_bg=True, font_size="14px", title=title)
    html_path.write_text(conv.convert(log_path.read_text(encoding="utf-8", errors="replace"), full=True), encoding="utf-8")


def logged(name: str, run: dict, staging: Path):
    """Decorator for a crew command: capture its output; when it ends, move
    the log into the folder of the run it made (`run["folder"]`, set by the
    command) and render it to HTML."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            staging.mkdir(parents=True, exist_ok=True)
            tmp = staging / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{name}.log"
            run.clear()
            tee, error = TeeLogger(tmp), None
            try:
                return fn(*args, **kwargs)
            except SystemExit as e:
                if e.code not in (0, None):
                    error = f"{name} stopped: {e.code}\n"
                raise
            except BaseException:
                error = traceback.format_exc()
                raise
            finally:
                tee.close()
                folder = run.get("folder")
                if folder and Path(folder).is_dir():
                    folder = Path(folder)
                    stem = "terminal" if not (folder / "terminal.log").exists() else f"terminal_{name}"
                    log = folder / f"{stem}.log"
                    shutil.move(str(tmp), log)
                else:
                    log, stem = tmp, tmp.stem
                html = log.with_suffix(".html")
                to_html(log, html, title=f"ESAM {name}: {run.get('run_id', log.parent.name)}")
                if error:
                    (log.parent / ("error.log" if stem == "terminal" else f"error_{name}.log")).write_text(error)
                print(f"  Terminal log  : {log}\n  Terminal HTML : {html}" + (f"\n  Error log     : {log.parent}" if error else ""))
        return inner
    return wrap
