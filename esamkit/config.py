"""
Configuration — every knob comes from an environment variable with a safe
local default. Nothing here imports a backend library, so reading config is
always cheap and never triggers a network call or a model load.

Copy `.env.example` to `.env` and edit, or export the variables in your shell.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Root of the esam/ project (this file lives in esam/esamkit/config.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, '#' comments. Existing env wins.

    We avoid the python-dotenv dependency on purpose: one less wheel to carry
    across the air gap.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")


def _path(env_name: str, default: str) -> Path:
    """Read a path from env; relative paths are taken relative to esam/."""
    p = Path(os.getenv(env_name, default))
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    # ── Embeddings ──────────────────────────────────────────────────────────
    # "local"  = sentence-transformers model loaded from a folder on disk
    # "openai" = any OpenAI-compatible /v1/embeddings endpoint (vLLM, TEI,
    #            Ollama, or real OpenAI if the gateway ever enables it)
    embed_backend: str = os.getenv("ESAM_EMBED_BACKEND", "local")

    # Hugging Face model id. Used (a) by lessons/00_setup/stage_model.py to
    # download the model ONCE on an internet-connected machine, and (b) as the
    # model's identity, stored next to every vector so stores never mix models.
    embed_model_id: str = os.getenv("ESAM_EMBED_MODEL_ID", "BAAI/bge-base-en-v1.5")

    # Folder that holds the pre-staged model files. At runtime we load ONLY
    # from here — never from the internet.
    embed_model_dir: Path = _path("ESAM_EMBED_MODEL_DIR", "models/bge-base-en-v1.5")

    # Only for embed_backend="openai".
    openai_base_url: str = os.getenv("ESAM_OPENAI_BASE_URL", "")
    openai_api_key: str = os.getenv("ESAM_OPENAI_API_KEY", "")
    openai_embed_model: str = os.getenv("ESAM_OPENAI_EMBED_MODEL", "")

    # ── Vector store ────────────────────────────────────────────────────────
    # "pgvector" = PostgreSQL + pgvector: the local Docker container here
    # (docker-compose.yml), VM-1 on the pilot. Other backends can be added
    # behind the VectorStore interface; see factory.py.
    vector_backend: str = os.getenv("ESAM_VECTOR_BACKEND", "pgvector")
    collection: str = os.getenv("ESAM_COLLECTION", "esam_documents")

    # libpq connection string. Default = the local docker-compose database.
    # On the pilot, point this at VM-1 (and keep the password in .env, not here).
    pg_dsn: str = os.getenv(
        "ESAM_PG_DSN", "postgresql://esam_user:esam_local_only@127.0.0.1:5433/esam_pilot")

    # ── Graph store ─────────────────────────────────────────────────────────
    # "postgres" = graph_vertices/graph_edges + recursive CTEs (pilot design).
    # A JanusGraph/Gremlin backend would be a new GraphStore class + a new
    # value here (see lessons/04_graph/gremlin_equivalents.md).
    graph_backend: str = os.getenv("ESAM_GRAPH_BACKEND", "postgres")
    # Defaults to the same database as the vectors, as on the pilot.
    graph_dsn: str = os.getenv("ESAM_GRAPH_DSN", "")

    # ── Chat model (lesson 05) ──────────────────────────────────────────────
    # Any OpenAI-compatible chat endpoint, e.g. the IL5 API Gateway. If unset,
    # lesson 05 prints the prompt it WOULD send instead of calling a model.
    chat_base_url: str = os.getenv("ESAM_CHAT_BASE_URL", "")
    chat_api_key: str = os.getenv("ESAM_CHAT_API_KEY", "")
    chat_model: str = os.getenv("ESAM_CHAT_MODEL", "")

    # ── Data ────────────────────────────────────────────────────────────────
    baseline_yaml: Path = _path("ESAM_BASELINE_YAML", "data/baseline/vehicle_decomposition.yaml")
    documents_dir: Path = _path("ESAM_DOCUMENTS_DIR", "data/documents")
    # The SE-owned vocabulary the baseline is checked against (esamkit/ontology.py).
    ontology_yaml: Path = _path("ESAM_ONTOLOGY_YAML", "data/ontology/esam_ontology.yaml")

    # ── Chunking ────────────────────────────────────────────────────────────
    chunk_size: int = int(os.getenv("ESAM_CHUNK_SIZE", "600"))       # characters
    chunk_overlap: int = int(os.getenv("ESAM_CHUNK_OVERLAP", "80"))  # characters


settings = Settings()
