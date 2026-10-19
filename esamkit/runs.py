"""
Crew run folders (work/runs/<timestamp>_<kind>[_<doc>]/), read without CrewAI:
what the Cockpit's Tasks tab lists, and the one decision it can record
directly (a rejection; an approval is queued as a task so the worker ingests).

Each folder may hold state.json (pipeline runs), reports (*.md), the run log
(terminal.log / terminal.html, crew/terminal_log.py) and error.log.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT

RUNS = PROJECT_ROOT / "work" / "runs"
DRAFTS = PROJECT_ROOT / "work" / "drafts"
INBOX = PROJECT_ROOT / "work" / "inbox"
BRIEFS = PROJECT_ROOT / "data" / "briefs"
_RUN_ID = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9_.-]+$")
SHOWN = (".html", ".md", ".json", ".log", ".csv")


def valid_run_id(run_id: str) -> bool:
    return bool(_RUN_ID.match(run_id)) and (RUNS / run_id).is_dir()


def read_state(folder: Path) -> dict:
    f = folder / "state.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _kind(name: str) -> str:
    rest = name.split("_", 2)[2] if name.count("_") >= 2 else name
    return {"environment": "env"}.get(rest.split("_")[0], rest.split("_")[0])


def summary(folder: Path) -> dict:
    s = read_state(folder)
    files = sorted(f.name for f in folder.iterdir() if f.is_file() and f.suffix in SHOWN and f.name != "state.json")
    pack = (s.get("evidence_pack") or {}).get("evidence_pack")
    return {"run_id": folder.name, "kind": s.get("kind") or _kind(folder.name), "state": s.get("state", ""),
            "document": (s.get("entry") or {}).get("id", ""), "recommendation": s.get("recommendation", ""),
            "verdict": s.get("verdict", ""), "lint": s.get("final_lint"), "approval": s.get("approval"),
            "created": s.get("created") or datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "files": files, "has_error": any(f.startswith("error") for f in files),
            "evidence_pack": str(Path(pack).relative_to(PROJECT_ROOT)) if pack and Path(pack).is_relative_to(PROJECT_ROOT) else None}


def list_runs(limit: int = 40) -> list[dict]:
    if not RUNS.exists():
        return []
    folders = sorted((p for p in RUNS.iterdir() if p.is_dir() and _RUN_ID.match(p.name)), reverse=True)
    return [summary(p) for p in folders[:limit]]


def reject(run_id: str, by: str, note: str) -> dict:
    """Record an SE's rejection of a run awaiting approval. Nothing is ingested."""
    if not valid_run_id(run_id):
        raise LookupError(f"no run {run_id!r}")
    if not by.strip() or not note.strip():
        raise ValueError("a rejection needs who rejects it and why")
    folder = RUNS / run_id
    state = read_state(folder)
    if state.get("state") != "awaiting_approval":
        raise ValueError(f"{run_id} is {state.get('state') or 'not a pipeline run'}, not awaiting approval")
    state.update(state="rejected", approval={"decision": "rejected", "by": by.strip(), "note": note.strip(),
                                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    (folder / "state.json").write_text(json.dumps(state, indent=1, default=str))
    return state


def choices() -> dict:
    """Files an SE may queue: documents in the inbox (and lesson 06's staged ones), and briefs."""
    rel = lambda p: str(p.relative_to(PROJECT_ROOT))  # noqa: E731
    staged = PROJECT_ROOT / "lessons" / "06_missing_document" / "staged"
    docs = sorted(INBOX.glob("*.md")) + sorted(staged.glob("*.md"))
    return {"documents": [rel(p) for p in docs], "briefs": [rel(p) for p in sorted(BRIEFS.glob("*.yaml"))]}
