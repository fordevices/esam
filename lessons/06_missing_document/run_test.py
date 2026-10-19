#!/usr/bin/env python3
"""
Lesson 06: the missing-document confidence test.

    python lessons/06_missing_document/run_test.py               # both scenarios
    python lessons/06_missing_document/run_test.py --only engine-icd
    python lessons/06_missing_document/run_test.py --keep        # leave the new documents loaded

For each scenario: ask a question whose answer lives in a document that
doesn't exist yet. The system must SAY it doesn't exist (and not make the
answer up). Then ingest the document and ask again: now the answer must come
from it, with a citation.

Afterwards the stores go back to the committed baseline (unless --keep), so
the Cockpit shows the real gaps and the test can be re-run at any time. The
result of this run replaces the last one in the run log; the Cockpit
Dashboard shows it.

Prerequisites: lessons 03 and 04 have been run. Without ESAM_CHAT_* only the
deterministic checks run (no model answers).
"""

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from esamkit.baseline import load_baseline  # noqa: E402
from esamkit.chat import chat_configured, complete  # noqa: E402
from esamkit.confidence import restore, run_scenario  # noqa: E402
from esamkit.config import PROJECT_ROOT, settings  # noqa: E402
from esamkit.factory import get_embedder, get_graph_store, get_vector_store  # noqa: E402
from esamkit.runlog import save_run  # noqa: E402
from scenarios import scenarios  # noqa: E402

RUN_NAME = "missing-document"
RUN_TITLE = "Missing-document confidence test"


def show_phase(label: str, phase: dict) -> None:
    print(f"\n  ── {label} " + "─" * (70 - len(label)))
    for line in phase["log"]:
        print("  " + line)
    if "answer" in phase:
        print("\n  ANSWER:")
        print(textwrap.indent(phase["answer"].strip(), "    │ "))
    print()
    for c in phase["checks"]:
        print(f"  {'✓' if c['ok'] else '✗'} {c['check']}  ({c['kind']})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one scenario by name")
    ap.add_argument("--keep", action="store_true", help="leave the scenario documents loaded")
    args = ap.parse_args()

    all_sc = scenarios()
    chosen = [s for s in all_sc if not args.only or s.name == args.only]
    if not chosen:
        sys.exit(f"no scenario {args.only!r}; choose from {[s.name for s in all_sc]}")

    emb = get_embedder()
    store, graph = get_vector_store(emb), get_graph_store()
    ask = complete if chat_configured() else None
    work = PROJECT_ROOT / "var" / "lesson06"
    print(f"Model: {settings.chat_model if ask else 'none (dry run: deterministic checks only)'}")

    results = []
    try:
        for sc in chosen:
            print("\n" + "═" * 78 + f"\n{sc.title}  [{sc.name}]\nQ: {sc.question}")
            r = run_scenario(sc, emb, store, graph, settings.baseline_yaml, work, ask)
            show_phase("BEFORE (document does not exist)", r["before"])
            i = r["ingest"]
            print(f"\n  ── INGEST: {i['artifact']} attached to {i['attached_to']}, {i['chunks']} chunks")
            show_phase("AFTER", r["after"])
            print(f"\n  {'PASS' if r['passed'] else 'FAIL'}: {sc.name}")
            results.append(r)
    finally:
        if not args.keep:
            restore(all_sc, store, graph, load_baseline(settings.baseline_yaml))

    passed = bool(results) and all(r["passed"] for r in results)
    save_run(settings.pg_dsn, RUN_NAME, RUN_TITLE, passed,
             {"model": settings.chat_model if ask else None, "kept": args.keep, "scenarios": results})
    print("\n" + "═" * 78)
    print(f"{'PASS' if passed else 'FAIL'}: {sum(r['passed'] for r in results)}/{len(results)} scenarios. "
          + ("Documents left loaded (--keep)." if args.keep else "Stores restored to the committed baseline.")
          + " Result saved for the Cockpit.")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
