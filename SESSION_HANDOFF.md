# ESAM learning project: session handoff

**Status (2026-10-18):** resumed on a fast connection. Everything is installed, every lesson
runs end to end on the real stack, and **48/48 tests pass**. What's left is in
[§5 Pending tasks](#5-pending-tasks-in-order): mainly pushing to GitHub and connecting a chat model.

History: paused 2026-10-17 at ~15–75 KB/s, resumed 2026-10-18 at ~8.5 MB/s.

## ▶ Next session: start here

Day-to-day restart steps for the user are in [`HOW_TO_START.md`](HOW_TO_START.md). The user may keep notes in `esam/notes/` (gitignored).

1. **Ask the user for their notes on Lesson 01** (`lessons/01_corpus/README.md`). They're working
   through the lessons in order (01 → 05) and will bring feedback. Fold those notes into the
   lesson or corpus before moving on.
2. Bring the database back: `docker compose up -d --wait` (stopped at the end of the 2026-10-18
   session to save resources; data is kept in volume `esam-pgdata`).
3. Sanity check: `source .venv/bin/activate && pytest -q` (expect 48 passed).

---

## 1. Where things are

| What | Path |
|---|---|
| This project (its own git repo, branch `main`) | `/home/dev/Documents/kirahi/aidev/esam/` |
| Parent repo (CrewAI RTOS workflow) | `/home/dev/Documents/kirahi/aidev/`. `esam/` was added to its `.gitignore` (same pattern as `projects/`) |
| ESAM pilot design docs this follows | `aidev/ESAM_Pilot_Implementation_v1.0.md`, `ESAM_Phase0_Execution_Guide.md`, `ESAM_User_Guide_SE_PM.md` |
| The earlier quick-and-dirty script (reviewed, superseded) | GitHub `fordevices/vce5500maxchenproject` → `embeddings.py` (no local copy exists) |
| MIL-STD-881F Appendix G text used for WBS codes | `docs/reference/MIL-STD-881F_AppendixG_excerpt.txt` (G.3 table + G.4.2.1.x definitions) |

## 2. Credentials, databases, services

**There are no real secrets in this project.** Every credential below is a local,
learning-only default. Don't reuse any of them on the pilot.

| Thing | Host / port | User / password | Database | State right now |
|---|---|---|---|---|
| **Main local DB** (`docker-compose.yml`, image `pgvector/pgvector:pg16`, container `esam-postgres`) | `127.0.0.1:5433` | `esam_user` / `esam_local_only` | `esam_pilot` (lesson data) | **Running.** PostgreSQL 16.15, pgvector 0.8.7. Holds 28 chunks + the 20-vertex graph |
| ↳ test database (pytest) | same server | same | `esam_test` | Created by `tests/conftest.py`; tests never touch `esam_pilot` |
| ↳ its data volume | Docker named volume **`esam-pgdata`** | | | Exists. Survives `docker compose down`; deleted only by `down -v` or `docker volume rm esam-pgdata` |
| ↳ connection string (default in `esamkit/config.py`) | `postgresql://esam_user:esam_local_only@127.0.0.1:5433/esam_pilot` | | | |
| Host-installed Postgres 14 (apt) | n/a | n/a | n/a | **Removed 2026-10-18** (`apt purge` + `autoremove`; data cluster deleted). Docker is the only Postgres now. The `postgres` system user and group were also removed. |

Other access facts:

| | State |
|---|---|
| GitHub via **SSH** | ✅ works, authenticates as **`fordevices`**, so `git push` over `git@github.com:` will work |
| GitHub via **`gh` CLI** | ❌ keyring token invalid. Run `gh auth login -h github.com` if you want `gh` (not needed to push) |
| Git identity used for commits in this repo | `max-kirahi <smadhu@alumni.cmu.edu>` (global config) |
| OpenAI key | Copied from `aidev/.env` (`OPENAI_API_KEY`) into **`esam/.env`** as `ESAM_CHAT_API_KEY` (gitignored, mode 600). Never printed |
| Chat model for lesson 05 | **OpenAI `gpt-5.1`** (`https://api.openai.com/v1`), the same model as the pilot. Lesson 05 sends the synthetic documents to OpenAI |
| ⚠ `aidev/.env` line 2 | A bare value with no `NAME=`. Sourcing the file printed a Google API key into the 2026-10-18 session transcript. **Rotate that Google key and fix the line** |

## 3. What was built, and what's verified

```
esamkit/              config · factory · chunking · baseline loader · chat client
  embedders/          base · local_st (BGE, offline, CPU) · openai_compat
  vectorstores/       base (+ model-mismatch guard) · pgvector_store (HNSW)
  graphstores/        base · postgres_cte (recursive CTEs, pilot schema + unique-edge fix)
data/baseline/        vehicle_decomposition.yaml (synthetic, real 881F codes)
data/documents/       ICD-BRK-001, SDD-BRK-001, SRS-BRK-001, SDD-DCL-001, SRS-DCL-001
lessons/00_setup/     stage_model.py + air-gap transfer README
lessons/01_corpus/    README
lessons/02_embeddings/embeddings_by_hand.py
lessons/03_vector_store/ingest.py, search.py
lessons/04_graph/     load_graph.py, queries.py, README, gremlin_equivalents.md
lessons/05_hybrid/    hybrid.py
docker-compose.yml    local Postgres 16 + pgvector
```

| Component | Verified? | How |
|---|---|---|
| Baseline loader, chunker | ✅ | `tests/test_pure.py` |
| Graph store: all queries, idempotent load, message origins, gaps | ✅ | `tests/test_graph_store.py` + lesson 04 on PG 16 |
| `PgVectorStore`: ordering, scores, upsert/delete, column + metadata filters, model guard, name validation, HNSW used | ✅ | `tests/test_pgvector_store.py` + `EXPLAIN` |
| `LocalSentenceTransformerEmbedder`: 768-dim, normalized, deterministic, query instruction applied | ✅ | `tests/test_embedder.py` |
| `stage_model.py`: download, manifest, offline load | ✅ | 11 files / 439 MB, manifest OK |
| Lessons 02, 03, 04, 05 (dry run) | ✅ | Real output captured in each lesson README |
| `chat.py` (lesson 05 with a real model) | ✅ | GPT-5.1 answer captured in the lesson 05 README |
| `OpenAICompatEmbedder` | ❌ untested | No embeddings endpoint in use |
| Gremlin / Cypher in `gremlin_equivalents.md` | ❌ not executed | No server; marked as such in the file |

## 4. Resume checklist

Everything is installed. To pick up again:

```bash
cd /home/dev/Documents/kirahi/aidev/esam
source .venv/bin/activate
docker compose up -d --wait          # if the container isn't running (e.g. after a reboot)
pytest -q                            # expect 48 passed
```

To rebuild from scratch on another machine (needs ~1 GB of downloads), see the Quick start in
`README.md` and the air-gap bundle steps in `lessons/00_setup/README.md`.

## 5. Pending tasks (in order)

Done 2026-10-18: install, stage model, run lessons 02–05, lesson 02/03/05 READMEs, tests,
top-level README update; also turned on pgvector 0.8 iterative scans for filtered search.

1. ~~Push to GitHub~~ Done 2026-10-18: `git@github.com:fordevices/esam.git`, branch `main` tracks `origin/main`.
2. ~~Connect a chat model for lesson 05~~ Done: OpenAI gpt-5.1.
2a. **Rotate the Google API key exposed from `aidev/.env` line 2**, and fix that line.
3. **Optional:** an ONNX Runtime embedder (drops the ~200 MB torch dependency, so a smaller
   air-gap bundle); a `JanusGraphStore` running the Gremlin from lesson 04.
   (`dev/offline_test/` was deleted 2026-10-18 along with the host Postgres it relied on; `tests/` covers it.)
4. **Raise with the programme:** `ESAM_Pilot_Implementation_v1.0.md` §3.1 says *"No on-premises
   model weights"*, but a local embedder *is* model weights (~440 MB, MIT). Get an exception, or
   request an embeddings route on the IL5 API Gateway (then set `ESAM_EMBED_BACKEND=openai`).

### Known issue: intermittent crash at the end of pytest (2026-10-19)
Twice in ~15 runs, `pytest -q` printed a native "Fatal Python error" dump (module list: torch, psycopg_binary,
scipy, sklearn) after the tests had run. 13 runs were clean (49 passed, exit 0), including 6 back-to-back
with `PYTHONFAULTHANDLER=1`, which never reproduced it. Likely an interpreter-shutdown race between torch's
threads and psycopg's C extension. If it recurs, capture the head of the output (`grep -A30 "Fatal Python error"`)
and try `pytest -p no:faulthandler` or closing the store connections in fixture teardown.

## 6. Decisions made this session (don't re-litigate)

| Decision | Why |
|---|---|
| Folder/project name **`esam`** | Matches the ESAM docs ("emam" was a typo) |
| Example: **Brake Control ECU (RTOS) + Driver Digital Cluster (Linux)**, military light tactical vehicle "SYNTH-LTV", linked by J1939 **EBC1 (PGN 61441)** | Hard vs. soft real-time contrast, plus a real cross-system interface for graph questions |
| WBS: brakes → **1.1.1.5 Suspension/Steering** (G.4.2.1.5a); cluster + CAN backbone → **1.1.1.6 Vehicle Electronics** (G.4.2.1.6b,c); engine → **1.1.1.7** | Verified against the 881F text (excerpt in `docs/reference/`) |
| **PostgreSQL + pgvector only, ChromaDB removed** | Matches the pilot. Postgres runs in Docker to avoid the host-install problems you had before |
| Host port **5433**, named volume `esam-pgdata` | Never clashes with the host Postgres; data persists |
| Embeddings: **local `BAAI/bge-base-en-v1.5`, 768-dim, CPU, loaded from `models/` with HF offline mode forced** | Pilot gateway has chat but no embeddings; air-gapped |
| Correct BGE usage: instruction prefix on **queries only** | The old script used E5-style `query:`/`passage:` prefixes |
| Graph = pilot schema (`graph_vertices`/`graph_edges`) + **recursive CTEs**; Gremlin shown as the JanusGraph future; **no Neo4j** | Pilot doc: Neo4j not allowed (IT constraint) |
| Every backend behind an interface + `factory.py` | You asked for everything to be modular |

## 7. Fixes over the earlier `embeddings.py` (for reference)

1. Wrong BGE prefixes → correct query instruction only.
2. Downloads model at runtime → local folder plus forced offline mode.
3. `ON CONFLICT DO NOTHING` with no unique edge constraint (every re-run duplicated edges) → unique index plus upsert.
4. IVFFlat index built on an empty table (bad recall, can return < k rows) → HNSW.
5. Hard-coded 768 dimensions, model not recorded → dimension from the model, plus a model-mismatch guard.
6. Invented subsystem WBS codes (`"<system>.sub"`) → derived by graph hop.
7. Doc↔artifact link guessed from file names → explicit `file:` in the baseline.
8. Graph "queries" were Python loops over a JSON copy → real recursive CTEs.
9. Missing documents folder crashed the run log; same-name files in subfolders overwrote each other; default password in code → all fixed.
