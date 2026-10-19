#!/usr/bin/env python3
"""
Lesson 03a — Ingest the synthetic documents into a vector store.

    python lessons/03_vector_store/ingest.py            # add / refresh
    python lessons/03_vector_store/ingest.py --reset    # wipe collection first

The database comes from config: the local Docker Postgres by default, or the
pilot's VM-1 with no code change:
    ESAM_PG_DSN=postgresql://esam_user:***@<vm-1>:5432/esam_pilot python .../ingest.py

The pipeline, one document at a time:

    file ──read──► text ──chunk──► chunks ──embed──► vectors ──upsert──► store
                                     │
             baseline YAML ──────────┘  (attach artifact_id + wbs_code to each
                                         chunk, so a search hit can be traced
                                         back to its node in the graph)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.baseline import load_baseline  # noqa: E402
from esamkit.ingest import ingest_file  # noqa: E402
from esamkit.config import settings  # noqa: E402
from esamkit.factory import get_embedder, get_vector_store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete the collection before ingesting")
    args = ap.parse_args()

    emb = get_embedder()
    store = get_vector_store(emb)
    print(f"Embedder : {emb.describe()}")
    print(f"Store    : {type(store).__name__}  collection={settings.collection!r}")
    if args.reset:
        store.reset()
        print("Collection reset.")

    # document file -> {artifact_id, wbs_code, ...}, straight from the
    # SE-owned baseline. The earlier script GUESSED this link by fuzzy-matching
    # file names; here the baseline states it (`file:` on each artifact), so
    # the link is exact and an unlisted document is reported, not stored unlinked.
    index = load_baseline(settings.baseline_yaml).artifact_index()
    files = sorted(settings.documents_dir.glob("*.md"))
    print(f"Documents: {len(files)} in {settings.documents_dir}\n")

    total = 0
    for path in files:
        # Key on the path RELATIVE to the documents folder, not the bare file
        # name, so two "README.md" in different subfolders can't collide.
        rel = str(path.relative_to(settings.documents_dir))
        facts = index.get(rel)
        if facts is None:
            print(f"  ! {rel}: not listed in the baseline — skipped (add it under `artifacts:`)")
            continue

        # Chunk, embed, store; the file's old chunks are replaced (it may now
        # have FEWER). Embedding is the slow part: on CPU, about a second per
        # batch of 16 chunks for bge-base. See esamkit/ingest.py.
        n, removed = ingest_file(path, rel, facts, emb, store)
        total += n
        print(f"  ✓ {rel:18s} {facts['artifact_id']:13s} wbs {facts['wbs_code']:8s} "
              f"{n:2d} chunks  (replaced {removed})")

    print(f"\nIngested {total} chunks. Store now holds {store.count()}.")


if __name__ == "__main__":
    main()
