"""
Coverage — does the document a question NEEDS actually exist?

Vector search can't answer that: a document that doesn't exist has no
vectors, so search just returns the closest thing that DOES exist. Ask "how
is SRS-BRK-011 tested?" with no test description loaded and the best match
is the requirement itself, which reads like an answer. A model handed that
chunk will happily write a plausible test procedure.

The graph knows which documents each subsystem has. So before the model sees
anything, we work out (1) which document TYPE would answer the question and
(2) which SUBSYSTEM it's about, and ask the graph whether that document
exists. If not, the prompt says so in plain words (see hybrid.py).

Step (1) is a keyword table: crude on purpose, and the first thing an
ontology would replace (notes B3).
"""

from .graphstores.base import GraphStore

# Words in a question -> the document type that would answer it.
DOC_TYPE_CUES = {
    "std": ("test", "verif", "pass criteria", "pass/fail", "acceptance"),
    "icd": ("interface", "message", "pgn", "signal", "j1939", "transmit", "receive", "consume"),
    "sdd": ("design", "architecture", "task set", "scheduler", "implement"),
    "srs": ("requirement", "shall"),
}


def doc_types_needed(question: str) -> list[str]:
    q = question.lower()
    return [t for t, cues in DOC_TYPE_CUES.items() if any(c in q for c in cues)]


def mentioned_subsystems(question: str, subsystems: list[dict]) -> list[str]:
    """Subsystems named in the question, by full name ("Engine Control Module")
    or by id without its prefix ("DCL"). `subsystems` = [{"eid", "name"}]."""
    q = question.lower()
    found = []
    for s in subsystems:
        short = s["eid"].removeprefix("SUB-").lower()
        if s["name"].lower() in q or f" {short} " in f" {q} ".replace("?", " ").replace(",", " "):
            found.append(s["eid"])
    return found


def missing_documents(graph: GraphStore, subsystem_eids: list[str], doc_types: list[str]) -> list[dict]:
    """[{eid, doc_type}] for every (subsystem, type) pair with no such document."""
    missing = []
    for t in doc_types:
        gaps = {r["eid"] for r in graph.artifact_gaps(t)}
        missing += [{"eid": e, "doc_type": t} for e in subsystem_eids if e in gaps]
    return missing
