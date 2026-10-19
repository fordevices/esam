#!/usr/bin/env python3
"""
ESAM crew runner.

    python -m crew.main env [--env pilot-il5]     # step 0: environment check + Environment Engineer's report

    # Pipeline A: a document → verified ingestion, with an SE approval in the middle
    python -m crew.main review path/to/STD-BRK-001.md   # A1 ontology check, A2 review → STOP
    python -m crew.main inbox                           # the same for every file in work/inbox/
    python -m crew.main runs                            # runs and their state
    python -m crew.main approve <run> [--note ...]      # the SE's decision → A3 ingest, A4 verify
    python -m crew.main reject <run> --note "why"
    python -m crew.main undo <run>                      # reverse an ingest

    # Interactive: a question answered by the three agents
    python -m crew.main ask "Which brake requirements have no test?"

(Pipeline B and the task-list worker are added in later steps; crew/README.md.)

Every run gets a folder work/runs/<timestamp>_<name>/ with the environment
report (JSON), the agents' output (markdown) and a usage summary, and every
LLM call and tool call is written to the usage ledger (database, or
work/usage/usage.jsonl when there is none).
"""

import argparse
import getpass
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esamkit.environment import apply_telemetry, inspect, load_profile, resolve  # noqa: E402
from esamkit.usage import UsageLedger  # noqa: E402

RUNS = ROOT / "work" / "runs"
INBOX = ROOT / "work" / "inbox"


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


# ── Pipelines ───────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def prepare(env: str | None, pipeline: str):
    """Step 0 before every pipeline: the probes (no tokens). Returns
    (profile, report, crew) or exits with the reason the pipeline can't run."""
    from crew.crew import EsamCrew

    profile = load_profile(env)
    apply_telemetry(profile)
    report = inspect(env)
    if not report["capabilities"]["agents_can_run"]:
        sys.exit("NOT READY: no reasoning model. Run `python -m crew.main env` for the fixes.")
    return profile, report, EsamCrew


def check_tools(crew, pipeline: str) -> None:
    missing = crew.missing_tools(pipeline)
    if missing:
        sys.exit(f"NOT READY: this environment disables {', '.join(missing)}, which the {pipeline} pipeline needs. "
                 "Run `python -m esamkit.environment` to see why.")


def kickoff(crew, pipeline: str, wb, folder: Path, ledger: UsageLedger, inputs: dict) -> str:
    t = time.perf_counter()
    c = crew.pipeline_crew(pipeline, wb, folder)
    result = c.kickoff(inputs=inputs)
    for spec_name, out in zip(crew.PIPELINES[pipeline]["tasks"], result.tasks_output):
        (folder / f"{spec_name}.md").write_text(str(out.raw))
    usage = record_crew_usage(ledger, result, crew.llm.model.removeprefix("openai/"), f"crew:{pipeline}")
    ledger.record("run", duration_ms=round((time.perf_counter() - t) * 1000), detail={"command": pipeline, **usage})
    print(f"\nUsage: {usage}")
    return str(result.raw)


def find_run(run: str) -> Path:
    hits = sorted(p for p in RUNS.glob(f"*{run}*") if (p / "state.json").exists())
    if len(hits) != 1:
        sys.exit(f"{len(hits)} runs match {run!r}" + (": " + ", ".join(h.name for h in hits) if hits else ""))
    return hits[0]


def _workbench(profile: dict, folder: Path, state: dict):
    from crew.custom_tools import Workbench
    from esamkit.environment import settings_for
    return Workbench(settings_for(profile), folder, state)


