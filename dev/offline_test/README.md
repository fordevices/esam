# Offline test harness (TEST-ONLY — not part of the lessons)

Built while the network was too slow to download torch, the BGE model, psycopg or the
pgvector image. Lets you exercise the code paths with **zero downloads**, using only system
Python 3.10 (+ pyyaml, numpy) and the host's Postgres 14 binaries.

| File | What it fakes | What it really tests |
|---|---|---|
| `start_temp_pg.sh` | — | starts a throwaway PG14 on 127.0.0.1:55433 (never the system service) |
| `fakepg/psycopg/` | the `psycopg` driver (runs SQL via `psql`, rows via `json_agg`) | the real graph SQL + Python graph code |
| `test_graph_sql.py` | — | runs every graph query's SQL directly through psql |
| `run_with_fakes.py` | embedder (bag-of-words hash, **not semantic**) + in-memory vector store | ingest.py / search.py / hybrid.py control flow |

```bash
cd esam
dev/offline_test/start_temp_pg.sh
python3 dev/offline_test/test_graph_sql.py
PYTHONPATH=dev/offline_test/fakepg python3 lessons/04_graph/load_graph.py --reset
PYTHONPATH=dev/offline_test/fakepg python3 lessons/04_graph/queries.py --no-sql
PYTHONPATH=dev/offline_test/fakepg python3 dev/offline_test/run_with_fakes.py \
    "lessons/03_vector_store/ingest.py|--reset" \
    "lessons/05_hybrid/hybrid.py|If we change the EBC1 message period, what is affected?"
dev/offline_test/start_temp_pg.sh stop
```

Scores from the toy embedder are meaningless. Only the plumbing is being checked.
Once the real stack is installed, delete this folder or keep it as a smoke test.
