# ESAM — learning embeddings, vector stores and graph queries

A slow, hands-on walk through the three ideas behind the ESAM knowledge layer:

1. **Embeddings**: turning text into vectors whose *direction* encodes meaning.
2. **Vector stores**: storing those vectors and finding the nearest ones fast.
3. **Graph queries**: following *relationships* (what connects to what), which similarity
   search cannot do.

Everything is built on a **synthetic** military light tactical vehicle (SYNTH-LTV): a
**Brake Control ECU on an RTOS** and a **Driver Digital Cluster on Linux**, linked by a SAE J1939
message (EBC1). The structure uses real **MIL-STD-881F Appendix G** WBS codes. All content below
that level is invented.

> **Coming back after a break or a reboot?** See [`HOW_TO_START.md`](HOW_TO_START.md).

## Built for the ESAM pilot's constraints

| Constraint (from `ESAM_Pilot_Implementation_v1.0.md`) | How this code handles it |
|---|---|
| No internet on the pilot network | Model loaded from a pre-staged folder; HF offline mode forced; no runtime downloads |
| LLM gateway has chat models but **no embeddings endpoint** | Local CPU embedder (`BAAI/bge-base-en-v1.5`, 768-dim) behind a swappable interface |
| PostgreSQL 16 + pgvector on VM-1 | Same Postgres 16 + pgvector locally, in Docker (`docker-compose.yml`); only the connection string differs |
| No GPU | Everything runs on CPU |
| Neo4j not available; recursive CTEs now, JanusGraph later | Graph lessons use SQL recursive CTEs, then the Gremlin equivalent |

## Layout

```
esam/
├── SESSION_HANDOFF.md        status, local credentials/DBs, resume steps, pending tasks
├── docker-compose.yml        local PostgreSQL 16 + pgvector 0.8 (127.0.0.1:5433, volume esam-pgdata)
├── esamkit/                  the reusable, swappable parts
│   ├── config.py             all settings from env vars / .env
│   ├── factory.py            the ONLY place that picks concrete backends
│   ├── baseline.py           SE YAML baseline -> vertices + edges (no DB code)
│   ├── chunking.py           documents -> heading-labelled chunks
│   ├── chat.py               OpenAI-compatible chat client (lesson 05; optional)
│   ├── embedders/
│   │   ├── base.py           Embedder interface (embed_documents / embed_query)
│   │   ├── local_st.py       sentence-transformers, from local folder, offline
│   │   └── openai_compat.py  any OpenAI-compatible /v1/embeddings endpoint
│   ├── vectorstores/
│   │   ├── base.py           VectorStore interface + model-mismatch guard
│   │   └── pgvector_store.py PostgreSQL + pgvector, HNSW + iterative filtered scans
│   └── graphstores/
│       ├── base.py           GraphStore interface
│       └── postgres_cte.py   pilot graph schema + recursive-CTE queries (commented SQL)
├── data/
│   ├── baseline/vehicle_decomposition.yaml   SE-owned structure (ESAM YAML format)
│   └── documents/*.md                        synthetic ICD / SDD / SRS documents (MIL-STD-498 names)
├── docs/reference/           MIL-STD-881F Appendix G excerpt (source of the WBS codes)
├── lessons/
│   ├── 00_setup/             install, stage the model, air-gap transfer bundle
│   ├── 01_corpus/            what we're embedding and why it's shaped this way
│   ├── 02_embeddings/        vectors and cosine similarity, by hand
│   ├── 03_vector_store/      chunk -> embed -> store -> search, and its blind spot
│   ├── 04_graph/             recursive CTEs (+ Gremlin / Cypher equivalents)
│   └── 05_hybrid/            vector entry point -> graph reach -> chat model answer
└── tests/                    pytest: pure, graph, pgvector, embedder (DB tests use database esam_test)
```

## Quick start

```bash
cd esam
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-dev.txt                 # requirements.txt + pytest
python lessons/00_setup/stage_model.py              # once, needs internet (~440 MB)
docker compose up -d --wait                         # local Postgres + pgvector

python lessons/02_embeddings/embeddings_by_hand.py
python lessons/03_vector_store/ingest.py --reset
python lessons/03_vector_store/search.py --demo
python lessons/04_graph/load_graph.py --reset
python lessons/04_graph/queries.py
python lessons/05_hybrid/hybrid.py "If we change the EBC1 message period, what is affected?"

pytest -q                                           # 24 tests
```

## Moving to the pilot / swapping backends

Nothing in `lessons/` imports a backend. Change environment variables only:

```bash
# Local Docker database (default, nothing to set)
ESAM_PG_DSN=postgresql://esam_user:esam_local_only@127.0.0.1:5433/esam_pilot

# ESAM pilot VM-1
ESAM_PG_DSN=postgresql://esam_user:***@<vm-1>:5432/esam_pilot

# If the gateway ever offers embeddings (or you host vLLM / TEI)
ESAM_EMBED_BACKEND=openai
ESAM_OPENAI_BASE_URL=https://<gateway>/v1
ESAM_OPENAI_EMBED_MODEL=<name>
```

**Changing the embedding model means re-embedding everything.** Each collection records the
model id and dimension it was built with and refuses to open with a different one
(`ModelMismatchError`). Use a new `ESAM_COLLECTION` or `ingest.py --reset`.

To add a backend, implement the interface and add one branch to `factory.py`. For a different
vector database, that's the five methods of `VectorStore`; for JanusGraph, the methods of
`GraphStore`, using the Gremlin in `lessons/04_graph/gremlin_equivalents.md`.
