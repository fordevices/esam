"""
ESAM SE Cockpit (learning build) — the web front end SEs use.

The Phase 0 Execution Guide (Step 12) sketches the pilot Cockpit. This build
grows it one tab at a time, on top of esamkit instead of its own SQL:

    Dashboard   graph counts, document coverage per subsystem, interfaces,
                the last run of each check (e.g. lesson 06's confidence test)
    Graph       the baseline as a tree, a tree with interface arcs, a network

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

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from esamkit import runlog
from esamkit.config import settings
from esamkit.factory import get_graph_store
from esamkit.graphstores.base import GraphStore

PAGE = Path(__file__).parent / "static" / "index.html"

# Which document types every subsystem is checked for on the Dashboard.
# A stand-in for the SE-owned rule table the health report (manual §5.2) will
# read from the baseline; until then it is the same list for every subsystem.
DOC_TYPES = ["icd", "sdd", "srs", "std"]

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
    # The run log lives in the main database; tests point this elsewhere.
    return settings.pg_dsn


def last_runs(dsn: str) -> list[dict]:
    """The latest run of each check. A missing or unreachable run log just
    means "never run"; it must not take the Dashboard down."""
    try:
        return runlog.last_runs(dsn)
    except Exception:
        return []


def collect(graph: GraphStore, doc_types: list[str], runs: list[dict] | None = None) -> dict:
    """Everything the page draws, in one payload (also what a snapshot embeds)."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "graph": graph.snapshot(),
        "doc_types": doc_types,
        "gaps": {t: [r["eid"] for r in graph.artifact_gaps(t)] for t in doc_types},
        "runs": runs or [],
    }


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


@app.get("/api/cockpit")
def cockpit(types: str = Query(",".join(DOC_TYPES), description="comma-separated document types"),
            graph: GraphStore = Depends(get_graph), runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    doc_types = [t.strip().lower() for t in types.split(",") if t.strip()]
    return collect(graph, doc_types, last_runs(runs_dsn))
