# ESAM learning project: session handoff

**Paused:** 2026-10-17, because the network ran at ~15–75 KB/s and couldn't finish the large
downloads. **Resume:** on a faster connection, start at [§4 Resume checklist](#4-resume-checklist).
Everything needed to continue is in this folder. Nothing depends on the internet except the
downloads listed in §4.

---

## 1. Where things are

| What | Path |
|---|---|
| This project (its own git repo, branch `main`) | `/home/dev/Documents/kirahi/aidev/esam/` |
| Parent repo (CrewAI RTOS workflow) | `/home/dev/Documents/kirahi/aidev/`. `esam/` was added to its `.gitignore` (same pattern as `projects/`) |
| ESAM pilot design docs this follows | `aidev/ESAM_Pilot_Implementation_v1.0.md`, `ESAM_Phase0_Execution_Guide.md`, `ESAM_User_Guide_SE_PM.md` |
| The earlier quick-and-dirty script (reviewed, superseded) | GitHub `fordevices/vce5500maxchenproject` → `embeddings.py` (no local copy exists) |
| MIL-STD-881F Appendix G text used for WBS codes | `docs/reference/MIL-STD-881F_AppendixG_excerpt.txt` (G.3 table + G.4.2.1.x definitions) |
| Offline test harness | `dev/offline_test/` (see its README) |

## 2. Credentials, databases, services

**There are no real secrets in this project.** Every credential below is a local,
learning-only default. Don't reuse any of them on the pilot.

| Thing | Host / port | User / password | Database | State right now |
|---|---|---|---|---|
| **Main local DB** (`docker-compose.yml`, image `pgvector/pgvector:pg16`, container `esam-postgres`) | `127.0.0.1:5433` | `esam_user` / `esam_local_only` | `esam_pilot` | **Never started.** Image not downloaded yet |
| ↳ its data volume | Docker named volume **`esam-pgdata`** | | | not created yet. Survives `docker compose down`; deleted only by `down -v` or `docker volume rm esam-pgdata` |
| ↳ connection string (default in `esamkit/config.py`) | `postgresql://esam_user:esam_local_only@127.0.0.1:5433/esam_pilot` | | | |
| **Offline test DB** (host PG14 binaries, `dev/offline_test/start_temp_pg.sh`) | `127.0.0.1:55433` | `tester` / *(no password, trust auth)* | `esamtest` | **Stopped.** Data in `esam/var/pgtest` (gitignored); `start_temp_pg.sh wipe` deletes it |
| Host system Postgres service (`postgresql`, v14) | 5432 | n/a | n/a | Installed, **inactive, never touched** this session |
| Session-scratch PG cluster (`/tmp/claude-1001/.../scratchpad/pgtest`) | was 55433 | `tester` | `esamtest` | **Stopped**, disposable; the OS may delete `/tmp`. Superseded by the offline test DB above |

Other access facts:

| | State |
|---|---|
| GitHub via **SSH** | ✅ works, authenticates as **`fordevices`**, so `git push` over `git@github.com:` will work |
| GitHub via **`gh` CLI** | ❌ keyring token invalid. Run `gh auth login -h github.com` if you want `gh` (not needed to push) |
| Git identity used for commits in this repo | `max-kirahi <smadhu@alumni.cmu.edu>` (global config) |
| OpenAI / chat keys | None used or stored by this project. `aidev/.env` exists (parent repo); this session didn't read it |
| Chat model for lesson 05 | Not configured. Set `ESAM_CHAT_BASE_URL`, `ESAM_CHAT_MODEL`, `ESAM_CHAT_API_KEY` in `esam/.env`; until then lesson 05 runs as a dry run and prints the prompt |

## 3. What was built, and what's verified

```
esamkit/              config · factory · chunking · baseline loader · chat client
  embedders/          base · local_st (BGE, offline, CPU) · openai_compat
  vectorstores/       base (+ model-mismatch guard) · pgvector_store (HNSW)
  graphstores/        base · postgres_cte (recursive CTEs, pilot schema + unique-edge fix)
data/baseline/        vehicle_decomposition.yaml (synthetic, real 881F codes)
data/documents/       ICD-BRK-001, SSDD-BRK-001, SSR-BRK-001, SSDD-DCL-001, SSR-DCL-001
lessons/00_setup/     stage_model.py + air-gap transfer README
lessons/01_corpus/    README
lessons/02_embeddings/embeddings_by_hand.py
lessons/03_vector_store/ingest.py, search.py
lessons/04_graph/     load_graph.py, queries.py, README, gremlin_equivalents.md
lessons/05_hybrid/    hybrid.py
dev/offline_test/     harness (temp PG, psycopg stand-in, toy embedder)
docker-compose.yml    local Postgres 16 + pgvector
```

| Component | Verified? | How |
|---|---|---|
| Baseline loader | ✅ | 20 vertices / 22 edges; artifact→WBS links correct |
| Chunker | ✅ | 28 chunks over 5 docs, heading-labelled, no mid-word cuts |
| Graph SQL (Q1 descendants, Q2 ancestors, Q3 impact, Q4 gaps, message origins) | ✅ | Real Postgres 14, idempotent load (load twice → same counts) |
| `load_graph.py`, `queries.py` | ✅ | End to end via the psycopg stand-in on real Postgres |
| `ingest.py`, `search.py`, `hybrid.py` control flow | ✅ (plumbing only) | Toy non-semantic embedder + in-memory store. Caught and fixed a real bug: impact must start at the message's *origin*, not wherever vector search lands |
| `docker-compose.yml` | ✅ syntax | `docker compose config` |
| `LocalSentenceTransformerEmbedder` | ❌ untested | needs torch + model |
| `PgVectorStore` (pgvector SQL, HNSW) | ❌ untested | needs pgvector image + psycopg |
| `embeddings_by_hand.py` (lesson 02) | ❌ untested | needs model |
| `stage_model.py` | ❌ untested | needs huggingface_hub + internet |
| `OpenAICompatEmbedder`, `chat.py` | ❌ untested | needs an endpoint |
| Gremlin / Cypher in `gremlin_equivalents.md` | ❌ not executed | no server; marked as such in the file |

## 4. Resume checklist

On a fast connection (~1 GB total download):

```bash
cd /home/dev/Documents/kirahi/aidev/esam
source .venv/bin/activate          # venv EXISTS but only has pip; nothing else installed yet

# 1. Python packages (~250 MB). Install torch first so pip doesn't pull the 2 GB CUDA build.
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# 2. Embedding model (~440 MB) -> esam/models/ + SHA-256 manifest + offline load test
python lessons/00_setup/stage_model.py

# 3. Local Postgres + pgvector (~150 MB image)
docker compose up -d --wait

# 4. Run the lessons in order
python lessons/02_embeddings/embeddings_by_hand.py
python lessons/03_vector_store/ingest.py --reset
python lessons/03_vector_store/search.py --demo
python lessons/04_graph/load_graph.py --reset
python lessons/04_graph/queries.py
python lessons/05_hybrid/hybrid.py "If we change the EBC1 message period, what is affected?"
```

Notes from the slow-network attempt:
* The partial torch download (134 MB of ~190 MB) was deleted; pip can't resume it. Expect a fresh download.
* `docker pull pgvector/pgvector:pg16` was cancelled part way; Docker may reuse layers it had finished.
* A stray container named `esam-pgvector-test` (port 55432) was never created. Nothing to clean up.

To test offline without any of the above, see `dev/offline_test/README.md`.

## 5. Pending tasks (in order)

1. **Run §4** and fix whatever the untested parts in §3 turn up (most likely `PgVectorStore`
   list→vector adaptation, and the `embed_raw` comparison in lesson 02 step 5).
2. **Write `lessons/02_embeddings/README.md` and `lessons/03_vector_store/README.md`.**
   Deliberately not written yet: they should explain the *actual* numbers (similarity table,
   prefix-gap results, demo search rankings) rather than guesses.
3. **Write `lessons/05_hybrid/README.md`.** Cover the five steps, why it re-anchors at the
   message origin, and a real model answer once `ESAM_CHAT_*` is set.
4. **Real tests** (`tests/`): chunking + baseline (pure Python), and graph + pgvector against the
   compose DB. Port the checks from `dev/offline_test/`.
5. **Update the top-level `README.md`.** Its layout section predates `graphstores/`, `baseline.py`,
   `chat.py`, lessons 04–05 and `dev/`.
6. **Push to GitHub.** You said you'll create a new empty repo. Then:
   `git remote add origin git@github.com:fordevices/<new-repo>.git && git push -u origin main`.
   Claude to show the commit list before pushing.
7. **Optional, decide later:** an ONNX Runtime embedder (drops the ~200 MB torch dependency, so
   a smaller air-gap bundle); a `JanusGraphStore` implementing the Gremlin from lesson 04.
8. **Raise with the programme:** `ESAM_Pilot_Implementation_v1.0.md` §3.1 says *"No on-premises
   model weights"*, but a local embedder *is* model weights (~440 MB, MIT). Get an exception, or
   request an embeddings route on the IL5 API Gateway (then set `ESAM_EMBED_BACKEND=openai`).

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
