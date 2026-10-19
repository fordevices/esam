"""
ESAM SE Cockpit (learning build) — the web front end SEs use.

The Phase 0 Execution Guide (Step 12) sketches the pilot Cockpit. This build
grows it one tab at a time, on top of esamkit instead of its own SQL:

    Dashboard   graph counts, document coverage per subsystem, interfaces,
                the last run of each check (e.g. lesson 06's confidence test)
    Graph       the baseline as a tree, a tree with interface arcs, a network
    Ontology    what's in the SE vocabulary file, and whether the baseline passes it

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

import yaml

from esamkit import runlog
from esamkit.config import PROJECT_ROOT, settings
from esamkit.factory import get_graph_store
from esamkit.graphstores.base import GraphStore
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
    # The run log lives in the main database; tests point this elsewhere.
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


def collect(graph: GraphStore, doc_types: list[str], runs: list[dict] | None = None) -> dict:
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


@app.get("/api/ontology")
def ontology() -> dict:
    return ontology_payload()


@app.get("/api/cockpit")
def cockpit(types: str = Query(",".join(DOC_TYPES), description="comma-separated document types"),
            graph: GraphStore = Depends(get_graph), runs_dsn: str = Depends(get_runs_dsn)) -> dict:
    doc_types = [t.strip().lower() for t in types.split(",") if t.strip()]
    return collect(graph, doc_types, last_runs(runs_dsn))
