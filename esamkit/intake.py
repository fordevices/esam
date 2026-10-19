"""
Document intake: from "a document arrived" to "it is in the baseline and verified".

Pipeline A in the crew (crew/README.md) calls these functions through its
tools; each step is deterministic, so the agents interpret results but never
decide them:

  A1 read_header + ontology_check   what the document says it is, and whether
                                    the ontology allows it (new findings only)
  A2 review_document                what it contains: requirements or test
                                    cases, the requirements they cite, chunks
     ── STOP: an SE approves or rejects ──
  A3 ingest                         copy into data/documents/, add the
                                    baseline line, embed, reload the graph;
                                    writes a manifest so `undo` can reverse it
  A4 verify_ingestion + health_diff chunks stored, graph has it, search finds
                                    it, integrity passes, what the health
                                    report gained and lost

Adding the baseline line edits the SE's YAML as text (one line appended to
`artifacts:`), so its comments and layout survive; `undo` removes that exact
line.
"""

import re
import shutil
from pathlib import Path

import yaml

from .baseline import load_baseline
from .config import Settings, settings as default_settings
from .embedders.base import Embedder
from .graphstores.base import GraphStore
from .ingest import ingest_file, make_chunks
from .ontology import Ontology, check_baseline
from .traceability import load_traceability, read_requirements, read_test_cases
from .vectorstores.base import VectorStore

_TITLE = re.compile(r"^#\s+(?P<id>[A-Z]+-[A-Z0-9]+-\d{3})\s*[—–-]\s*(?P<name>.+?)\s*$")
_SECTION = re.compile(r"^#{2,3}\s+(?P<title>.+?)\s*$")


# ── A1 ───────────────────────────────────────────────────────────────────────

def read_header(text: str) -> dict:
    """The baseline entry the document describes itself as: its title line
    `# <ID> — <name>` and the `| Document | Type | Revision | Subsystem |` table.
    Fields it doesn't state are left out (the ontology check reports them)."""
    entry: dict = {}
    lines = text.splitlines()
    for line in lines:
        if m := _TITLE.match(line):
            entry["id"], entry["name"] = m["id"], m["name"]
            break
    for i, line in enumerate(lines):
        cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and "document" in cells and i + 2 < len(lines):
            values = [c.strip() for c in lines[i + 2].strip().strip("|").split("|")]
            row = dict(zip(cells, values))
            entry.setdefault("id", row.get("document", ""))
            for key, col in (("type", "type"), ("revision", "revision"), ("attached_to", "subsystem")):
                if row.get(col):
                    entry[key] = row[col].lower() if key == "type" else row[col]
            break
    if entry.get("id"):
        entry.setdefault("type", entry["id"].split("-")[0].lower())   # the ontology ties type to the id prefix
        entry["file"] = f"{entry['id']}.md"
    return entry


def baseline_artifacts(baseline_yaml: Path) -> list[dict]:
    return yaml.safe_load(baseline_yaml.read_text()).get("artifacts") or []


def ontology_check(onto: Ontology, baseline_yaml: Path, entry: dict) -> dict:
    """Would the baseline still pass the ontology with this entry added? Only
    the findings the new entry introduces are reported. A document already in
    the baseline is a new revision (allowed if the revision letter moves on)."""
    data = yaml.safe_load(baseline_yaml.read_text())
    arts = data.get("artifacts") or []
    current = next((a for a in arts if a.get("id") == entry.get("id")), None)
    before = {str(f) for f in check_baseline(onto, data)}
    data["artifacts"] = [a for a in arts if a is not current] + [entry]
    after = check_baseline(onto, data)
    findings = [{"level": f.level, "where": f.where, "message": f.message}
                for f in after if str(f) not in before]
    if current:
        old, new = str(current.get("revision", "")), str(entry.get("revision", ""))
        if (len(new), new) <= (len(old), old):
            findings.append({"level": "error", "where": entry["id"],
                             "message": f"already in the baseline at revision {old}; a replacement needs a later revision, not {new or 'none'}"})
    subsystem = _subsystem(data, entry.get("attached_to"))
    required = {}
    if subsystem is not None:
        has_ifaces = any(entry.get("attached_to") in (i.get("from"), i.get("to")) for i in data.get("interfaces") or [])
        required = onto.required_documents({**subsystem, "safety_critical": subsystem.get("safety_critical") is True},
                                           has_ifaces)
    dtype = onto.document_types.get(entry.get("type", ""), {})
    return {"entry": entry, "findings": findings,
            "ok": not any(f["level"] == "error" for f in findings),
            "replaces_revision": current.get("revision") if current else None,
            "document_type": {"code": entry.get("type"), "name": dtype.get("name"), "standard": dtype.get("standard")},
            "subsystem": {"id": entry.get("attached_to"), "name": (subsystem or {}).get("name"),
                          "safety_critical": bool((subsystem or {}).get("safety_critical"))},
            "required_by_rules": required.get(entry.get("type", ""), []),
            "subsystem_requires": list(required)}


