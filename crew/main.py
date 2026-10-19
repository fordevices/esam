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

    # Pipeline B: an SRS from an SE's brief → candidate draft + evidence pack in work/drafts/
    python -m crew.main author data/briefs/SRS-ENG-001.yaml

    # The task list (PostgreSQL) and its worker
    python -m crew.main tasks add review work/inbox/STD-BRK-001.md
    python -m crew.main tasks add approve <run> --note "checked"     # the SE's own decision
    python -m crew.main tasks add author data/briefs/SRS-ENG-001.yaml
    python -m crew.main tasks add ask "Which brake requirements have no test?"
    python -m crew.main tasks list | show <id> | cancel <id> | requeue <id>
    python -m crew.main worker [--once]

    # Interactive: a question answered by the three agents
    python -m crew.main ask "Which brake requirements have no test?"

(Details: crew/README.md.)

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

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from esamkit.environment import apply_telemetry, inspect, load_profile, resolve  # noqa: E402
from esamkit.usage import UsageLedger  # noqa: E402

from crew.terminal_log import logged  # noqa: E402

RUNS = ROOT / "work" / "runs"
LOGS = ROOT / "work" / "logs"            # a command's log before its run folder exists
LAST_RUN: dict = {}      # the run the current command made (its log goes there; the worker records it)
INBOX = ROOT / "work" / "inbox"
DRAFTS = ROOT / "work" / "drafts"


def new_run(name: str, environment: str) -> tuple[str, Path, UsageLedger]:
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}"
    LAST_RUN.update(run_id=run_id, folder=RUNS / run_id)
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


@logged("env", LAST_RUN, LOGS)
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


@logged("review", LAST_RUN, LOGS)
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
        print(f"  {s['run_id']:<44} {s['state']:<20} {s.get('entry', {}).get('id', ''):<13} "
              f"recommendation: {s.get('recommendation', '-')}")
    return 0


@logged("approve", LAST_RUN, LOGS)
def cmd_approve(args) -> int:
    folder = find_run(args.run)
    LAST_RUN.update(run_id=folder.name, folder=folder)
    state = json.loads((folder / "state.json").read_text())
    if state["state"] != "awaiting_approval":
        sys.exit(f"{state['run_id']} is {state['state']}, not awaiting approval.")
    if state.get("recommendation") != "INGEST" and not args.against_recommendation:
        sys.exit(f"The SE Reviewer recommended {state.get('recommendation')!r}. To approve anyway, add "
                 "--against-recommendation and a --note saying why.")
    LAST_RUN.update(run_id=state["run_id"], folder=folder)
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
    from esamkit.runs import reject

    try:
        state = reject(find_run(args.run).name, args.by, args.note)
    except (LookupError, ValueError) as e:
        sys.exit(str(e))
    print(f"Rejected {state['run_id']}. Nothing was ingested.")
    return 0


@logged("undo", LAST_RUN, LOGS)
def cmd_undo(args) -> int:
    from esamkit.intake import undo

    folder = find_run(args.run)
    LAST_RUN.update(run_id=folder.name, folder=folder)
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


