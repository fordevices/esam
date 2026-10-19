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

Step (1) reads the ontology (data/ontology/esam_ontology.yaml): each
document type lists the words that mean it would answer a question, plus its
synonyms. Step (2) matches subsystem names and their baseline `aliases`.
Both are SE-owned data, not code.
"""

import re

from .graphstores.base import GraphStore
from .ontology import Ontology, default_ontology


def _says(text: str, phrase: str) -> bool:
    # Match at the START of a word: "verif" matches "verified", "test" matches
    # "tests", but "irs" does not match inside "first".
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase), text) is not None


def doc_types_needed(question: str, onto: Ontology | None = None) -> list[str]:
    q = question.lower()
    cues = (onto or default_ontology()).doc_type_cues()
    return [t for t, words in cues.items() if any(_says(q, w) for w in words)]


def mentioned_subsystems(question: str, subsystems: list[dict]) -> list[str]:
    """Subsystems named in the question, by full name, by any alias
    ("ECM", "brake ECU"), or by id without its prefix ("DCL").
    `subsystems` = [{"eid", "name", "aliases"?}]."""
    q = question.lower()
    found = []
    for s in subsystems:
        names = [s["name"], s["eid"].removeprefix("SUB-"), *s.get("aliases", [])]
        if any(re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", q) for n in names if n):
            found.append(s["eid"])
    return found


def missing_documents(graph: GraphStore, subsystem_eids: list[str], doc_types: list[str]) -> list[dict]:
    """[{eid, doc_type}] for every (subsystem, type) pair with no such document."""
    missing = []
    for t in doc_types:
        gaps = {r["eid"] for r in graph.artifact_gaps(t)}
        missing += [{"eid": e, "doc_type": t} for e in subsystem_eids if e in gaps]
    return missing