def _subsystem(data: dict, sid: str | None) -> dict | None:
    for fam in (data.get("vehicle") or {}).get("families", []):
        for var in fam.get("variants", []):
            for sysm in var.get("systems", []):
                for sub in sysm.get("subsystems", []):
                    if sub.get("id") == sid:
                        return sub
    return None


# ── A2 ───────────────────────────────────────────────────────────────────────

def review_document(text: str, entry: dict, graph: GraphStore, s: Settings = default_settings) -> dict:
    """What the document contains, read deterministically, plus the graph
    context a reviewer needs: for an STD, the text of every requirement each
    test case cites, so pass criteria can be compared with the 'shall'."""
    aid, dtype = entry.get("id", "?"), entry.get("type", "")
    snap = graph.snapshot()
    reqs = {v["eid"]: v["props"] for v in snap["vertices"] if v["label"] == "Requirement"}
    out: dict = {"id": aid, "type": dtype,
                 "sections": [m["title"] for line in text.splitlines() if (m := _SECTION.match(line))],
                 "chunks": len(make_chunks(text, entry.get("file", f"{aid}.md"), {"artifact_id": aid}, s)),
                 "issues": []}
    if dtype == "std":
        tcs = read_test_cases(text, aid).vertices
        cases = []
        for tc in tcs:
            p = tc.props
            cited = [{"id": r, "exists": r in reqs, "text": reqs.get(r, {}).get("text", ""),
                      "already_verified_by": [t["eid"] for t in graph.verified_by(r) if t["document"] != aid]}
                     for r in p["verifies"]]
            cases.append({"id": p["id"], "title": p["name"], "method": p["method"],
                          "pass_criteria": p["pass_criteria"], "verifies": cited})
            if not p["verifies"]:
                out["issues"].append(f"{p['id']}: no Verifies: line")
            for c in cited:
                if not c["exists"]:
                    out["issues"].append(f"{p['id']}: cites {c['id']}, which is not a requirement in the graph")
            if not p["method"]:
                out["issues"].append(f"{p['id']}: no Method: line")
            if not p["pass_criteria"]:
                out["issues"].append(f"{p['id']}: no Pass criteria: line")
        ids = [c["id"] for c in cases]
        out["issues"] += [f"{i}: test case id used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
        covered = {r["id"] for c in cases for r in c["verifies"]}
        out["test_cases"] = cases
        out["still_untested"] = [r["eid"] for r in graph.untested_requirements()
                                 if r["subsystem"] == entry.get("attached_to") and r["eid"] not in covered]
    elif dtype == "srs":
        found = read_requirements(text, aid, entry.get("attached_to", "")).vertices
        others = {r for r, p in reqs.items() if p.get("source_artifact") != aid}
        out["requirements"] = [{"id": v.eid, "text": v.props["text"], "section": v.props["section"]} for v in found]
        ids = [v.eid for v in found]
        out["issues"] += [f"{i}: requirement id used twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
        out["issues"] += [f"{v.eid}: already specified in {reqs[v.eid]['source_artifact']}" for v in found if v.eid in others]
        out["issues"] += [f"{v.eid}: no 'shall'" for v in found if " shall " not in f" {v.props['text'].lower()} "]
        if not found:
            out["issues"].append("no requirement lines ('- **SRS-…** The software shall …') found")
    return out


# ── A3 ───────────────────────────────────────────────────────────────────────

def baseline_line(entry: dict) -> str:
    keys = ("id", "name", "type", "revision", "attached_to", "file")
    parts = [f"{k}: {yaml.safe_dump(entry[k], default_flow_style=True, width=1000).strip().removesuffix('...').strip()}"
             if k == "name" else f"{k}: {entry[k]}" for k in keys if k in entry]
    return "  - { " + ", ".join(parts) + " }"


def _append_artifact(baseline_yaml: Path, line: str) -> None:
    text = baseline_yaml.read_text()
    tops = [ln for ln in text.splitlines() if re.match(r"^[a-z_]+:", ln)]
    if not tops or tops[-1] != "artifacts:":
        raise ValueError(f"{baseline_yaml.name}: 'artifacts:' must be the last section to append to it")
    baseline_yaml.write_text(text.rstrip("\n") + "\n" + line + "\n")


def _remove_line(baseline_yaml: Path, line: str) -> bool:
    lines = baseline_yaml.read_text().splitlines()
    if line not in lines:
        return False
    lines.remove(line)
    baseline_yaml.write_text("\n".join(lines) + "\n")
    return True


def reload_graph(graph: GraphStore, s: Settings = default_settings) -> dict:
    base = load_baseline(s.baseline_yaml)
    graph.reset()
    graph.load(base)
    return load_traceability(graph, base, s.documents_dir)


def ingest(source: Path, entry: dict, emb: Embedder, store: VectorStore, graph: GraphStore,
           backup_dir: Path, s: Settings = default_settings) -> dict:
    """Put the document into the baseline and both stores. Returns the
    manifest `undo` needs. A new revision replaces the old file and line
    (both kept in `backup_dir`)."""
    target = s.documents_dir / entry["file"]
    manifest: dict = {"entry": entry, "file": str(target), "line": baseline_line(entry),
                      "previous_file": None, "previous_line": None}
    backup_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        manifest["previous_file"] = str(backup_dir / target.name)
        shutil.copy2(target, manifest["previous_file"])
    old = next((ln for ln in s.baseline_yaml.read_text().splitlines()
                if re.match(rf"^\s*- \{{\s*id:\s*{re.escape(entry['id'])}\s*,", ln)), None)
    if old:
        manifest["previous_line"] = old
        _remove_line(s.baseline_yaml, old)
    _append_artifact(s.baseline_yaml, manifest["line"])
    shutil.copy2(source, target)
    facts = load_baseline(s.baseline_yaml).artifact_index()[entry["file"]]
    manifest["chunks"], manifest["chunks_replaced"] = ingest_file(target, entry["file"], facts, emb, store, s)
    manifest["traceability"] = reload_graph(graph, s)
    return manifest


def undo(manifest: dict, store: VectorStore, graph: GraphStore, emb: Embedder | None = None,
         s: Settings = default_settings) -> dict:
    """Reverse an ingest: the baseline line, the file, its chunks and the graph.
    A replaced revision comes back (re-embedded, so `emb` is needed then)."""
    entry, target = manifest["entry"], Path(manifest["file"])
    removed_line = _remove_line(s.baseline_yaml, manifest["line"])
    removed_chunks = store.delete_source(entry["file"])
    target.unlink(missing_ok=True)
    if manifest.get("previous_line"):
        _append_artifact(s.baseline_yaml, manifest["previous_line"])
    if manifest.get("previous_file"):
        shutil.copy2(manifest["previous_file"], target)
        if emb is None:
            raise ValueError("restoring the previous revision needs the embedder")
        facts = load_baseline(s.baseline_yaml).artifact_index()[entry["file"]]
        ingest_file(target, entry["file"], facts, emb, store, s)
    return {"baseline_line_removed": removed_line, "chunks_removed": removed_chunks,
            "previous_revision_restored": bool(manifest.get("previous_file")),
            "traceability": reload_graph(graph, s)}


# ── A4 ───────────────────────────────────────────────────────────────────────

def _key(check: dict, f: dict) -> str:
    return f"{check['id']}. {check['title']} | {f['where']}: {f['message']}"


def health_diff(before: dict, after: dict) -> dict:
    b = {_key(c, f): f for c in before["checks"] for f in c["findings"]}
    a = {_key(c, f): f for c in after["checks"] for f in c["findings"]}
    roll = lambda r: {x["name"]: x["percent"] for x in next(c for c in r["checks"] if c["id"] == 6)["rollup"]}  # noqa: E731
    rb, ra = roll(before), roll(after)
    return {"closed": sorted(set(b) - set(a)), "opened": sorted(set(a) - set(b)),
            "blocked_before": before["blocked"], "blocked_after": after["blocked"],
            "rollup": [{"name": n, "before": rb.get(n), "after": ra[n]} for n in ra if rb.get(n) != ra[n]]}


def verify_ingestion(entry: dict, review: dict, manifest: dict, emb: Embedder, store: VectorStore,
                     graph: GraphStore, health_after: dict, s: Settings = default_settings) -> dict:
    """Deterministic checks that the document is really in: every check has
    `ok` and what was expected and found."""
    from .vectorstores.pgvector_store import chunk_counts

    aid, checks = entry["id"], []

    def check(name, ok, expected, found):
        checks.append({"check": name, "ok": bool(ok), "expected": expected, "found": found})

    stored = chunk_counts(s.pg_dsn, s.collection).get(aid, 0)
    check("chunks stored", stored == manifest["chunks"] == review["chunks"], review["chunks"], stored)
    owners = [r["eid"] for r in graph.ancestors(aid) if r["label"] == "Subsystem"]
    check("document attached in the graph", entry["attached_to"] in owners, entry["attached_to"], owners or "not in graph")
    snap = graph.snapshot()
    derived = {v["eid"] for v in snap["vertices"] if v["props"].get("source_artifact") == aid}
    if review.get("test_cases") is not None:
        want = {c["id"] for c in review["test_cases"]}
        check("test cases in the graph", want <= derived, sorted(want), sorted(derived))
        edges = {(e["from_eid"], e["to_eid"]) for e in snap["edges"] if e["type"] == "VERIFIES"}
        want_e = {(c["id"], r["id"]) for c in review["test_cases"] for r in c["verifies"] if r["exists"]}
        check("VERIFIES links in the graph", want_e <= edges, len(want_e), len(want_e & edges))
    if review.get("requirements") is not None:
        want = {r["id"] for r in review["requirements"]}
        check("requirements in the graph", want <= derived, sorted(want), sorted(derived))
    hits = store.search(emb.embed_query(entry.get("name", aid)), k=3)
    found = [h.chunk.artifact_id for h in hits]
    check("search by title finds it", aid in found, aid, found)
    integrity = next(c for c in health_after["checks"] if c["id"] == 5)
    check("integrity (health check 5)", integrity["status"] == "pass", "pass",
          integrity["status"] if integrity["status"] == "pass" else [f["message"] for f in integrity["findings"]])
    return {"verified": all(c["ok"] for c in checks), "checks": checks}
