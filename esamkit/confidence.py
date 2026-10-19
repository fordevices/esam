"""
The missing-document confidence test.

For each scenario: ask a question whose answer lives in a document that
doesn't exist yet, check the system SAYS it doesn't exist, ingest the
document, ask again, and check the answer now comes from it.

Two kinds of check, reported separately:
  * deterministic: what the graph reports and what goes into the prompt.
    These are the guarantee; they don't depend on the model.
  * answer: what the model wrote, checked by simple text rules. Useful,
    but heuristics: a model can say "missing" in words these rules miss.

"Ingesting" a scenario document means what an SE would do: add the artifact
to the baseline and load the file. The committed baseline YAML isn't edited;
the run writes a copy with the extra artifact (an overlay) and loads that.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from .baseline import Baseline, load_baseline
from .embedders.base import Embedder
from .graphstores.base import GraphStore
from .hybrid import SYSTEM_PROMPT, retrieve
from .ingest import ingest_file
from .traceability import load_traceability
from .vectorstores.base import VectorStore

# Ways a model says "that document doesn't exist". Lower-case substrings.
MISSING_PHRASES = ("missing", "does not exist", "doesn't exist", "not exist", "no test description",
                   "not available", "not been provided", "not provided", "no documents", "no document",
                   "cannot be answered", "can't be answered", "cannot be determined", "cannot determine",
                   "cannot be assessed", "not attached", "not yet", "absent", "unconfirmed")


@dataclass
class Scenario:
    name: str               # short id, e.g. "brake-std"
    title: str
    question: str
    subsystem: str          # the subsystem the missing document belongs to
    doc_type: str           # std / icd / ...
    document: Path          # the staged document file
    artifact: dict          # its baseline `artifacts:` entry
    expect_cited: list[str] = field(default_factory=list)   # ids the AFTER answer must mention
    forbid_before: str = ""  # regex the BEFORE answer must NOT match (an invented value)
    verifies: dict = field(default_factory=dict)  # {requirement: test case} the new document must add


def baseline_with(base_yaml: Path, artifacts: list[dict], out: Path) -> Baseline:
    """The baseline plus extra artifact entries, as the SE would have added them."""
    data = yaml.safe_load(base_yaml.read_text())
    data["artifacts"] = list(data.get("artifacts", [])) + artifacts
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(data, sort_keys=False))
    return load_baseline(out)


def restore(scenarios: list[Scenario], store: VectorStore, graph: GraphStore, base: Baseline,
            documents_dir: Path | None = None) -> None:
    """Back to the committed baseline: scenario documents out of both stores,
    and the requirements/test cases of the committed documents reloaded."""
    from .config import settings

    for sc in scenarios:
        store.delete_source(sc.artifact["file"])
    graph.reset()
    graph.load(base)
    load_traceability(graph, base, documents_dir or settings.documents_dir)


def _check(label: str, ok: bool, kind: str) -> dict:
    return {"check": label, "ok": bool(ok), "kind": kind}


def run_scenario(sc: Scenario, emb: Embedder, store: VectorStore, graph: GraphStore,
                 base_yaml: Path, work_dir: Path,
                 ask: Callable[[str, str], str] | None = None) -> dict:
    """BEFORE -> ingest -> AFTER. `ask(system, user)` calls the chat model;
    None = deterministic checks only (dry run, and the pytest suite)."""
    base = load_baseline(base_yaml)
    art = sc.artifact["id"]

    # ── BEFORE: the document is nowhere ──
    restore([sc], store, graph, base)
    r1 = retrieve(sc.question, emb, store, graph, base)
    missing1 = {(m["eid"], m["doc_type"]) for m in r1["coverage"]["missing"]}
    before = {"log": r1["log"], "coverage": r1["coverage"], "evidence": r1["evidence"], "checks": [
        _check(f"graph reports {sc.subsystem} has no {sc.doc_type.upper()}",
               (sc.subsystem, sc.doc_type) in missing1, "deterministic"),
        _check("prompt tells the model it is MISSING", "MISSING: " + sc.subsystem in r1["prompt"], "deterministic"),
    ] + [_check(f"graph: no test case verifies {req}", not graph.verified_by(req), "deterministic")
         for req in sc.verifies]}
    if ask:
        a1 = ask(SYSTEM_PROMPT, r1["prompt"])
        before["answer"] = a1
        low = a1.lower()
        before["checks"] += [
            _check("answer says the document is missing", any(p in low for p in MISSING_PHRASES), "answer"),
            _check(f"answer does not cite [{art}]", f"[{art}]" not in a1, "answer"),
        ]
        if sc.forbid_before:
            before["checks"].append(_check("answer invents nothing (no made-up ids/values)",
                                           not re.search(sc.forbid_before, a1), "answer"))

    # ── INGEST: the SE adds the artifact to the baseline and loads the file ──
    overlay = baseline_with(base_yaml, [sc.artifact], work_dir / f"baseline_{sc.name}.yaml")
    graph.load(overlay)
    facts = overlay.artifact_index()[sc.artifact["file"]]
    n, _ = ingest_file(sc.document, sc.artifact["file"], facts, emb, store)
    load_traceability(graph, overlay, sc.document.parent, only={art})   # its test cases / requirements
    ingest = {"artifact": art, "attached_to": sc.artifact["attached_to"], "chunks": n}

    # ── AFTER: same question ──
    r2 = retrieve(sc.question, emb, store, graph, overlay)
    missing2 = {(m["eid"], m["doc_type"]) for m in r2["coverage"]["missing"]}
    after = {"log": r2["log"], "coverage": r2["coverage"], "evidence": r2["evidence"], "checks": [
        _check(f"graph now has a {sc.doc_type.upper()} for {sc.subsystem}",
               (sc.subsystem, sc.doc_type) not in missing2, "deterministic"),
        _check(f"{art} retrieved as evidence", art in r2["evidence"], "deterministic"),
    ] + [_check(f"graph: {tc} verifies {req}", tc in [v["eid"] for v in graph.verified_by(req)], "deterministic")
         for req, tc in sc.verifies.items()]}
    if ask:
        a2 = ask(SYSTEM_PROMPT, r2["prompt"])
        after["answer"] = a2
        after["checks"] += [_check(f"answer cites {x}", x in a2, "answer") for x in sc.expect_cited]

    passed = all(c["ok"] for c in before["checks"] + after["checks"])
    return {"name": sc.name, "title": sc.title, "question": sc.question, "passed": passed,
            "before": before, "ingest": ingest, "after": after}
