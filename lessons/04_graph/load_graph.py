#!/usr/bin/env python3
"""
Lesson 04a — Load the SE baseline into the graph tables.

    python lessons/04_graph/load_graph.py            # load (safe to repeat)
    python lessons/04_graph/load_graph.py --reset    # empty the graph first

It loads TWICE on purpose and prints the counts both times. They must be
identical. That is the idempotency fix: the earlier script had no unique
constraint on edges, so every re-run silently doubled them.

No embedding model is needed for this lesson, only Postgres.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.baseline import load_baseline  # noqa: E402
from esamkit.config import settings  # noqa: E402
from esamkit.factory import get_graph_store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    baseline = load_baseline(settings.baseline_yaml)
    print(f"Baseline: {settings.baseline_yaml.relative_to(settings.baseline_yaml.parents[2])}")
    print("  vertices by label:", dict(Counter(v.label for v in baseline.vertices)))
    print("  edges by type    :", dict(Counter(e.type for e in baseline.edges)))

    graph = get_graph_store()
    if args.reset:
        graph.reset()
        print("Graph reset.")

    first = graph.load(baseline)
    second = graph.load(baseline)
    print(f"\nAfter load #1: {first}")
    print(f"After load #2: {second}   <- must equal load #1")
    if first != second:
        sys.exit("NOT idempotent: something created duplicates.")
    print("\nIdempotent ✓. Re-loading an unchanged baseline changes nothing.")


if __name__ == "__main__":
    main()
