"""
Hybrid retrieval: vectors find the way in, the graph finds the reach and
what's MISSING, and the chat model writes it up.

Lesson 05 introduced the steps; lesson 06 added step 3b. The pipeline lives
here (not in a lesson script) so lessons, the Cockpit and agents all run the
same code.

  1. VECTOR    embed the question, find the best-matching chunks
  2. LINK      chunk.artifact_id -> graph: which SUBSYSTEM is this about?
  3. GRAPH     impact walk from that subsystem along INTERFACES_WITH
               (restricted to a J1939 message if the question names one)
  3b. COVERAGE which document TYPE would answer this, for which subsystem,
               and does the graph say it exists? (coverage.py) For a
               verification question about a named requirement, also: which
               test case VERIFIES it? (traceability.py)
  4. VECTOR    for each relevant document, the chunk most relevant to the
               question (a vector search FILTERED by artifact)
  5. LLM       answer from that evidence only, citing artifact ids, and
               saying plainly when a needed document doesn't exist
"""

from .baseline import Baseline
import re

from .coverage import doc_types_needed, mentioned_subsystems, missing_documents
from .embedders.base import Embedder
from .graphstores.base import GraphStore
from .vectorstores.base import VectorStore

SYSTEM_PROMPT = """You are a systems engineering assistant for a military ground vehicle programme.
Answer ONLY from the EVIDENCE provided. Rules:
- Cite artifact ids in square brackets, e.g. [ICD-BRK-001], for every claim taken from a document.
- An impact whose status is "candidate" is UNCONFIRMED: say so explicitly and recommend SE review.
- If an affected subsystem has no documents, say that the impact cannot be assessed from documents.
- If DOCUMENT COVERAGE or TRACEABILITY lists something MISSING, say first and plainly that the question
  cannot be answered because that document or test does not exist yet (name it). You may
  report what the other documents say, but never fill the gap from them: no invented test steps,
  values, message contents or behaviour.
- If the evidence does not answer the question, say so. Never invent requirements or values."""

DOC_TYPE_NAMES = {"std": "test description (STD)", "icd": "interface control document (ICD)",
                  "sdd": "software design description (SDD)", "srs": "software requirements specification (SRS)"}


# A requirement ID as the ontology defines it (classes.Requirement.id_pattern).
_REQ_ID = re.compile(r"\bSRS-[A-Z0-9]+-0[1-9]\d\b")


def message_in_question(question: str, messages: set[str]) -> str | None:
    """'EBC1 (PGN 61441)' matches a question that says 'EBC1' or '61441'."""
    q = question.lower()
    for m in messages:
        short = m.split(" (")[0].lower()           # "ebc1"
        pgn = m.split("PGN ")[-1].rstrip(")") if "PGN" in m else ""
        if short in q or (pgn and pgn in q):
            return m
    return None


def _owned_artifacts(baseline: Baseline, subsystem_eid: str) -> list[str]:
    return [e.to_eid for e in baseline.edges
            if e.type == "HAS_ARTIFACT" and e.from_eid == subsystem_eid]


def build_prompt(question: str, entry: dict, impact: list[dict],
                 evidence: list[tuple[str, str]], coverage: dict | None = None) -> str:
    """Pure function (no I/O) so it can be tested on its own."""
    lines = [f"QUESTION: {question}", "",
             f"ENTRY POINT (from vector search): {entry['subsystem']} "
             f"via document [{entry['artifact']}] (score {entry['score']:.3f})",
             f"IMPACT ORIGIN (from graph): {entry['origin']}", "",
             "GRAPH: subsystems reachable over interfaces (depth = hops from the impact origin)"]
    if not impact:
        lines.append("  (none)")
    for r in impact:
        docs = ", ".join(r["artifacts"]) or "NO DOCUMENTS"
        lines.append(f"  - {r['eid']} ({r['name']}): depth {r['depth']}, status {r['status']}, "
                     f"safety_critical={r['safety_critical']}, path {r['path']}, documents: {docs}")
    if coverage and coverage["needed"]:
        lines += ["", "DOCUMENT COVERAGE (from graph): this question needs "
                  + ", ".join(DOC_TYPE_NAMES.get(t, t) for t in coverage["needed"])
                  + " for " + ", ".join(coverage["focus"])]
        for m in coverage["missing"]:
            lines.append(f"  MISSING: {m['eid']} has no {DOC_TYPE_NAMES.get(m['doc_type'], m['doc_type'])}. "
                         "That document does not exist in the baseline yet.")
        if not coverage["missing"]:
            lines.append("  All needed documents exist.")
    if coverage and coverage.get("trace"):
        lines += ["", "TRACEABILITY (from graph: test cases whose Verifies: line names the requirement)"]
        for t in coverage["trace"]:
            if not t["exists"]:
                lines.append(f"  UNKNOWN: {t['requirement']} is not a requirement in any SRS in the baseline.")
            elif t["verified_by"]:
                lines += [f"  {t['requirement']} is verified by {v['eid']} ({v['method'] or 'method not stated'}) "
                          f"in [{v['document']}]" for v in t["verified_by"]]
            else:
                lines.append(f"  MISSING: no test case verifies {t['requirement']}. That test does not exist yet.")
    lines += ["", "EVIDENCE (most relevant chunk per document):"]
    for art, text in evidence:
        lines.append(f"[{art}]\n{text.strip()}\n")
    return "\n".join(lines)


