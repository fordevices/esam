"""
Run log: the LATEST result of each named check, for the Cockpit to show.

One row per check name. A new run replaces the old one, so there is no
history to manage: the Cockpit answers "when did this last run, and did it
pass?". If history is wanted later, drop the primary key and add a run id.
"""

import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS esam_runs (
    name    TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    run_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    passed  BOOLEAN NOT NULL,
    result  JSONB NOT NULL DEFAULT '{}'
)
"""


def _connect(dsn: str):
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    conn.execute(SCHEMA)
    return conn


def save_run(dsn: str, name: str, title: str, passed: bool, result: dict) -> None:
    with _connect(dsn) as conn:
        conn.execute(
            """INSERT INTO esam_runs (name, title, run_at, passed, result)
               VALUES (%s, %s, now(), %s, %s::jsonb)
               ON CONFLICT (name) DO UPDATE SET title = EXCLUDED.title, run_at = EXCLUDED.run_at,
                   passed = EXCLUDED.passed, result = EXCLUDED.result""",
            (name, title, passed, json.dumps(result, default=str)))


def last_runs(dsn: str) -> list[dict]:
    with _connect(dsn) as conn:
        return conn.execute(
            "SELECT name, title, run_at, passed, result FROM esam_runs ORDER BY name").fetchall()