@logged("author", LAST_RUN, LOGS)
def cmd_author(args) -> int:
    from crew.custom_tools import _authoring, build_pack

    brief_path = Path(args.brief).resolve()
    if not brief_path.is_file():
        sys.exit(f"no such brief: {brief_path}")
    doc = (yaml.safe_load(brief_path.read_text()) or {}).get("document") or {}
    if not doc.get("id"):
        sys.exit(f"{brief_path.name}: the brief needs document.id")
    profile, report, EsamCrew = prepare(args.env, "author")
    run_id, folder, ledger = new_run(f"author_{doc['id']}", profile["name"])
    crew = EsamCrew(profile["name"], ledger, report)
    check_tools(crew, "author")
    shutil.copy2(brief_path, folder / "brief.yaml")
    state = {"run_id": run_id, "kind": "author", "state": "drafting", "created": _now(), "environment": profile["name"],
             "brief": str(folder / "brief.yaml"), "entry": {"id": doc["id"]}, "model": crew.llm.model,
             "drafts": str(DRAFTS / f"{doc['id']}_{run_id.split('_author')[0]}")}
    wb = _workbench(profile, folder, state)
    wb.save()
    _, assessment, _, _ = _authoring(wb)
    print(f"Run {run_id}: drafting {doc['id']} (CSCI {assessment['csci_swci'] or 'no SSFs'}; usage → {ledger.sink})")
    for f in assessment["findings"]:
        print(f"  brief {f['level']}: {f['where']}: {f['message']}")
    if not assessment["ok"]:
        state["state"] = "brief_rejected"
        wb.save()
        print("The brief has errors; fix them before drafting.")
        return 1
    out = kickoff(crew, "author", wb, folder, ledger,
                  {"document": f"{doc['id']} ({doc.get('name', '')})", "run_id": run_id,
                   "request": f"draft {doc['id']} for {doc.get('subsystem')}", "environment": profile["name"]})
    if not (folder / "draft.md").exists():
        state["state"] = "no_draft"
        wb.save()
        print(f"\n{out}\n\nNo draft was saved.")
        return 1
    files = build_pack(wb)                      # always from the final draft
    review = folder / "b3_verify_srs.md"
    if review.exists():
        shutil.copy2(review, Path(state["drafts"]) / "se_reviewer_memo.md")
    m = re.search(r"VERDICT:\s*(READY FOR SE REVIEW|NOT READY)", review.read_text() if review.exists() else "")
    state.update(state="candidate", verdict=m[1] if m else "none given", recommendation=m[1] if m else "-")
    wb.save()
    lint = state["final_lint"]
    print(f"\n{out}\n\nCandidate draft: {files['draft']}\nEvidence pack:   {files['evidence_pack']}\n"
          f"Lint: {lint['errors']} errors, {lint['warnings']} warnings. Reviewer verdict: {state['verdict']}.\n"
          f"Next: python -m crew.main review {files['draft']}")
    return 0 if lint["errors"] == 0 else 1


@logged("ask", LAST_RUN, LOGS)
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


# ── The task list ───────────────────────────────────────────────────────────

def _queue(env: str | None = None):
    from esamkit.tasks import TaskQueue
    profile = load_profile(env)
    try:
        return TaskQueue(resolve(profile)["dsn"]), profile
    except Exception as e:
        sys.exit(f"The task list needs the profile's database: {type(e).__name__}: {e}")


def _outcome(rc: int) -> dict:
    """What a finished command left in its run folder, in one line for people."""
    run_id, folder = LAST_RUN.get("run_id", ""), LAST_RUN.get("folder")
    state = json.loads((folder / "state.json").read_text()) if folder and (folder / "state.json").exists() else {}
    s, line = state.get("state", ""), f"exit {rc}"
    if s == "awaiting_approval":
        line = f"awaiting SE approval (recommendation: {state.get('recommendation')}): crew.main approve {run_id}"
    elif s in ("verified", "verification_failed", "ingest_refused", "rejected", "undone"):
        line = s.replace("_", " ")
    elif s == "candidate":
        lint = state.get("final_lint", {})
        line = (f"candidate draft, {lint.get('errors')} errors / {lint.get('warnings')} warnings, reviewer: "
                f"{state.get('verdict')}; pack: {(state.get('evidence_pack') or {}).get('evidence_pack', '')}")
    elif folder and (folder / "answer.md").exists():
        line = f"answer: {folder / 'answer.md'}"
    elif folder and (folder / "environment_report.md").exists():
        line = "GREEN LIGHT" if rc == 0 else "NOT READY"
    return {"ok": rc == 0, "run_id": run_id, "outcome": line,
            "result": {k: state.get(k) for k in ("state", "recommendation", "verdict", "final_lint", "evidence_pack") if k in state}}


def _handlers() -> dict:
    ns = argparse.Namespace

    def run(fn, task, **kw):
        return _outcome(fn(ns(env=task["environment"] or None, **kw)))

    def review(task):
        path = Path(task["payload"]["file"])
        return _outcome(review_one(path if path.is_absolute() else ROOT / path,
                                   ns(env=task["environment"] or None, set=task["payload"].get("set") or [])))

    return {
        "review": review,
        "approve": lambda t: run(cmd_approve, t, run=t["payload"]["run"], by=t["created_by"],
                                 note=t["payload"].get("note", ""),
                                 against_recommendation=t["payload"].get("against_recommendation", False)),
        "author": lambda t: run(cmd_author, t, brief=t["payload"]["brief"]),
        "ask": lambda t: run(cmd_ask, t, question=t["payload"]["question"]),
        "env": lambda t: run(cmd_env, t, no_network=False),
    }


