# How to start (after a reboot or a break)

About 2 minutes. Everything runs locally; no internet needed except lesson 05's call to OpenAI.

## 1. Open a terminal in the project

```bash
cd /home/dev/Documents/kirahi/aidev/esam
source .venv/bin/activate          # prompt now starts with (.venv)
```

## 2. Start the database

```bash
docker compose up -d --wait
```

Expected: `Container esam-postgres  Healthy`. Your lesson data (28 chunks, the 20-vertex graph) is
still there; it lives in the `esam-pgdata` volume, which survives reboots.

> Docker itself starts automatically at boot (rootless Docker, user service). The **database
> container does not**, which is on purpose so it only uses resources when you're studying.

## 3. Check everything works (optional, 15 s)

```bash
pytest -q                          # expect: 45 passed
```

## 4. Study

Work through the lessons in order. For each: **read the README, then run the script.**

| # | Read | Run |
|---|---|---|
| 01 | `lessons/01_corpus/README.md` | (nothing, it's reading only) |
| 02 | `lessons/02_embeddings/README.md` | `python lessons/02_embeddings/embeddings_by_hand.py` |
| 03 | `lessons/03_vector_store/README.md` | `python lessons/03_vector_store/ingest.py --reset`<br>`python lessons/03_vector_store/search.py --demo` |
| 04 | `lessons/04_graph/README.md` + `gremlin_equivalents.md` | `python lessons/04_graph/load_graph.py --reset`<br>`python lessons/04_graph/queries.py` |
| 05 | `lessons/05_hybrid/README.md` | `python lessons/05_hybrid/hybrid.py "If we change the EBC1 message period, what is affected?"` |
| 06 | `lessons/06_missing_document/README.md` | `python lessons/06_missing_document/run_test.py` (then see **Checks** on the Cockpit Dashboard) |

Try your own questions, e.g. `python lessons/03_vector_store/search.py "your question"`.

To see the baseline in the SE Cockpit (Dashboard + Graph tabs; details in `cockpit/README.md`):

```bash
uvicorn cockpit.app:app --port 8080        # open http://127.0.0.1:8080, Ctrl+C to stop
```

To check the baseline against the ontology (the SE vocabulary in `data/ontology/`):

```bash
python -m esamkit.ontology                 # expect: 0 errors, 0 warnings
```

To look inside the database directly:

```bash
docker exec -it esam-postgres psql -U esam_user -d esam_pilot
#   \dt                                   list tables
#   SELECT id, left(content, 80) FROM esam_documents_chunks LIMIT 5;
#   \q                                    quit
```

## 5. Take notes

Jot notes per lesson as you go, anywhere you like. A simple option is a file next to the lessons
(gitignored, so it stays private):

```bash
mkdir -p notes && $EDITOR notes/lesson01.md
```

## 6. When you're ready for a session with Claude

Open Claude Code in the **parent** folder (that's where the project memory lives):

```bash
cd /home/dev/Documents/kirahi/aidev
claude
```

Claude has a reminder to ask for your **Lesson 01 notes** first. Paste them, or say where the file
is (e.g. `esam/notes/lesson01.md`). Status, credentials and pending tasks are all in
`SESSION_HANDOFF.md` if you need them.

## 7. When you're done for the day

```bash
docker compose stop                # frees the resources; data is kept
deactivate                         # leave the Python venv
```

---

### If something goes wrong

| Symptom | Fix |
|---|---|
| `Cannot connect to the Docker daemon` | `systemctl --user start docker`, then retry step 2 |
| `port is already allocated` on 5433 | Something else took the port: `ss -ltnp \| grep 5433` to see what |
| `connection refused` from a lesson script | The database isn't running; do step 2 |
| `No model found in .../models/...` | The model folder is missing; re-run `python lessons/00_setup/stage_model.py` (needs internet, ~440 MB) |
| `python: command not found` / import errors | You skipped `source .venv/bin/activate` (step 1) |
| Lesson 05 prints a prompt instead of an answer | `esam/.env` is missing the `ESAM_CHAT_*` settings; see `SESSION_HANDOFF.md` §2 |
| You want a completely fresh database | `docker compose down -v && docker compose up -d --wait`, then re-run the ingest/load steps in 03 and 04 |
