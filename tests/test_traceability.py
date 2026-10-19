"""Requirements and test cases read from the documents, loaded as vertices, and queried."""

import pytest

from esamkit.config import PROJECT_ROOT, settings
from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore
from esamkit.hybrid import build_prompt
from esamkit.traceability import extract, load_traceability, read_requirements, read_test_cases

STAGED = PROJECT_ROOT / "lessons" / "06_missing_document" / "staged"


def test_requirements_read_from_srs():
    ex = read_requirements((settings.documents_dir / "SRS-BRK-001.md").read_text(), "SRS-BRK-001", "SUB-BRK-ECU")
    ids = [v.eid for v in ex.vertices]
    assert ids == ["SRS-BRK-010", "SRS-BRK-011", "SRS-BRK-012", "SRS-BRK-020", "SRS-BRK-021",
                   "SRS-BRK-022", "SRS-BRK-030", "SRS-BRK-031", "SRS-BRK-032"]
    r011 = ex.vertices[1].props
    assert r011["text"].startswith("The software shall release brake pressure") and "lock-up on that wheel." in r011["text"]
    assert r011["section"] == "1. Control requirements" and r011["subsystem"] == "SUB-BRK-ECU"
    assert {(e.type, e.to_eid) for e in ex.edges if e.from_eid == "SRS-BRK-011"} == {
        ("SPECIFIED_IN", "SRS-BRK-001"), ("ALLOCATED_TO", "SUB-BRK-ECU")}


def test_test_cases_read_from_std():
    ex = read_test_cases((STAGED / "STD-BRK-001.md").read_text(), "STD-BRK-001")
    tc = {v.eid: v.props for v in ex.vertices}
    assert list(tc) == ["TC-BRK-010", "TC-BRK-011", "TC-BRK-021", "TC-BRK-030"]
    assert tc["TC-BRK-011"]["verifies"] == ["SRS-BRK-011"] and tc["TC-BRK-011"]["method"] == "Test"
    assert "within 15 ms" in tc["TC-BRK-011"]["pass_criteria"]
    assert ("TC-BRK-011", "SRS-BRK-011", "VERIFIES") in {(e.from_eid, e.to_eid, e.type) for e in ex.edges}


@pytest.fixture
def graph(pg_dsn, baseline):
    g = PostgresCTEGraphStore(pg_dsn)
    g.reset()
    g.load(baseline)
    return g


def test_load_and_query(graph, baseline, tmp_path):
    t = load_traceability(graph, baseline, settings.documents_dir)
    assert (t["requirements"], t["test_cases"], t["verifies"]) == (16, 0, 0)
    assert len(graph.untested_requirements()) == 16                     # no STD exists yet
    summary = {r["eid"]: (r["requirements"], r["verified"]) for r in graph.trace_summary()}
    assert summary["SUB-BRK-ECU"] == (9, 0) and summary["SUB-DCL"] == (7, 0) and summary["SUB-ENG-ECM"] == (0, 0)

    # an STD arrives: its test cases now verify four brake requirements
    std = baseline.artifacts + [{"id": "STD-BRK-001", "name": "STD", "type": "std", "revision": "A",
                                 "attached_to": "SUB-BRK-ECU", "file": "STD-BRK-001.md"}]
    from esamkit.baseline import Baseline
    overlay = Baseline(baseline.metadata, baseline.vertices, baseline.edges, std)
    graph.load(Baseline({}, [], [], []))
    t = load_traceability(graph, overlay, STAGED, only={"STD-BRK-001"})
    assert (t["test_cases"], t["verifies"]) == (4, 4)
    assert [r["eid"] for r in graph.verified_by("SRS-BRK-011")] == ["TC-BRK-011"]
    assert len(graph.untested_requirements()) == 12

    # a new revision drops a test case citing a typo: old test cases go, the typo is reported
    rev_b = tmp_path / "STD-BRK-001.md"
    rev_b.write_text("## TC-BRK-099 — Typo test\nVerifies: SRS-BRK-999.\nMethod: Test.\n")
    t = load_traceability(graph, overlay, tmp_path, only={"STD-BRK-001"})
    assert t["unresolved"] == [{"test_case": "TC-BRK-099", "cites": "SRS-BRK-999"}]
    assert graph.existing_ids("TestCase") == {"TC-BRK-099"}
    assert len(graph.untested_requirements()) == 16


def test_extract_reads_only_srs_and_std(baseline):
    ex = extract(baseline, settings.documents_dir)
    assert {v.label for v in ex.vertices} == {"Requirement"}
    assert {v.props["source_artifact"] for v in ex.vertices} == {"SRS-BRK-001", "SRS-DCL-001"}


def test_prompt_states_untested_requirement():
    entry = {"subsystem": "SUB-BRK-ECU", "artifact": "SRS-BRK-001", "score": 0.8, "origin": "SUB-BRK-ECU"}
    cov = {"needed": ["std"], "focus": ["SUB-BRK-ECU"], "missing": [], "trace": [
        {"requirement": "SRS-BRK-011", "exists": True, "verified_by": []},
        {"requirement": "SRS-BRK-010", "exists": True, "verified_by": [{"eid": "TC-BRK-010", "method": "Test", "document": "STD-BRK-001"}]},
        {"requirement": "SRS-BRK-999", "exists": False, "verified_by": []}]}
    p = build_prompt("q", entry, [], [], cov)
    assert "MISSING: no test case verifies SRS-BRK-011." in p
    assert "SRS-BRK-010 is verified by TC-BRK-010 (Test) in [STD-BRK-001]" in p
    assert "UNKNOWN: SRS-BRK-999 is not a requirement" in p
