"""
Factory — the ONLY place that knows which concrete backend is in use.

Lesson scripts (and later, CrewAI tools) do:

    from esamkit.factory import get_embedder, get_vector_store
    emb = get_embedder()
    store = get_vector_store(emb)

and never import psycopg or sentence_transformers themselves. Moving from
the local Docker database to the pilot's VM-1 is just:

    ESAM_PG_DSN=postgresql://esam_user:***@<vm-1>:5432/esam_pilot

Adding another vector database later = one new class implementing
VectorStore (vectorstores/base.py) + one more branch below. Backend modules
are imported lazily inside each branch, so you only install what you use.
"""

from .config import Settings, settings as default_settings
from .embedders.base import Embedder
from .graphstores.base import GraphStore
from .vectorstores.base import VectorStore


def get_embedder(s: Settings = default_settings) -> Embedder:
    if s.embed_backend == "local":
        from .embedders.local_st import LocalSentenceTransformerEmbedder
        return LocalSentenceTransformerEmbedder(s.embed_model_dir, s.embed_model_id)
    if s.embed_backend == "openai":
        from .embedders.openai_compat import OpenAICompatEmbedder
        return OpenAICompatEmbedder(s.openai_base_url, s.openai_api_key, s.openai_embed_model)
    raise ValueError(f"Unknown ESAM_EMBED_BACKEND={s.embed_backend!r} (use 'local' or 'openai')")


def get_vector_store(embedder: Embedder, s: Settings = default_settings,
                     collection: str | None = None) -> VectorStore:
    name = collection or s.collection
    if s.vector_backend == "pgvector":
        from .vectorstores.pgvector_store import PgVectorStore
        return PgVectorStore(s.pg_dsn, name, embedder.model_id, embedder.dim)
    raise ValueError(f"Unknown ESAM_VECTOR_BACKEND={s.vector_backend!r} (supported: 'pgvector')")


def get_graph_store(s: Settings = default_settings) -> GraphStore:
    # Note: no embedder needed. The graph holds structure, not vectors.
    if s.graph_backend == "postgres":
        from .graphstores.postgres_cte import PostgresCTEGraphStore
        return PostgresCTEGraphStore(s.graph_dsn or s.pg_dsn)
    raise ValueError(f"Unknown ESAM_GRAPH_BACKEND={s.graph_backend!r} (supported: 'postgres')")