def cmd_tasks(args) -> int:
    from esamkit.tasks import KINDS

    q, profile = _queue(args.env)
    if args.action == "add":
        if not args.rest or args.rest[0] not in KINDS:
            sys.exit(f"tasks add <kind> ...: kind is one of {', '.join(KINDS)}")
        args.kind, arg = args.rest[0], " ".join(args.rest[1:])
        if not arg and args.kind != "env":
            sys.exit(f"tasks add {args.kind} needs its argument (a file, brief, run id or question)")
        payload = {"review": lambda: {"file": arg, "set": args.set or []}, "author": lambda: {"brief": arg},
                   "ask": lambda: {"question": arg}, "env": lambda: {},
                   "approve": lambda: {"run": arg, "note": args.note,
                                       "against_recommendation": args.against_recommendation}}[args.kind]()
        for key in ("file", "brief"):
            if key in payload and not (ROOT / payload[key]).exists() and not Path(payload[key]).exists():
                sys.exit(f"no such file: {payload[key]}")
        tid = q.add(args.kind, payload, created_by=args.by, priority=args.priority, environment=args.env or "")
        print(f"Task {tid} queued: {args.kind} {arg}. Run `python -m crew.main worker` to process the list.")
        return 0
    if args.action == "list":
        rows = q.list(args.status)
        if not rows:
            print("No tasks.")
        for r in rows:
            what = r["payload"].get("file") or r["payload"].get("brief") or r["payload"].get("run") or r["payload"].get("question", "")
            print(f"  {r['id']:>4}  {r['status']:<9} {r['kind']:<8} {str(what)[:46]:<46} {r['outcome'][:70]}")
        return 0
    task = q.get(int(args.rest[0])) if args.rest and args.rest[0].isdigit() else None
    if task is None:
        sys.exit(f"no task {' '.join(args.rest)}")
    if args.action == "show":
        print(json.dumps(task, indent=1, default=str))
        return 0
    ok = q.cancel(task["id"]) if args.action == "cancel" else q.requeue(task["id"])
    print(f"Task {task['id']}: {args.action} {'done' if ok else 'not possible (status ' + task['status'] + ')'}")
    return 0 if ok else 1


def cmd_worker(args) -> int:
    from esamkit.tasks import process_one, worker_name

    q, _ = _queue(args.env)
    handlers, me = _handlers(), worker_name()
    if args.kinds:
        handlers = {k: v for k, v in handlers.items() if k in args.kinds}
    print(f"Worker {me}: kinds {', '.join(handlers)}; {'one task' if args.once else f'polling every {args.poll} s (Ctrl-C stops)'}")
    done = 0
    try:
        while True:
            task = process_one(q, handlers, me)
            if task is None:
                if args.once or (args.drain and done):
                    break
                time.sleep(args.poll)
                continue
            done += 1
            print(f"\nTask {task['id']} {task['kind']}: {task['status']}: {task['outcome']}")
            if args.once:
                break
    except KeyboardInterrupt:
        print("\nStopped. A task left 'running' can be put back with `tasks requeue <id>`.")
    print(f"Worker {me}: {done} task(s) processed.")
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
    tk = add("tasks", cmd_tasks, "the task list: add, list, show, cancel, requeue")
    tk.add_argument("action", choices=["add", "list", "show", "cancel", "requeue"])
    tk.add_argument("rest", nargs="*", help="add: <kind> <file | brief | run id | question>;  show/cancel/requeue: <task id>")
    tk.add_argument("--by", default=getpass.getuser(), help="who adds it (an approve task records this person)")
    tk.add_argument("--note", default="")
    tk.add_argument("--priority", type=int, default=0)
    tk.add_argument("--status", default=None, help="list: only this status")
    tk.add_argument("--set", action="append", metavar="KEY=VALUE")
    tk.add_argument("--against-recommendation", action="store_true")
    w = add("worker", cmd_worker, "process the task list")
    w.add_argument("--once", action="store_true", help="one task, then stop")
    w.add_argument("--drain", action="store_true", help="stop when the list is empty")
    w.add_argument("--poll", type=float, default=10.0)
    w.add_argument("--kinds", nargs="*", help="only these kinds")
    b = add("author", cmd_author, "Pipeline B: draft an SRS from an SE's brief, verified, with an evidence pack")
    b.add_argument("brief")
    a = add("ask", cmd_ask, "answer a question with the three agents")
    a.add_argument("question")
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
