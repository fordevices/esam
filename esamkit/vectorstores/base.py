"""
The VectorStore interface.

A vector store keeps (text, vector, metadata) rows and answers one question
fast: "which stored vectors point in the most similar direction to THIS
vector?" That's a nearest-neighbour search.

The backend today is PostgreSQL + pgvector. Any other backend would implement
the same five methods, so lesson scripts and agent tools never import a
backend directly — they ask `esamkit.factory.get_vector_store()` and get
whichever one config selects.

The model guard
---------------
When a collection is first created it records the embedder's model_id and
dim. Every later open checks them. If you switch models without re-embedding,
you get a loud ModelMismatchError instead of silently wrong search results.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """One retrievable piece of a document."""

    id: str                 # stable id, e.g. "ICD-BRK-001::0003"
    text: str
    source_file: str        # path relative to data/documents/
    chunk_index: int
    # Graph link: which DesignArtifact node this chunk belongs to (if known).
    # This is the bridge between "vector world" and "graph world" (lesson 05).
    artifact_id: str = ""
    metadata: dict = field(default_factory=dict)  # anything else: wbs_code, title...


@dataclass
class Hit:
    chunk: Chunk
    # Cosine similarity in [-1, 1]; higher = more similar. Backends convert
    # their native "distance" into this (pgvector: 1 - (a <=> b)) so scores
    # mean the same thing whatever the backend.
    score: float


class ModelMismatchError(RuntimeError):
    pass


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Insert or replace chunks (matched by Chunk.id)."""

    @abstractmethod
    def delete_source(self, source_file: str) -> int:
        """Remove every chunk from one source file (for clean re-ingestion).
        Returns how many were removed."""

    @abstractmethod
    def search(self, query_vector: list[float], k: int = 5,
               where: dict | None = None) -> list[Hit]:
        """Return the k most similar chunks, best first.
        `where` is an optional exact-match metadata filter, e.g.
        {"artifact_id": "ICD-BRK-001"}."""

    @abstractmethod
    def count(self) -> int:
        """Number of stored chunks."""

    @abstractmethod
    def reset(self) -> None:
        """Delete the whole collection (lessons use this to start clean)."""

    # ── shared helper ───────────────────────────────────────────────────────
    @staticmethod
    def check_model(collection: str, stored_id: str, stored_dim: int,
                    model_id: str, dim: int) -> None:
        if stored_id != model_id or int(stored_dim) != int(dim):
            raise ModelMismatchError(
                f"Collection {collection!r} was built with {stored_id} "
                f"({stored_dim}-dim) but the current embedder is {model_id} "
                f"({dim}-dim). Vectors from different models cannot be "
                "compared. Re-ingest into a new collection (ESAM_COLLECTION) "
                "or reset this one."
            )
