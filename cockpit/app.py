"""
ESAM SE Cockpit (learning build) — the web front end SEs use.

The Phase 0 Execution Guide (Step 12) sketches the pilot Cockpit. This build
grows it one tab at a time, on top of esamkit instead of its own SQL:

    Dashboard   health report (six checks), graph counts, document coverage
                per subsystem, requirement coverage, interfaces,
                the last run of each check (e.g. lesson 06's confidence test)
    Graph       the baseline as a tree, a tree with interface arcs, a network
    Ontology    what's in the SE vocabulary file, and whether the baseline passes it
    Tasks       the crew's task list (esam_tasks), reviews waiting for an SE's
                decision, recent runs with their logs and reports, and a form
                to queue work. A worker (python -m crew.main worker) does it.

Still to come (see notes/): the health report (Dashboard), the query tab
(lesson 05 hybrid), and baseline diff / approve.

Run it:

    uvicorn cockpit.app:app --port 8080        # from the esam/ folder
    open http://127.0.0.1:8080

The page itself (static/index.html) has no CDN or font downloads, so it works
unchanged on the air-gapped pilot VM. The same page can also be written out
as one self-contained file with the data baked in; see export_snapshot.py.
"""

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse

import yaml

from esamkit import runlog
from esamkit import runs as crew_runs
from esamkit.config import PROJECT_ROOT, settings
from esamkit.factory import get_graph_store
from esamkit.graphstores.base import GraphStore
from esamkit.health import health_report
from esamkit.ontology import check_baseline, default_ontology

PAGE = Path(__file__).parent / "static" / "index.html"

# The document types the Dashboard checks: every type some ontology rule can
# require. Which of them each subsystem actually needs comes from the rules.
DOC_TYPES = default_ontology().required_types()
# The ontology is read once, here. Edits to the file need a restart to apply;
# the Ontology tab says so when the file is newer than this.
ONTOLOGY_LOADED_AT = datetime.now(timezone.utc)

# The page ships as a fragment (title, style, markup, script) so the very same
# file can be published as a snapshot; the server adds the document shell.
_SHELL = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
          '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
          '</head><body>{page}</body></html>')
_DATA_SLOT = '<script id="esam-data" type="application/json">null</script>'

app = FastAPI(title="ESAM SE Cockpit (learning build)")


@lru_cache(maxsize=1)
def get_graph() -> GraphStore:
    # One connection for the process; tests override this dependency.
    return get_graph_store()


def get_runs_dsn() -> str:
    # The main database: run log and chunk counts. Tests point this elsewhere.
    return settings.pg_dsn


def last_runs(dsn: str) -> list[dict]:
    """The latest run of each check. A missing or unreachable run log just
    means "never run"; it must not take the Dashboard down."""
    try:
        return runlog.last_runs(dsn)
    except Exception:
        return []


def required_by_rules(snapshot: dict) -> dict[str, dict[str, list[str]]]:
    """{subsystem eid: {doc_type: [why, ...]}} from the ontology's rules."""
    onto = default_ontology()
    linked = {e[k] for e in snapshot["edges"] if e["type"] == "INTERFACES_WITH" for k in ("from_eid", "to_eid")}
    return {v["eid"]: onto.required_documents(v["props"], v["eid"] in linked)
            for v in snapshot["vertices"] if v["label"] == "Subsystem"}


def ontology_payload() -> dict:
    """The ontology as loaded, where it came from, and the baseline checked against it."""
    onto, path = default_ontology(), settings.ontology_yaml
    findings = onto.check_self() + check_baseline(onto, yaml.safe_load(settings.baseline_yaml.read_text()),
                                                  settings.documents_dir)
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    try:
        relative = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        relative = str(path)
    return {
        "path": str(path),
        "relative_path": relative,
        "file_modified": modified.isoformat(timespec="seconds"),
        "loaded_at": ONTOLOGY_LOADED_AT.isoformat(timespec="seconds"),
        "changed_since_load": modified > ONTOLOGY_LOADED_AT,
        "data": onto.data,
        "check": [{"level": f.level, "where": f.where, "message": f.message} for f in findings],
    }


def health_of(graph: GraphStore, dsn: str) -> dict:
    """The six-check health report (esamkit/health.py). Chunk counts come
    straight from the database: no embedding model is loaded."""
    from esamkit.vectorstores.pgvector_store import chunk_counts

    try:
        chunks = chunk_counts(dsn, settings.collection)
    except Exception:
        chunks = {}
    return health_report(graph, default_ontology(), yaml.safe_load(settings.baseline_yaml.read_text()),
                         settings.documents_dir, chunks)


def collect(graph: GraphStore, doc_types: list[str], runs: list[dict] | None = None,
            dsn: str | None = None) -> dict:
    """Everything the page draws, in one payload (also what a snapshot embeds)."""
    snapshot = graph.snapshot()
    onto = default_ontology()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "graph": snapshot,
        "doc_types": doc_types,
        "doc_type_names": {t: d.get("name", t) for t, d in onto.document_types.items()},
        "gaps": {t: [r["eid"] for r in graph.artifact_gaps(t)] for t in doc_types},
        "required": required_by_rules(snapshot),
        "trace": {"summary": graph.trace_summary(), "untested": graph.untested_requirements()},
        "rules": onto.rules.get("required_documents", []),
        "ontology": ontology_payload(),
        "health": health_of(graph, dsn or settings.pg_dsn),
        "runs": runs or [],
        "tasks": tasks_payload(dsn or settings.pg_dsn),
    }


