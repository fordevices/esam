"""
The task list: crew work queued in PostgreSQL and done by a worker.

An SE (or the Cockpit, later) adds a task; a worker claims the oldest,
highest-priority one, runs it, and records the outcome and the run it made.
Several workers can run at once: a claim locks the row (FOR UPDATE SKIP
LOCKED), so no two take the same task.

    kind      what the worker does                      payload
    review    Pipeline A up to the SE's approval         {"file": path, "set": ["revision=A", ...]}
    approve   the SE's approval → ingest + verify        {"run": run id, "note": text}
    author    Pipeline B: SRS draft + evidence pack      {"brief": path}
    ask       a question to the three agents             {"question": text}
    env       step 0: the Environment Engineer           {}

Statuses: queued → running → done | failed; queued → cancelled. A task that
ends waiting for a person (a review awaiting approval) is `done`: its
`outcome` says what the person must do next. The worker never approves
anything: an `approve` task is the SE's own decision, recorded with who
queued it.

    python -m crew.main tasks add review work/inbox/STD-BRK-001.md
    python -m crew.main tasks list
    python -m crew.main worker            # loop;  --once: one task and stop
"""

import json
import socket
import traceback
from typing import Callable

KINDS = ("review", "approve", "author", "ask", "env")

SCHEMA = """
CREATE TABLE IF NOT EXISTS esam_tasks (
    id           BIGSERIAL PRIMARY KEY,
    kind         TEXT NOT NULL,
    payload      JSONB NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'queued',   -- queued | running | done | failed | cancelled
    priority     INTEGER NOT NULL DEFAULT 0,       -- higher first
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    environment  TEXT NOT NULL DEFAULT '',
    worker       TEXT NOT NULL DEFAULT '',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    attempts     INTEGER NOT NULL DEFAULT 0,
    run_id       TEXT NOT NULL DEFAULT '',
    outcome      TEXT NOT NULL DEFAULT '',         -- one line for people
    result       JSONB NOT NULL DEFAULT '{}',
    error        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS esam_tasks_queue ON esam_tasks (status, priority DESC, id);
"""

_COLS = ("id", "kind", "payload", "status", "priority", "created_by", "created_at", "environment", "worker",
         "started_at", "finished_at", "attempts", "run_id", "outcome", "result", "error")


class TaskQueue:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("the task list needs a PostgreSQL database (the profile has none)")
        self.dsn = dsn
        with self._conn() as c:
            c.execute(SCHEMA)

    def _conn(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def add(self, kind: str, payload: dict, created_by: str = "", priority: int = 0, environment: str = "") -> int:
        if kind not in KINDS:
            raise ValueError(f"unknown task kind {kind!r}; one of {KINDS}")
        with self._conn() as c:
            return c.execute("INSERT INTO esam_tasks (kind, payload, created_by, priority, environment) "
                             "VALUES (%s, %s::jsonb, %s, %s, %s) RETURNING id",
                             (kind, json.dumps(payload), created_by, priority, environment)).fetchone()["id"]

    def claim(self, worker: str, kinds: tuple[str, ...] = KINDS) -> dict | None:
        """Take the next queued task (highest priority, then oldest) and mark it running."""
        with self._conn() as c:
            row = c.execute(
                f"""UPDATE esam_tasks SET status = 'running', worker = %s, started_at = now(), attempts = attempts + 1
                    WHERE id = (SELECT id FROM esam_tasks WHERE status = 'queued' AND kind = ANY(%s)
                                ORDER BY priority DESC, id LIMIT 1 FOR UPDATE SKIP LOCKED)
                    RETURNING {', '.join(_COLS)}""", (worker, list(kinds))).fetchone()
        return dict(row) if row else None

    def finish(self, task_id: int, ok: bool, run_id: str = "", outcome: str = "", result: dict | None = None,
               error: str = "") -> None:
        with self._conn() as c:
            c.execute("UPDATE esam_tasks SET status = %s, finished_at = now(), run_id = %s, outcome = %s, "
                      "result = %s::jsonb, error = %s WHERE id = %s",
                      ("done" if ok else "failed", run_id, outcome[:500], json.dumps(result or {}, default=str),
                       error[-4000:], task_id))

    def cancel(self, task_id: int) -> bool:
        with self._conn() as c:
            return c.execute("UPDATE esam_tasks SET status = 'cancelled', finished_at = now() "
                             "WHERE id = %s AND status = 'queued'", (task_id,)).rowcount == 1

    def requeue(self, task_id: int) -> bool:
        """Put a failed (or stuck running) task back in the queue."""
        with self._conn() as c:
            return c.execute("UPDATE esam_tasks SET status = 'queued', worker = '', error = '' "
                             "WHERE id = %s AND status IN ('failed', 'running')", (task_id,)).rowcount == 1

    def get(self, task_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(f"SELECT {', '.join(_COLS)} FROM esam_tasks WHERE id = %s", (task_id,)).fetchone()
        return dict(row) if row else None

    def list(self, status: str | None = None, limit: int = 30) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(f"SELECT {', '.join(_COLS)} FROM esam_tasks"
                             + (" WHERE status = %s" if status else "") + " ORDER BY id DESC LIMIT %s",
                             ((status, limit) if status else (limit,))).fetchall()
        return [dict(r) for r in rows]


def worker_name() -> str:
    import os
    return f"{socket.gethostname()}:{os.getpid()}"


# A handler runs one task and returns {"ok", "run_id", "outcome", "result"}.
Handler = Callable[[dict], dict]


def process_one(queue: TaskQueue, handlers: dict[str, Handler], worker: str | None = None) -> dict | None:
    """Claim one task, run its handler, record the outcome. Returns the
    finished task, or None if the queue is empty. A handler that raises (or
    calls sys.exit) fails the task with the reason; the worker carries on."""
    task = queue.claim(worker or worker_name(), tuple(handlers))
    if task is None:
        return None
    try:
        out = handlers[task["kind"]](task)
        queue.finish(task["id"], bool(out.get("ok")), out.get("run_id", ""), out.get("outcome", ""), out.get("result"))
    except SystemExit as e:                     # the crew commands exit with a reason on a refusal
        queue.finish(task["id"], False, outcome=f"stopped: {e.code}" if not isinstance(e.code, int) else "stopped",
                     error=str(e.code))
    except Exception as e:
        queue.finish(task["id"], False, outcome=f"{type(e).__name__}: {e}"[:500], error=traceback.format_exc())
    return queue.get(task["id"])
