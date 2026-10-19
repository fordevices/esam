# ESAM SE Cockpit (learning build)

The web front end Systems Engineers use. It follows the SE Cockpit sketched in
`ESAM_Phase0_Execution_Guide.md` (Step 12), but is built on `esamkit` (the tested
graph and vector stores) rather than its own SQL, and is grown one tab at a time.

| Tab | Shows | Status |
|---|---|---|
| **Dashboard** | Node/link counts; document coverage per subsystem (missing documents on safety-critical subsystems in red); interfaces with confirmed/candidate status | ✅ |
| **Graph** | The baseline three ways: **Tree** (collapsible outline), **Tree + interfaces** (left-to-right tree with the J1939 links drawn as arcs across it), **Network** (force-directed, draggable). Click any node for where it sits, its properties, what it contains, its interfaces and its document gaps. | ✅ |
| Health report | The six checks in the SE manual §5.2 | planned (B6) |
| Query | Natural-language questions via the lesson 05 hybrid | planned |
| Baseline | Diff Matrix, Approve and Lock | planned |

## Run it

```bash
cd /home/dev/Documents/kirahi/aidev/esam
source .venv/bin/activate
docker compose up -d --wait                  # the database
uvicorn cockpit.app:app --port 8080          # then open http://127.0.0.1:8080
```

The graph must be loaded first (`python lessons/04_graph/load_graph.py`). It already is if you've done lesson 04.

## Hand someone a snapshot

```bash
python -m cockpit.export_snapshot            # -> var/cockpit/ESAM_Cockpit_snapshot.html
```

This writes one HTML file with the current graph baked in. It opens in any browser with no server, database
or network, and it does not update: re-export after each ingest.

## API

| Route | Returns |
|---|---|
| `GET /` | The page |
| `GET /api/health` | `{"status": "ok", "vertices": n, "edges": m}`, or 503 if the database is unreachable |
| `GET /api/cockpit?types=icd,ssdd,ssr,std` | Everything the page draws: the whole graph by entity id, plus which subsystems lack each document type |

## Air-gap notes

The page (`static/index.html`) loads nothing from the internet: no CDN, no web fonts, all script inline.
New dependencies: `fastapi` and `uvicorn` (pure Python wheels, a few MB); add them to the air-gap bundle.

## Differences from the Phase 0 guide's sketch (on purpose)

1. **No SQL written by the LLM is executed.** The guide's `/api/query` runs whatever SQL GPT returns. The Query
   tab, when built, will have the model choose among the fixed, tested `GraphStore` / `VectorStore` calls instead.
2. **The tree is rebuilt correctly.** The guide's tree query returns each node as its own parent
   (`e.from_id AS parent_id` where `from_id` is the child).
3. **Approve will require authentication** and won't `git push` straight from a web request.
4. **One code path for queries:** the Cockpit calls `esamkit`, so the lessons, the tests and the Cockpit run the same SQL.
