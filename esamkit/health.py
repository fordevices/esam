"""
The baseline health report: what is changing is the Diff Matrix's job; this
says what is still MISSING or WRONG. Six checks (SE manual §5.2):

  1 required documents   per subsystem, from the ontology's rules
  2 interfaces           links whose receiving end has no interface document,
                         messages with no origin, confirmed links citing no ICD
  3 awaiting SE review   candidate links
  4 traceability         requirements no test verifies; tests citing unknown requirements
  5 integrity  (BLOCKS)  baseline vs ontology, files vs baseline, graph vs
                         baseline, searchable chunks vs documents
  6 roll-up              walks UP the tree: % of required documents per
                         system, variant, vehicle

Checks 1-4 and 6 report: gaps are normal mid-programme. Check 5 means the
load itself is broken, so it blocks approval.

    python -m esamkit.health       # print the report; exit 1 if anything blocks

Reads the graph, the raw baseline YAML, the documents folder and chunk
counts. It never loads the embedding model.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import Settings, settings
from .graphstores.base import GraphStore
from .ontology import Ontology, check_baseline, default_ontology


def _f(where: str, message: str, critical: bool = False) -> dict:
    return {"where": where, "message": message, "critical": critical}


def _check(cid: int, title: str, findings: list[dict], blocks: bool = False, extra: dict | None = None) -> dict:
    status = "pass" if not findings else ("blocks" if blocks else "findings")
    return {"id": cid, "title": title, "blocks_approval": blocks, "status": status,
            "findings": findings, **(extra or {})}


def health_report(graph: GraphStore, onto: Ontology, baseline_data: dict, documents_dir: Path,
                  chunks_by_artifact: dict[str, int]) -> dict:
    snap = graph.snapshot()
    V = {v["eid"]: v for v in snap["vertices"]}
    subs = {e: v for e, v in V.items() if v["label"] == "Subsystem"}
    sc = lambda eid: bool(subs.get(eid, {}).get("props", {}).get("safety_critical"))  # noqa: E731
    name = lambda eid: V.get(eid, {}).get("props", {}).get("name", eid)  # noqa: E731
    ifaces = [e for e in snap["edges"] if e["type"] == "INTERFACES_WITH"]
    linked = {e[k] for e in ifaces for k in ("from_eid", "to_eid")}
    has = {}                                          # subsystem -> {doc_type: doc id}
    for e in snap["edges"]:
        if e["type"] == "HAS_ARTIFACT":
            has.setdefault(e["from_eid"], {})[V[e["to_eid"]]["props"].get("artifact_type")] = e["to_eid"]

    # 1. Required documents, from the rules
    required = {s: onto.required_documents(v["props"], s in linked) for s, v in subs.items()}
    f1 = [_f(s, f"{name(s)} has no {t.upper()} ({'; '.join(why)})", sc(s))
          for s, req in sorted(required.items()) for t, why in req.items() if t not in has.get(s, {})]

    # 2. Interfaces
    f2 = []
    for e in ifaces:
        msg, to = e["props"].get("message") or "an interface", e["to_eid"]
        if "icd" not in has.get(to, {}):
            f2.append(_f(f"{e['from_eid']} → {to}", f"{name(to)} receives {msg} but has no interface document "
                                                     "saying how it uses it", sc(to)))
        if e["props"].get("status") == "confirmed" and not e["props"].get("source_artifact"):
            f2.append(_f(f"{e['from_eid']} → {to}", f"confirmed link for {msg} cites no interface document"))
    for msg in sorted({e["props"].get("message") for e in ifaces if e["props"].get("message")}):
        if not graph.message_origins(msg):
            f2.append(_f(msg, "no subsystem originates this message (every sender also receives it)", True))

    # 3. Awaiting SE review
    f3 = [_f(f"{e['from_eid']} → {e['to_eid']}", f"{e['props'].get('message') or 'interface'} link is a candidate"
             + (f": {e['props']['notes']}" if e["props"].get("notes") else ""), sc(e["from_eid"]) or sc(e["to_eid"]))
          for e in ifaces if e["props"].get("status") == "candidate"]

    # 4. Traceability
    reqs = {e for e, v in V.items() if v["label"] == "Requirement"}
    f4 = [_f(r["eid"], f"no test case verifies it ({r['subsystem']}, {r['document']})", sc(r["subsystem"]))
          for r in graph.untested_requirements()]
    for e, v in sorted(V.items()):
        if v["label"] == "TestCase":
            for ref in v["props"].get("verifies", []):
                if ref not in reqs:
                    f4.append(_f(e, f"verifies {ref}, which no SRS defines"))

    # 5. Integrity (blocks)
    f5 = [_f(x.where, f"baseline vs ontology: {x.message}") for x in
          onto.check_self() + check_baseline(onto, baseline_data, documents_dir) if x.level == "error"]
    listed = {a["id"]: a for a in baseline_data.get("artifacts") or []}
    graph_docs = {e for e, v in V.items() if v["label"] == "DesignArtifact"}
    for a in sorted(set(listed) - graph_docs):
        f5.append(_f(a, "listed in the baseline but not in the graph: reload the graph"))
    for a in sorted(graph_docs - set(listed)):
        f5.append(_f(a, "in the graph but no longer in the baseline: reload the graph"))
    for a, art in sorted(listed.items()):
        if (documents_dir / art.get("file", "")).exists() and not chunks_by_artifact.get(a):
            f5.append(_f(a, "listed and on disk, but has no searchable chunks: run the ingest"))
    for a in sorted(set(chunks_by_artifact) - set(listed)):
        f5.append(_f(a, f"{chunks_by_artifact[a]} searchable chunks for a document the baseline doesn't list"))
    warnings = [_f(x.where, x.message) for x in check_baseline(onto, baseline_data, documents_dir) if x.level == "warning"]

    # 6. Roll-up: walk UP from each subsystem and add its required/present to every ancestor.
    roll: dict[str, dict] = {}
    for s, req in required.items():
        present = sum(t in has.get(s, {}) for t in req)
        for a in graph.ancestors(s)[1:]:                 # [0] is the subsystem itself
            r = roll.setdefault(a["eid"], {"eid": a["eid"], "label": a["label"], "name": a["name"],
                                           "wbs_code": a["wbs_code"], "depth": 0, "required": 0, "present": 0})
            r["required"] += len(req)
            r["present"] += present
    order = {"Vehicle": 0, "VehicleFamily": 1, "Variant": 2, "System": 3}
    rollup = sorted(roll.values(), key=lambda r: (str(r["wbs_code"] or ""), order.get(r["label"], 9)))
    for r in rollup:
        r["depth"] = order.get(r["label"], 3)
        r["percent"] = round(100 * r["present"] / r["required"]) if r["required"] else 100
    f6 = [_f(r["eid"], f"{r['name']} ({r['wbs_code']}): {r['present']} of {r['required']} required documents "
                       f"({r['percent']}%)") for r in rollup if r["label"] == "System" and r["percent"] < 100]

    checks = [
        _check(1, "Required documents", f1),
        _check(2, "Interfaces", f2),
        _check(3, "Awaiting SE review", f3),
        _check(4, "Traceability", f4),
        _check(5, "Integrity", f5, blocks=True, extra={"warnings": warnings}),
        _check(6, "Roll-up", f6, extra={"rollup": rollup}),
    ]
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "blocked": any(c["status"] == "blocks" for c in checks), "checks": checks}


def report_for(graph: GraphStore, s: Settings = settings) -> dict:
    """The report against the configured baseline, documents and collection."""
    from .vectorstores.pgvector_store import chunk_counts

    return health_report(graph, default_ontology(), yaml.safe_load(s.baseline_yaml.read_text()),
                         s.documents_dir, chunk_counts(s.pg_dsn, s.collection))


def summary_line(rep: dict) -> str:
    """One line for the end of an ingest or graph load."""
    parts = []
    for c in rep["checks"]:
        if c["status"] != "pass":
            crit = sum(f["critical"] for f in c["findings"])
            parts.append(f"{c['title'][:1].lower() + c['title'][1:]} {len(c['findings'])}" + (f" ({crit} ⚠)" if crit else ""))
    head = "BLOCKED (integrity)" if rep["blocked"] else "nothing blocks"
    return f"Health: {head}; " + (", ".join(parts) or "no findings") + ". Details: python -m esamkit.health"


def main() -> None:
    from .factory import get_graph_store

    rep = report_for(get_graph_store())
    for c in rep["checks"]:
        mark = {"pass": "✓", "findings": "!", "blocks": "✗"}[c["status"]]
        label = {"pass": "pass", "findings": f"{len(c['findings'])} findings",
                 "blocks": f"{len(c['findings'])} BLOCKING"}[c["status"]]
        print(f"{mark} {c['id']}. {c['title']:<20} {label}")
        for f in c["findings"]:
            print(f"     {'⚠ ' if f['critical'] else '  '}{f['where']}: {f['message']}")
        for w in c.get("warnings", []):
            print(f"       (warning) {w['where']}: {w['message']}")
        if c["id"] == 6:
            for r in c["rollup"]:
                print(f"     {'  ' * r['depth']}{r['wbs_code'] or '':<9} {r['name']:<34} "
                      f"{r['present']}/{r['required']}  {r['percent']}%")
    print("\nBLOCKED: fix check 5 before approving." if rep["blocked"]
          else "\nNothing blocks approval. (⚠ = safety-critical)")
    sys.exit(1 if rep["blocked"] else 0)


if __name__ == "__main__":
    main()
