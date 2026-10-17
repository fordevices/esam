#!/usr/bin/env python3
"""
Lesson 05: Hybrid retrieval. Vectors find the way in, the graph finds the
reach, and the chat model writes it up.

    python lessons/05_hybrid/hybrid.py "If we change the EBC1 message period, what is affected?"
    python lessons/05_hybrid/hybrid.py --show-prompt "..."   # also print the prompt sent

Prerequisites: lesson 03 ingest.py and lesson 04 load_graph.py have been run.
Without ESAM_CHAT_* configured it does everything except the final model
call, and prints the prompt instead (dry run).

The five steps:

  1. VECTOR  embed the question, find the best-matching chunks
  2. LINK    chunk.artifact_id -> graph: which SUBSYSTEM is this about?
             (this is the bridge built in lesson 03 by tagging chunks)
  3. GRAPH   impact walk from that subsystem along INTERFACES_WITH
             (restricted to a J1939 message if the question names one)
  4. VECTOR  for each reached subsystem's documents, fetch the chunk most
             relevant to the question (a vector search FILTERED by artifact)
  5. LLM     answer from that evidence only, citing artifact ids, and
             labelling candidate impacts and missing documents as such

Neither store could do this alone: step 1 can't see the engine ECM; step 3
can't read what any document says.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.baseline import load_baseline  # noqa: E402
from esamkit.chat import chat_configured, complete  # noqa: E402
from esamkit.config import settings  # noqa: E402
from esamkit.factory import get_embedder, get_graph_store, get_vector_store  # noqa: E402

SYSTEM_PROMPT = """You are a systems engineering assistant for a military ground vehicle programme.
Answer ONLY from the EVIDENCE provided. Rules:
- Cite artifact ids in square brackets, e.g. [ICD-BRK-001], for every claim taken from a document.
- An impact whose status is "candidate" is UNCONFIRMED: say so explicitly and recommend SE review.
- If an affected subsystem has no documents, say that the impact cannot be assessed from documents.
- If the evidence does not answer the question, say so. Never invent requirements or values."""


def message_in_question(question: str, messages: set[str]) -> str | None:
    """'EBC1 (PGN 61441)' matches a question that says 'EBC1' or '61441'."""
    q = question.lower()
    for m in messages:
        short = m.split(" (")[0].lower()           # "ebc1"
        pgn = m.split("PGN ")[-1].rstrip(")") if "PGN" in m else ""
        if short in q or (pgn and pgn in q):
            return m
    return None


def _owned_artifacts(baseline, subsystem_eid: str) -> list[str]:
    return [e.to_eid for e in baseline.edges
            if e.type == "HAS_ARTIFACT" and e.from_eid == subsystem_eid]


def build_prompt(question: str, entry: dict, impact: list[dict],
                 evidence: list[tuple[str, str]]) -> str:
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
    lines += ["", "EVIDENCE (most relevant chunk per document):"]
    for art, text in evidence:
        lines.append(f"[{art}]\n{text.strip()}\n")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="+")
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()
    question = " ".join(args.question)

    emb = get_embedder()
    store = get_vector_store(emb)
    graph = get_graph_store()
    baseline = load_baseline(settings.baseline_yaml)

    # 1. VECTOR: where does the question "land"?
    qvec = emb.embed_query(question)
    hits = store.search(qvec, k=3)
    if not hits:
        sys.exit("No chunks found. Run lessons/03_vector_store/ingest.py first.")
    top = hits[0]
    print(f"1. VECTOR  top hit: {top.chunk.artifact_id} chunk {top.chunk.chunk_index} "
          f"(score {top.score:.3f})")

    # 2. LINK: artifact -> owning subsystem, via the graph (Q2, ancestors).
    chain = graph.ancestors(top.chunk.artifact_id)
    sub = next((r for r in chain if r["label"] == "Subsystem"), None)
    if sub is None:
        sys.exit(f"{top.chunk.artifact_id} isn't attached to a subsystem in the graph. "
                 "Run lessons/04_graph/load_graph.py.")
    print(f"2. LINK    {top.chunk.artifact_id} belongs to {sub['eid']} ({sub['name']})")

    # 3. GRAPH: who is reachable, and from WHERE?
    #    Vector search finds the document that best matches the wording, but
    #    that may be a CONSUMER of the message (the cluster's SSDD talks about
    #    EBC1 a lot). Impact flows downstream from the PRODUCER, so if the
    #    question names a message, ask the graph where that message originates
    #    and walk from there. Similarity has no notion of direction; edges do.
    messages = {e.props["message"] for e in baseline.edges
                if e.type == "INTERFACES_WITH" and e.props.get("message")}
    msg = message_in_question(question, messages)
    start = sub["eid"]
    if msg:
        origins = [o["eid"] for o in graph.message_origins(msg)]
        if origins and start not in origins:
            print(f"3. GRAPH   {start} isn't where {msg} originates; "
                  f"re-anchoring at its origin: {origins[0]}")
            start = origins[0]
    impact = graph.impact(start, message=msg)
    print(f"3. GRAPH   impact from {start}" + (f" on {msg}" if msg else " (any message)")
          + f": {[r['eid'] + ('?' if r['status'] == 'candidate' else '') for r in impact]}")

    # 4. VECTOR again, filtered: the best chunk from each relevant document.
    #    Entry document first, then the origin's documents (if we re-anchored),
    #    then the documents of every reached subsystem.
    artifacts = ([top.chunk.artifact_id] + sorted(_owned_artifacts(baseline, start))
                 + [a for r in impact for a in r["artifacts"]])
    evidence = []
    for art in dict.fromkeys(artifacts):           # de-duplicate, keep order
        best = store.search(qvec, k=1, where={"artifact_id": art})
        if best:
            evidence.append((art, best[0].chunk.text))
    print(f"4. VECTOR  evidence from {[a for a, _ in evidence]}")

    # 5. LLM
    prompt = build_prompt(question,
                          {"subsystem": sub["eid"], "artifact": top.chunk.artifact_id,
                           "score": top.score, "origin": start},
                          impact, evidence)
    if args.show_prompt or not chat_configured():
        print("\n" + "─" * 78 + "\nPROMPT\n" + "─" * 78)
        print("[system]\n" + SYSTEM_PROMPT + "\n\n[user]\n" + prompt)
    if not chat_configured():
        print("\n(dry run: set ESAM_CHAT_BASE_URL and ESAM_CHAT_MODEL to send this to a model)")
        return
    print("\n" + "─" * 78 + "\n5. ANSWER\n" + "─" * 78)
    print(complete(SYSTEM_PROMPT, prompt))


if __name__ == "__main__":
    main()
