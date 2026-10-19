"""
Traceability: requirements and test cases as vertices of the graph.

The baseline lists DOCUMENTS. Inside an SRS are the requirements; inside an
STD are the test cases, each with a `Verifies:` line (SE manual §9). This
module reads them out, deterministically (no AI), and turns them into:

    Requirement ──SPECIFIED_IN──► DesignArtifact (its SRS)
    Requirement ──ALLOCATED_TO──► Subsystem      (the SRS's subsystem)
    TestCase    ──SPECIFIED_IN──► DesignArtifact (its STD)
    TestCase    ──VERIFIES──────► Requirement

which answers "which requirements have no test?" and "what verifies
SRS-BRK-011?" with a graph query instead of a guess.

Every extracted vertex records `source_artifact` (the document it came
from), so reloading a document first removes what its previous revision
contributed: a deleted requirement doesn't linger.

The formats it reads are the ones the manual asks SEs to write:

    - **SRS-BRK-011** The software shall release brake pressure ...
      (continuation lines indented)

    ## TC-BRK-011 — Pressure release time on incipient wheel lock-up
    Verifies: SRS-BRK-011.
    Method: Test.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .baseline import Baseline, Edge, Vertex
from .graphstores.base import GraphStore

_REQ_LINE = re.compile(r"^- \*\*(?P<id>[A-Z]+-[A-Z0-9]+-\d{3})\*\*\s*(?P<text>.*)$")
_SECTION = re.compile(r"^#{2,6}\s+(?P<title>.+)$")
_TC_HEAD = re.compile(r"^#{2,6}\s+(?P<id>TC-[A-Z0-9]+-\d{3})\s*[—–-]\s*(?P<title>.+)$")
_FIELD = re.compile(r"^(?P<key>Verifies|Method|Steps|Pass criteria):\s*(?P<value>.*)$", re.I)
_ID = re.compile(r"\b[A-Z]+-[A-Z0-9]+-\d{3}\b")


@dataclass
class Extracted:
    vertices: list[Vertex] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


def _short(text: str, n: int = 90) -> str:
    first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
    return first if len(first) <= n else first[: n - 1].rstrip() + "…"


def read_requirements(text: str, artifact_id: str, subsystem: str) -> Extracted:
    out, section, cur = Extracted(), "", None

    def close():
        if cur:
            body = " ".join(cur["lines"]).strip()
            out.vertices.append(Vertex("Requirement", {
                "id": cur["id"], "name": _short(body), "text": body, "section": cur["section"],
                "source_artifact": artifact_id, "subsystem": subsystem}))
            out.edges += [Edge(cur["id"], artifact_id, "SPECIFIED_IN"), Edge(cur["id"], subsystem, "ALLOCATED_TO")]

    for line in text.splitlines():
        if m := _SECTION.match(line):
            close(); cur = None
            section = m["title"].strip()
        elif m := _REQ_LINE.match(line):
            close()
            cur = {"id": m["id"], "lines": [m["text"]], "section": section}
        elif cur and line.startswith("  ") and line.strip():
            cur["lines"].append(line.strip())
        elif cur and not line.strip():
            close(); cur = None
    close()
    return out


def read_test_cases(text: str, artifact_id: str) -> Extracted:
    out, cur = Extracted(), None

    def close():
        if cur:
            refs = _ID.findall(cur["fields"].get("verifies", ""))
            out.vertices.append(Vertex("TestCase", {
                "id": cur["id"], "name": cur["title"], "method": cur["fields"].get("method", "").rstrip("."),
                "verifies": refs, "pass_criteria": cur["fields"].get("pass criteria", ""),
                "source_artifact": artifact_id}))
            out.edges.append(Edge(cur["id"], artifact_id, "SPECIFIED_IN"))
            out.edges += [Edge(cur["id"], r, "VERIFIES") for r in refs]

    key = None
    for line in text.splitlines():
        if m := _TC_HEAD.match(line):
            close()
            cur, key = {"id": m["id"], "title": m["title"].strip(), "fields": {}}, None
        elif _SECTION.match(line):
            close(); cur = None
        elif cur and (m := _FIELD.match(line.strip())):
            key = m["key"].lower()
            cur["fields"][key] = m["value"].strip()
        elif cur and key and line.strip():
            cur["fields"][key] += " " + line.strip()      # a field continued on the next line
    close()
    return out


def extract(baseline: Baseline, documents_dir: Path, only: set[str] | None = None) -> Extracted:
    """Requirements from every SRS and test cases from every STD the baseline lists
    (or only the artifact ids in `only`)."""
    out = Extracted()
    for rel, facts in baseline.artifact_index().items():
        if only is not None and facts["artifact_id"] not in only:
            continue
        path = documents_dir / rel
        if not path.exists():
            continue
        t = facts["artifact_type"]
        if t == "srs":
            part = read_requirements(path.read_text(), facts["artifact_id"], facts["subsystem_id"])
        elif t == "std":
            part = read_test_cases(path.read_text(), facts["artifact_id"])
        else:
            continue
        out.vertices += part.vertices
        out.edges += part.edges
    return out


def load_traceability(graph: GraphStore, baseline: Baseline, documents_dir: Path,
                      only: set[str] | None = None) -> dict:
    """Replace what the given documents contribute to the graph. Edges to a
    requirement that doesn't exist (a test that cites a typo) are not stored:
    the TestCase keeps the reference in `verifies`, and `unresolved` reports it.
    Returns counts plus the unresolved references."""
    ex = extract(baseline, documents_dir, only)
    sources = {v.props["source_artifact"] for v in ex.vertices} | (only or set())
    for src in sources:
        graph.remove_derived(src)
    known = ({v.eid for v in baseline.vertices} | {v.eid for v in ex.vertices}
             | graph.existing_ids("Requirement") | graph.existing_ids("DesignArtifact"))
    edges = [e for e in ex.edges if e.to_eid in known]
    unresolved = [{"test_case": e.from_eid, "cites": e.to_eid} for e in ex.edges
                  if e.type == "VERIFIES" and e.to_eid not in known]
    graph.load(Baseline(metadata={}, vertices=ex.vertices, edges=edges, artifacts=[]))
    return {"requirements": sum(v.label == "Requirement" for v in ex.vertices),
            "test_cases": sum(v.label == "TestCase" for v in ex.vertices),
            "verifies": sum(e.type == "VERIFIES" for e in edges), "unresolved": unresolved}