def _parse_sets(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs or []:
        key, _, value = pair.partition("=")
        if key not in ("id", "name", "type", "revision", "attached_to") or not value:
            sys.exit(f"--set {pair!r}: use id=, name=, type=, revision= or attached_to=")
        out[key] = value
    return out


def review_one(path: Path, args) -> int:
    from esamkit.intake import read_header

    entry = {**read_header(path.read_text()), **_parse_sets(args.set)}
    if "id" in entry:
        entry["file"] = f"{entry['id']}.md"
    if not entry.get("id"):
        print(f"{path.name}: no '# <ID> — <name>' title line, so ESAM can't tell what it is. "
              "Add it, or give --set id=... name=...")
        return 1
    profile, report, EsamCrew = prepare(args.env, "review")
    run_id, folder, ledger = new_run(f"review_{entry['id']}", profile["name"])
    crew = EsamCrew(profile["name"], ledger, report)
    check_tools(crew, "review")
    (folder / "incoming").mkdir()
    shutil.copy2(path, folder / "incoming" / entry["file"])
    state = {"run_id": run_id, "kind": "intake", "state": "reviewing", "created": _now(),
             "environment": profile["name"], "source": str(path), "document": f"incoming/{entry['file']}", "entry": entry}
    wb = _workbench(profile, folder, state)
    wb.save()
    print(f"Run {run_id}: reviewing {entry['id']} (usage → {ledger.sink})")
    out = kickoff(crew, "review", wb, folder, ledger,
                  {"document": f"{entry['id']} ({entry.get('name', path.name)})", "run_id": run_id,
                   "request": f"review {entry['id']} for ingestion", "environment": profile["name"]})
    m = re.search(r"RECOMMENDATION:\s*(DO NOT INGEST|INGEST)", out)
    state.update(state="awaiting_approval", recommendation=m[1] if m else "none given")
    wb.save()
    (folder / "approval_request.md").write_text(out)
    print(f"\n{out}\n\nSTOPPED for SE approval. Read {folder / 'approval_request.md'}, then:\n"
          f"  python -m crew.main approve {run_id}\n  python -m crew.main reject {run_id} --note \"why\"")
    return 0


def cmd_review(args) -> int:
    path = Path(args.file).resolve()
    if not path.is_file():
        sys.exit(f"no such file: {path}")
    return review_one(path, args)


def cmd_inbox(args) -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    files = sorted(INBOX.glob("*.md"))
    if not files:
        print(f"Inbox is empty: drop documents (.md) into {INBOX}")
        return 0
    rc = 0
    for f in files:
        rc |= review_one(f, args)
        (INBOX / "done").mkdir(exist_ok=True)
        shutil.move(str(f), INBOX / "done" / f.name)
    return rc


def cmd_runs(args) -> int:
    rows = [json.loads((p / "state.json").read_text()) for p in sorted(RUNS.glob("*")) if (p / "state.json").exists()]
    if not rows:
        print("No pipeline runs yet.")
    for s in rows:
        print(f"  {s['run_id']:<40} {s['state']:<20} {s['entry'].get('id', ''):<13} "
              f"recommendation: {s.get('recommendation', '-')}")
    return 0


def cmd_approve(args) -> int:
    folder = find_run(args.run)
    state = json.loads((folder / "state.json").read_text())
    if state["state"] != "awaiting_approval":
        sys.exit(f"{state['run_id']} is {state['state']}, not awaiting approval.")
    if state.get("recommendation") != "INGEST" and not args.against_recommendation:
        sys.exit(f"The SE Reviewer recommended {state.get('recommendation')!r}. To approve anyway, add "
                 "--against-recommendation and a --note saying why.")
    profile, report, EsamCrew = prepare(args.env or state["environment"], "ingest")
    run_id, ledger = state["run_id"], new_run_ledger(state, profile)
    crew = EsamCrew(profile["name"], ledger, report)
    check_tools(crew, "ingest")
    state["approval"] = {"decision": "approved", "by": args.by, "at": _now(), "note": args.note,
                         "against_recommendation": state.get("recommendation") != "INGEST"}
    wb = _workbench(profile, folder, state)
    wb.save()
    entry = state["entry"]
    out = kickoff(crew, "ingest", wb, folder, ledger,
                  {"document": f"{entry['id']} ({entry.get('name', '')})", "run_id": run_id,
                   "request": f"ingest and verify {entry['id']}", "environment": profile["name"]})
    (folder / "verification_report.md").write_text(out)
    if state["state"] == "awaiting_approval":           # the ingest tool refused
        state["state"] = "ingest_refused"
    wb.save()
    print(f"\n{out}\n\nRun {run_id}: {state['state']}. Report: {folder / 'verification_report.md'}")
    return 0 if state["state"] == "verified" else 1


def new_run_ledger(state: dict, profile: dict) -> UsageLedger:
    """The usage of an approval continues its run's record."""
    want_db = (profile.get("usage") or {}).get("sink", "file") == "database"
    return UsageLedger(state["run_id"], profile["name"], dsn=resolve(profile)["dsn"] if want_db else None)


def cmd_reject(args) -> int:
    folder = find_run(args.run)
    state = json.loads((folder / "state.json").read_text())
    if state["state"] != "awaiting_approval":
        sys.exit(f"{state['run_id']} is {state['state']}, not awaiting approval.")
    state.update(state="rejected", approval={"decision": "rejected", "by": args.by, "at": _now(), "note": args.note})
    (folder / "state.json").write_text(json.dumps(state, indent=1, default=str))
    print(f"Rejected {state['run_id']}. Nothing was ingested.")
    return 0


def cmd_undo(args) -> int:
    from esamkit.intake import undo

    folder = find_run(args.run)
    state = json.loads((folder / "state.json").read_text())
    if not (folder / "manifest.json").exists() or state["state"] == "undone":
        sys.exit(f"{state['run_id']} is {state['state']}: nothing to undo.")
    profile = load_profile(args.env or state["environment"])
    wb = _workbench(profile, folder, state)
    manifest = json.loads((folder / "manifest.json").read_text())
    res = undo(manifest, wb.store, wb.graph, wb.emb if manifest.get("previous_file") else None, wb.s)
    state.update(state="undone", undo={"by": args.by, "at": _now(), **res})
    wb.save()
    print(f"Undone {state['run_id']}: {json.dumps(res, default=str)}")
    return 0


def cmd_ask(args) -> int:
    profile, report, EsamCrew = prepare(args.env, "ask")
    run_id, folder, ledger = new_run("ask", profile["name"])
    crew = EsamCrew(profile["name"], ledger, report)
    check_tools(crew, "ask")
    wb = _workbench(profile, folder, {})
    (folder / "question.txt").write_text(args.question)
    out = kickoff(crew, "ask", wb, folder, ledger, {"request": args.question, "environment": profile["name"],
                                                    "document": "", "run_id": run_id})
    (folder / "answer.md").write_text(out)
    print(f"\n{out}\n\nSaved: {folder}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="ESAM crew runner")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, env=True):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        if env:
            p.add_argument("--env", default=None, help="environment profile (default: ESAM_ENV or local)")
        return p

    e = add("env", cmd_env, "step 0: check the environment")
    e.add_argument("--no-network", action="store_true", help="don't contact model endpoints in the probes")
    for name, fn, help_ in (("review", cmd_review, "A1+A2 on one document, then stop for approval"),
                            ("inbox", cmd_inbox, "review every document in work/inbox/")):
        p = add(name, fn, help_)
        if name == "review":
            p.add_argument("file")
        p.add_argument("--set", action="append", metavar="KEY=VALUE",
                       help="supply a field the document doesn't state (revision=A, attached_to=SUB-...)")
    add("runs", cmd_runs, "list pipeline runs", env=False)
    for name, fn in (("approve", cmd_approve), ("reject", cmd_reject), ("undo", cmd_undo)):
        p = add(name, fn, f"{name} a run")
        p.add_argument("run", help="run id (or a unique part of it)")
        p.add_argument("--by", default=getpass.getuser(), help="who decides (default: your login)")
        p.add_argument("--note", default="", required=(name == "reject"))
        if name == "approve":
            p.add_argument("--against-recommendation", action="store_true")
    a = add("ask", cmd_ask, "answer a question with the three agents")
    a.add_argument("question")
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
