"""
Ingest one document file: chunk -> embed -> store, replacing its old chunks.

Lesson 03 runs this over every file in data/documents/; lesson 06 runs it on
a single newly arrived document. `facts` comes from the baseline
(Baseline.artifact_index()), so every chunk carries its artifact id, and that
id is what links it to the graph.
"""

from pathlib import Path

from .chunking import chunk_markdown
from .config import Settings, settings as default_settings
from .embedders.base import Embedder
from .vectorstores import Chunk
from .vectorstores.base import VectorStore


def make_chunks(text: str, rel: str, facts: dict, s: Settings = default_settings) -> list[Chunk]:
    pieces = chunk_markdown(text, s.chunk_size, s.chunk_overlap)
    return [
        Chunk(
            # Deterministic id: re-ingesting the same file REPLACES its
            # chunks instead of piling up duplicates.
            id=f"{facts['artifact_id']}::{i:04d}",
            text=piece,
            source_file=rel,
            chunk_index=i,
            artifact_id=facts["artifact_id"],
            metadata={k: v for k, v in facts.items() if k != "artifact_id"},
        )
        for i, piece in enumerate(pieces)
    ]


def ingest_file(path: Path, rel: str, facts: dict, emb: Embedder, store: VectorStore,
                s: Settings = default_settings) -> tuple[int, int]:
    """Returns (chunks stored, chunks removed from the previous version)."""
    chunks = make_chunks(path.read_text(), rel, facts, s)
    # Remove whatever this file had before (it may now have FEWER chunks),
    # then embed and store. Embedding is the slow part.
    removed = store.delete_source(rel)
    store.upsert(chunks, emb.embed_documents([c.text for c in chunks]))
    return len(chunks), removed
