#!/usr/bin/env python3
"""
Lesson 05: Hybrid retrieval. Vectors find the way in, the graph finds the
reach, and the chat model writes it up.

    python lessons/05_hybrid/hybrid.py "If we change the EBC1 message period, what is affected?"
    python lessons/05_hybrid/hybrid.py --show-prompt "..."   # also print the prompt sent

Prerequisites: lesson 03 ingest.py and lesson 04 load_graph.py have been run.
Without ESAM_CHAT_* configured it does everything except the final model
call, and prints the prompt instead (dry run).

The steps (the code is in esamkit/hybrid.py, so the Cockpit and agents run it too):

  1. VECTOR  embed the question, find the best-matching chunks
  2. LINK    chunk.artifact_id -> graph: which SUBSYSTEM is this about?
             (this is the bridge built in lesson 03 by tagging chunks)
  3. GRAPH   impact walk from that subsystem along INTERFACES_WITH
             (restricted to a J1939 message if the question names one)
  3b. COVER  does the document type the question needs exist? (lesson 06)
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
from esamkit.hybrid import SYSTEM_PROMPT, retrieve  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="+")
    ap.add_argument("--show-prompt", action="store_true")
    args = ap.parse_args()
    question = " ".join(args.question)

    emb = get_embedder()
    try:
        r = retrieve(question, emb, get_vector_store(emb), get_graph_store(),
                     load_baseline(settings.baseline_yaml))
    except LookupError as e:
        sys.exit(str(e))
    print("\n".join(r["log"]))

    if args.show_prompt or not chat_configured():
        print("\n" + "─" * 78 + "\nPROMPT\n" + "─" * 78)
        print("[system]\n" + SYSTEM_PROMPT + "\n\n[user]\n" + r["prompt"])
    if not chat_configured():
        print("\n(dry run: set ESAM_CHAT_BASE_URL and ESAM_CHAT_MODEL to send this to a model)")
        return
    print("\n" + "─" * 78 + "\n5. ANSWER\n" + "─" * 78)
    print(complete(SYSTEM_PROMPT, r["prompt"]))


if __name__ == "__main__":
    main()