def retrieve(question: str, emb: Embedder, store: VectorStore, graph: GraphStore,
             baseline: Baseline) -> dict:
    """Steps 1–4. Returns the prompt plus everything that went into it, and a
    `log` of one line per step for printing. Raises LookupError if the stores
    are empty (nothing ingested / graph not loaded)."""
    log = []

    # 1. VECTOR: where does the question "land"?
    qvec = emb.embed_query(question)
    hits = store.search(qvec, k=3)
    if not hits:
        raise LookupError("No chunks found. Run lessons/03_vector_store/ingest.py first.")
    top = hits[0]
    log.append(f"1. VECTOR  top hit: {top.chunk.artifact_id} chunk {top.chunk.chunk_index} "
               f"(score {top.score:.3f})")

    # 2. LINK: artifact -> owning subsystem, via the graph (ancestors).
    chain = graph.ancestors(top.chunk.artifact_id)
    sub = next((r for r in chain if r["label"] == "Subsystem"), None)
    if sub is None:
        raise LookupError(f"{top.chunk.artifact_id} isn't attached to a subsystem in the graph. "
                          "Run lessons/04_graph/load_graph.py.")
    log.append(f"2. LINK    {top.chunk.artifact_id} belongs to {sub['eid']} ({sub['name']})")

    # 3. GRAPH: who is reachable, and from WHERE?
    #    Vector search finds the document that best matches the wording, but
    #    that may be a CONSUMER of the message. Impact flows downstream from
    #    the PRODUCER, so if the question names a message, ask the graph where
    #    that message originates and walk from there.
    messages = {e.props["message"] for e in baseline.edges
                if e.type == "INTERFACES_WITH" and e.props.get("message")}
    msg = message_in_question(question, messages)
    start = sub["eid"]
    if msg:
        origins = [o["eid"] for o in graph.message_origins(msg)]
        if origins and start not in origins:
            log.append(f"3. GRAPH   {start} isn't where {msg} originates; "
                       f"re-anchoring at its origin: {origins[0]}")
            start = origins[0]
    impact = graph.impact(start, message=msg)
    log.append(f"3. GRAPH   impact from {start}" + (f" on {msg}" if msg else " (any message)")
               + f": {[r['eid'] + ('?' if r['status'] == 'candidate' else '') for r in impact]}")

    # 3b. COVERAGE: the subsystem the question is about is the one it NAMES,
    #     else the one the vector search landed on. Does it have the document
    #     type the question needs?
    subsystems = [{"eid": v.eid, "name": v.props.get("name", ""), "aliases": v.props.get("aliases", [])}
                  for v in baseline.vertices if v.label == "Subsystem"]
    focus = mentioned_subsystems(question, subsystems) or [sub["eid"]]
    needed = doc_types_needed(question)
    missing = missing_documents(graph, focus, needed)
    coverage = {"needed": needed, "focus": focus, "missing": missing, "trace": []}
    log.append(f"3b. COVER  needs {needed or 'no particular type'} for {focus}: "
               + ("MISSING " + ", ".join(f"{m['eid']}:{m['doc_type'].upper()}" for m in missing)
                  if missing else "all present"))
    #     A verification question that names requirements: ask the graph
    #     which test cases VERIFY each one (not just whether an STD exists).
    reqs = list(dict.fromkeys(_REQ_ID.findall(question)))
    if reqs and "std" in needed:
        known = graph.existing_ids("Requirement")
        for r in reqs:
            coverage["trace"].append({"requirement": r, "exists": r in known,
                                      "verified_by": graph.verified_by(r) if r in known else []})
        log.append("3b. TRACE  " + "; ".join(
            f"{t['requirement']}: " + ("not a known requirement" if not t["exists"] else
                                       ", ".join(v["eid"] for v in t["verified_by"]) or "NO TEST verifies it")
            for t in coverage["trace"]))

    # 4. VECTOR again, filtered: the best chunk from each relevant document.
    #    Entry document first, then the origin's and focus subsystems'
    #    documents, then the documents of every reached subsystem.
    artifacts = ([top.chunk.artifact_id]
                 + [v["document"] for t in coverage["trace"] for v in t["verified_by"]]
                 + sorted(_owned_artifacts(baseline, start))
                 + [a for f in focus for a in sorted(_owned_artifacts(baseline, f))]
                 + [a for r in impact for a in r["artifacts"]])
    evidence = []
    for art in dict.fromkeys(artifacts):           # de-duplicate, keep order
        best = store.search(qvec, k=1, where={"artifact_id": art})
        if best:
            evidence.append((art, best[0].chunk.text))
    log.append(f"4. VECTOR  evidence from {[a for a, _ in evidence]}")

    prompt = build_prompt(question,
                          {"subsystem": sub["eid"], "artifact": top.chunk.artifact_id,
                           "score": top.score, "origin": start},
                          impact, evidence, coverage)
    return {"question": question, "log": log, "entry": sub["eid"], "origin": start,
            "impact": impact, "coverage": coverage,
            "evidence": [a for a, _ in evidence], "prompt": prompt}
