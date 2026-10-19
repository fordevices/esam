"""Coverage rules (pure) and the prompt's MISSING section. No model needed."""

from esamkit.coverage import doc_types_needed, mentioned_subsystems, missing_documents
from esamkit.hybrid import build_prompt

SUBS = [{"eid": "SUB-ENG-ECM", "name": "Engine Control Module"}, {"eid": "SUB-DCL", "name": "Driver Digital Cluster"}]


def test_doc_types_from_question_words():
    assert doc_types_needed("How is SRS-BRK-011 verified? Give the test steps.") == ["std"]
    assert doc_types_needed("Does the ECM use the EBC1 brake message?") == ["icd"]
    assert doc_types_needed("What colour is the cab?") == []


def test_mentioned_subsystems_by_name_or_short_id():
    assert mentioned_subsystems("Does the Engine Control Module use EBC1?", SUBS) == ["SUB-ENG-ECM"]
    assert mentioned_subsystems("What does the DCL show?", SUBS) == ["SUB-DCL"]
    assert mentioned_subsystems("What is the brake period?", SUBS) == []


def test_missing_documents_against_graph(pg_dsn, baseline):
    from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore
    g = PostgresCTEGraphStore(pg_dsn)
    g.reset()
    g.load(baseline)
    assert missing_documents(g, ["SUB-BRK-ECU"], ["std", "icd"]) == [{"eid": "SUB-BRK-ECU", "doc_type": "std"}]


def test_prompt_states_missing_document():
    entry = {"subsystem": "SUB-BRK-ECU", "artifact": "SSR-BRK-001", "score": 0.8, "origin": "SUB-BRK-ECU"}
    cov = {"needed": ["std"], "focus": ["SUB-BRK-ECU"], "missing": [{"eid": "SUB-BRK-ECU", "doc_type": "std"}]}
    p = build_prompt("How is it tested?", entry, [], [], cov)
    assert "MISSING: SUB-BRK-ECU has no test description (STD)" in p
    cov["missing"] = []
    assert "All needed documents exist." in build_prompt("q", entry, [], [], cov)
    assert "DOCUMENT COVERAGE" not in build_prompt("q", entry, [], [])     # lesson 05 callers unchanged