def tasks_payload(dsn: str) -> dict:
    """The Tasks tab: the queue (if the database has one), runs, and what can be queued."""
    out = {"runs": crew_runs.list_runs(), "choices": crew_runs.choices(), "tasks": [], "queue": None}
    try:
        from esamkit.tasks import TaskQueue
        out["tasks"] = TaskQueue(dsn).list(limit=50)
        out["queue"] = "ok"
    except Exception as e:  # no database: runs still show
        out["queue"] = f"{type(e).__name__}: {e}"
    return out


def render_page(data: dict | None = None, source: str = "live") -> str:
    """The page fragment. With `data`, it's baked in and the page never calls
    the API (a snapshot); without it, the page fetches /api/cockpit."""
    page = PAGE.read_text()
    if data is None:
        return page
    payload = json.dumps({**data, "source": source}, default=str)
    # "</" inside a <script> block would end it early; JSON allows "<\/".
    payload = payload.replace("</", "<\\/")
    assert page.count(_DATA_SLOT) == 1, "index.html must contain the data slot exactly once"
    return page.replace(_DATA_SLOT, f'<script id="esam-data" type="application/json">{payload}</script>')


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _SHELL.format(page=render_page())


@app.get("/api/health")
def health(graph: GraphStore = Depends(get_graph)) -> dict:
    try:
        snap = graph.snapshot()
    except Exception as e:  # report, don't crash: this is what ops will poll
        raise HTTPException(status_code=503, detail=f"graph store unreachable: {e}") from e
    return {"status": "ok", "vertices": len(snap["vertices"]), "edges": len(snap["edges"])}


@app.get("/api/health-report")
def health_report_api(graph: GraphStore = Depends(get_graph), runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    return health_of(graph, runs_dsn)


@app.get("/api/ontology")
def ontology() -> dict:
    return ontology_payload()


@app.get("/api/cockpit")
def cockpit(types: str = Query(",".join(DOC_TYPES), description="comma-separated document types"),
            graph: GraphStore = Depends(get_graph), runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    doc_types = [t.strip().lower() for t in types.split(",") if t.strip()]
    return collect(graph, doc_types, last_runs(runs_dsn), runs_dsn)


# ── Tasks tab ───────────────────────────────────────────────────────────────
# The Cockpit listens on 127.0.0.1 in this build. Before it is reachable by
# others (the pilot), these endpoints need the pilot's sign-on: an approval
# records the name the SE types, and nothing here checks who they are.

def _queue(dsn: str):
    from esamkit.tasks import TaskQueue
    try:
        return TaskQueue(dsn)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"the task list needs the database: {e}") from e


@app.get("/api/tasks")
def tasks_api(runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    return tasks_payload(runs_dsn)


@app.post("/api/tasks")
def add_task(kind: str = Body(...), arg: str = Body(""), by: str = Body(...), note: str = Body(""),
             against_recommendation: bool = Body(False), priority: int = Body(0),
             runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    by, arg = by.strip(), arg.strip()
    if not by:
        raise HTTPException(422, "say who is adding the task")
    if kind in ("review", "author"):
        allowed = crew_runs.choices()["documents" if kind == "review" else "briefs"]
        if arg not in allowed:
            raise HTTPException(422, f"{arg!r} is not one of the files the Cockpit offers for {kind}")
        payload = {"file": arg, "set": []} if kind == "review" else {"brief": arg}
    elif kind == "ask":
        if not arg:
            raise HTTPException(422, "type the question")
        payload = {"question": arg}
    elif kind == "env":
        payload = {}
    elif kind == "approve":
        if not crew_runs.valid_run_id(arg):
            raise HTTPException(404, f"no run {arg!r}")
        state = crew_runs.read_state(crew_runs.RUNS / arg)
        if state.get("state") != "awaiting_approval":
            raise HTTPException(409, f"{arg} is {state.get('state')}, not awaiting approval")
        if state.get("recommendation") != "INGEST" and not (against_recommendation and note.strip()):
            raise HTTPException(409, f"the SE Reviewer recommended {state.get('recommendation')!r}: approving anyway "
                                     "needs 'against the recommendation' and a note saying why")
        payload = {"run": arg, "note": note.strip(), "against_recommendation": against_recommendation}
    else:
        raise HTTPException(422, f"unknown task kind {kind!r}")
    tid = _queue(runs_dsn).add(kind, payload, created_by=by, priority=priority)
    return {"id": tid, "kind": kind, "payload": payload}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: int, runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    if not _queue(runs_dsn).cancel(task_id):
        raise HTTPException(409, f"task {task_id} is not queued (only a queued task can be cancelled)")
    return {"id": task_id, "status": "cancelled"}


@app.post("/api/runs/{run_id}/reject")
def reject_run(run_id: str, by: str = Body(...), note: str = Body(...)) -> dict:
    try:
        state = crew_runs.reject(run_id, by, note)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"run_id": run_id, "state": state["state"], "approval": state["approval"]}


def _served(base: Path, *parts: str) -> Path:
    """A file under `base`, by plain names only (no '..', no hidden files)."""
    if any(not p or p.startswith(".") or "/" in p or "\\" in p for p in parts):
        raise HTTPException(404, "not found")
    f = base.joinpath(*parts).resolve()
    if not f.is_relative_to(base.resolve()) or not f.is_file() or f.suffix not in crew_runs.SHOWN:
        raise HTTPException(404, "not found")
    return f


@app.get("/runs/{run_id}/{name}")
def run_file(run_id: str, name: str):
    f = _served(crew_runs.RUNS, run_id, name)
    return FileResponse(f, media_type="text/html") if f.suffix == ".html" else PlainTextResponse(f.read_text(errors="replace"))


@app.get("/drafts/{folder}/{name}")
def draft_file(folder: str, name: str):
    f = _served(crew_runs.DRAFTS, folder, name)
    return PlainTextResponse(f.read_text(errors="replace"))
