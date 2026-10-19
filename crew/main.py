#!/usr/bin/env python3
"""
ESAM crew runner.

    python -m crew.main env [--env pilot-il5]     # step 0: environment check + Environment Engineer's report

(Pipelines A and B, the interactive mode, the inbox and the task-list worker
are added step by step; see the B5 plan in notes/.)

Every run gets a folder work/runs/<timestamp>_<name>/ with the environment
report (JSON), the agents' output (markdown) and a usage summary, and every
LLM call and tool call is written to the usage ledger (database, or
work/usage/usage.jsonl when there is none).
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esamkit.environment import apply_telemetry, inspect, load_profile, resolve  # noqa: E402
from esamkit.usage import UsageLedger  # noqa: E402

RUNS = ROOT / "work" / "runs"


def new_run(name: str, environment: str) -> tuple[str, Path, UsageLedger]:
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
    folder = RUNS / run_id
    folder.mkdir(parents=True, exist_ok=True)
    profile = load_profile(environment)
    want_db = (profile.get("usage") or {}).get("sink", "file") == "database"
    return run_id, folder, UsageLedger(run_id, profile["name"], dsn=resolve(profile)["dsn"] if want_db else None)


def record_crew_usage(ledger: UsageLedger, result, model: str, agent: str) -> dict:
    u = getattr(result, "token_usage", None)
    if not u:
        return {}
    usage = {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
             "total_tokens": u.total_tokens, "requests": u.successful_requests}
    ledger.record("llm", agent=agent, model=model, prompt_tokens=u.prompt_tokens,
                  completion_tokens=u.completion_tokens, calls=max(1, u.successful_requests))
    return usage


def cmd_env(args) -> int:
    profile = load_profile(args.env)
    apply_telemetry(profile)
    report = inspect(args.env, network=not args.no_network)
    run_id, folder, ledger = new_run("environment", profile["name"])
    (folder / "environment.json").write_text(json.dumps(report, indent=1, default=str))
    print(f"Run {run_id}  (usage → {ledger.sink})\n")
    for p in report["probes"]:
        print(f"  {p['name']:<14} {p['status']:<15} {p['detail']}")
    if not report["capabilities"]["agents_can_run"]:
        print("\nNo reasoning model is available, so the Environment Engineer can't run. "
              f"Fix the llm.reasoning probe above. Report saved in {folder}")
        return 1

    from crew.crew import EsamCrew

    t = time.perf_counter()
    crew = EsamCrew(profile["name"], ledger, report).environment_crew()
    result = crew.kickoff(inputs={"environment": profile["name"],
                                  "needed_tools": ", ".join(report["capabilities"]["enabled"]) or "none"})
    usage = record_crew_usage(ledger, result, resolve(profile)["roles"]["reasoning"]["model"], "environment_engineer")
    ledger.record("run", duration_ms=round((time.perf_counter() - t) * 1000), detail={"command": "env", **usage})
    (folder / "environment_report.md").write_text(str(result.raw))
    print(f"\n{result.raw}\n\nSaved: {folder}\nUsage: {usage}")
    return 0 if "GREEN LIGHT" in str(result.raw) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="ESAM crew runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("env", help="step 0: check the environment")
    e.add_argument("--env", default=None, help="environment profile (default: ESAM_ENV or local)")
    e.add_argument("--no-network", action="store_true", help="don't contact model endpoints in the probes")
    args = ap.parse_args()
    sys.exit({"env": cmd_env}[args.cmd](args))


if __name__ == "__main__":
    main()
