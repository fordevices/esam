#!/usr/bin/env python3
"""
Lesson 03b — Search the vector store.

    python lessons/03_vector_store/search.py "what happens if brake data is lost?"
    python lessons/03_vector_store/search.py --artifact SSR-DCL-001 "telltale timing"
    python lessons/03_vector_store/search.py --demo      # runs the lesson's question set

What a search is, mechanically:
  1. Embed the QUESTION with embed_query() (BGE: with the query instruction).
  2. Ask the store for the k stored vectors with the highest cosine
     similarity to it.
  3. Get back the chunk text plus its metadata. The artifact_id and wbs_code
     tell you where in the vehicle baseline each hit came from.

No keywords are involved anywhere. The store doesn't know what "brake" means;
it only compares directions of arrows.
"""

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.factory import get_embedder, get_vector_store  # noqa: E402

DEMO = [
    # 1. Answer stated in different words than the question.
    "What does the driver see when anti-lock braking fails?",
    # 2. Rationale lives in a design doc, not a requirement.
    "Why is Linux acceptable for the cluster but not for the brake controller?",
    # 3. Military-specific wording.
    "How does the display behave when the crew is using night vision goggles?",
    # 4. An IMPACT question. Watch what the vector store CANNOT tell you here.
    "If we change the EBC1 message period, which subsystems are affected?",
]


def show(store, emb, question: str, k: int, where: dict | None) -> None:
    print("\n" + "─" * 78)
    print(f"Q: {question}" + (f"   [filter {where}]" if where else ""))
    print("─" * 78)
    hits = store.search(emb.embed_query(question), k=k, where=where)
    if not hits:
        print("  (no hits — did you run ingest.py?)")
    for rank, h in enumerate(hits, 1):
        m = h.chunk.metadata
        print(f"  #{rank}  score {h.score:.3f}  {h.chunk.artifact_id}  "
              f"(wbs {m.get('wbs_code', '?')}, {m.get('artifact_type', '?')})  chunk {h.chunk.chunk_index}")
        body = " ".join(h.chunk.text.split())
        print(textwrap.indent(textwrap.fill(body[:300] + ("…" if len(body) > 300 else ""), 72), "       "))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*")
    ap.add_argument("-k", type=int, default=4)
    ap.add_argument("--artifact", help="only search chunks of this artifact id")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    emb = get_embedder()
    store = get_vector_store(emb)
    where = {"artifact_id": args.artifact} if args.artifact else None

    questions = DEMO if args.demo or not args.question else [" ".join(args.question)]
    for q in questions:
        show(store, emb, q, args.k, where)

    if args.demo:
        print("""
About question 4: the store returns chunks that TALK about EBC1, mostly the
ICD and the requirements. It cannot list "every subsystem that consumes EBC1",
because that fact isn't written in any single chunk. It's a RELATIONSHIP spread
across the baseline (brake ECU -> backbone -> cluster, and an unconfirmed
link to the engine ECM). The engine ECM has no documents at all, so it can
never appear here. Following relationships like that is lesson 04's job (the graph).""")


if __name__ == "__main__":
    main()
