"""
Usage ledger: what each run cost, in calls, tokens and time.

One row per event: an LLM call (model, agent, tokens), a tool call (tool,
duration, ok) or a whole run (duration, totals). Written to PostgreSQL
(`esam_usage`) when the profile's database is available, else appended to
work/usage/usage.jsonl, so it works in every environment.

    python -m esamkit.usage            # totals per model and per tool, last 7 days

Pricing is optional: a profile's `llm.pricing` gives per-1k-token rates (your
programme's own numbers). Without it, cost is simply not reported.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import PROJECT_ROOT

FILE_SINK = PROJECT_ROOT / "work" / "usage" / "usage.jsonl"

SCHEMA = """
CREATE TABLE IF NOT EXISTS esam_usage (
    id                BIGSERIAL PRIMARY KEY,
    at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id            TEXT NOT NULL,
    environment       TEXT NOT NULL DEFAULT '',
    kind              TEXT NOT NULL,          -- llm | tool | run
    agent             TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT '',
    tool              TEXT NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    calls             INTEGER NOT NULL DEFAULT 1,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    ok                BOOLEAN NOT NULL DEFAULT true,
    detail            JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS esam_usage_run ON esam_usage (run_id);
CREATE INDEX IF NOT EXISTS esam_usage_at ON esam_usage (at);
"""

_FIELDS = ("run_id", "environment", "kind", "agent", "model", "tool", "prompt_tokens", "completion_tokens",
           "calls", "duration_ms", "ok", "detail")


class UsageLedger:
    def __init__(self, run_id: str, environment: str = "", dsn: str | None = None, path: Path = FILE_SINK):
        self.run_id, self.environment, self.dsn, self.path = run_id, environment, dsn, path
        self.sink = "file"
        if dsn:
            try:
                import psycopg

                with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as conn:
                    conn.execute(SCHEMA)
                self.sink = "database"
            except Exception:
                self.sink = "file"     # database down: never lose the record, fall back to the file

    def record(self, kind: str, **kw) -> None:
        row = {"run_id": self.run_id, "environment": self.environment, "kind": kind, "agent": "", "model": "",
               "tool": "", "prompt_tokens": 0, "completion_tokens": 0, "calls": 1, "duration_ms": 0, "ok": True,
               "detail": {}}
        row.update({k: v for k, v in kw.items() if k in _FIELDS})
        if self.sink == "database":
            import psycopg

            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute(f"INSERT INTO esam_usage ({', '.join(_FIELDS)}) VALUES "
                             f"({', '.join(['%s'] * len(_FIELDS))})",
                             [json.dumps(row[f], default=str) if f == "detail" else row[f] for f in _FIELDS])
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **row}, default=str) + "\n")


def _rows(dsn: str | None, since: datetime, path: Path = FILE_SINK) -> list[dict]:
    rows = []
    if dsn:
        try:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(dsn, connect_timeout=3, row_factory=dict_row) as conn:
                if conn.execute("SELECT to_regclass('esam_usage')").fetchone()["to_regclass"]:
                    rows += conn.execute("SELECT * FROM esam_usage WHERE at >= %s", (since,)).fetchall()
        except Exception:
            pass
    if path.exists():
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if datetime.fromisoformat(r["at"]) >= since:
                rows.append(r)
    return rows


def summary(dsn: str | None = None, days: int = 7, pricing: dict | None = None, path: Path = FILE_SINK) -> dict:
    """Totals per model (LLM calls, tokens, optional cost) and per tool (calls, time, failures)."""
    rows = _rows(dsn, datetime.now(timezone.utc) - timedelta(days=days), path)
    models, tools, runs = {}, {}, set()
    for r in rows:
        runs.add(r["run_id"])
        if r["kind"] == "llm":
            m = models.setdefault(r["model"] or "?", {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
            m["calls"] += r["calls"]
            m["prompt_tokens"] += r["prompt_tokens"]
            m["completion_tokens"] += r["completion_tokens"]
        elif r["kind"] == "tool":
            t = tools.setdefault(r["tool"], {"calls": 0, "failures": 0, "total_ms": 0})
            t["calls"] += r["calls"]
            t["failures"] += 0 if r["ok"] else 1
            t["total_ms"] += r["duration_ms"]
    for name, m in models.items():
        rate = (pricing or {}).get(name)
        m["cost"] = (round(m["prompt_tokens"] / 1000 * rate["input_per_1k"]
                           + m["completion_tokens"] / 1000 * rate["output_per_1k"], 4) if rate else None)
    return {"days": days, "runs": len(runs), "models": models, "tools": tools}


def main() -> None:
    from .environment import load_profile, resolve

    prof = load_profile()
    s = summary(resolve(prof)["dsn"] or None, pricing=(prof.get("llm") or {}).get("pricing"))
    print(f"Usage, last {s['days']} days: {s['runs']} runs ({prof['name']} profile)")
    for name, m in s["models"].items():
        cost = f", cost {m['cost']}" if m["cost"] is not None else ""
        print(f"  model {name:<16} {m['calls']:>4} calls  {m['prompt_tokens']:>8} in  {m['completion_tokens']:>8} out{cost}")
    for name, t in s["tools"].items():
        print(f"  tool  {name:<16} {t['calls']:>4} calls  {t['total_ms']:>8} ms  {t['failures']} failed")
    if not s["runs"]:
        print("  (nothing recorded yet)")
    sys.exit(0)


if __name__ == "__main__":
    main()
