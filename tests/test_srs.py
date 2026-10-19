"""Pipeline B's deterministic core: the brief, the SRS lint and the evidence
pack, without any agent. Uses the committed brief and a reference draft."""

import pytest

from esamkit.config import PROJECT_ROOT
from esamkit.ontology import default_ontology
from esamkit.srs import check_brief, check_srs, evidence_pack, load_brief
from esamkit.standards import default_library

BRIEF = PROJECT_ROOT / "data" / "briefs" / "SRS-ENG-001.yaml"
DRAFT = (PROJECT_ROOT / "tests" / "fixtures" / "SRS-ENG-001_reference.md").read_text()


class _Graph:
    """Just enough graph: the subsystem, its candidate EBC1 interface, one brake requirement."""
    def snapshot(self):
        return {"vertices": [{"eid": "SUB-ENG-ECM", "label": "Subsystem", "props": {}},
                             {"eid": "SRS-BRK-011", "label": "Requirement", "props": {"source_artifact": "SRS-BRK-001"}}],
                "edges": [{"type": "INTERFACES_WITH", "from_eid": "SUB-J1939-BB", "to_eid": "SUB-ENG-ECM",
                           "props": {"message": "EBC1 (PGN 61441)"}}]}


@pytest.fixture(scope="module")
def ctx():
    brief = load_brief(BRIEF)
    lib, onto = default_library(), default_ontology()
    return brief, check_brief(brief, lib, onto), lib, onto


def _lint(ctx, text):
    brief, a, lib, onto = ctx
    return check_srs(text, brief, a, _Graph(), lib, onto)


def _rules(r):
    return {(f["rule"], f["where"]) for f in r["findings"] if f["level"] == "error"}


def test_brief_swci_from_table_v(ctx):
    a = ctx[1]
    assert a["ok"] and a["csci_swci"] == "SwCI 2"
    assert {x["id"]: x["swci"] for x in a["ssfs"]} == {"SSF-ENG-01": "SwCI 2", "SSF-ENG-02": "SwCI 3", "SSF-ENG-03": "SwCI 2"}
    assert any("3.2.31" in f["basis"] for f in a["findings"])      # baseline says not safety-critical


def test_reference_draft_passes(ctx):
    r = _lint(ctx, DRAFT)
    assert r["ok"], [f for f in r["findings"] if f["level"] == "error"]
    assert len(r["requirements"]) == 4


@pytest.mark.parametrize("change,expect", [
    (("Basis: MIL-STD-882E 4.4.2, DI-IPSC-81433A 3.7", "Basis: MIL-STD-882E 4.4.7"), ("standards trace", "SRS-ENG-020")),
    (("Safety: SwCI 2 | Timing: 20 ms max", "Safety: SwCI 3 | Timing: 20 ms max"), ("safety", "SRS-ENG-020")),
    (("Traces to: SSF-ENG-03, ICD-ENG-001 §2.1", "Traces to: ICD-ENG-001 §2.9"), ("traceability", "SRS-ENG-021")),
    (("### 3.6 Adaptation requirements\n\nNo requirements.\n", ""), ("outline", "3.6")),
    (("### 3.8 Security and privacy requirements\n\nNo requirements.", "### 3.8 Security and privacy requirements\n"), ("outline", "3.8")),
    (("Safety: SwCI 3 | Verification: T |", "Safety: SwCI 3 | Verification: X |"), ("qualification", "SRS-ENG-010")),
    (("**SRS-ENG-030**", "**SRS-BRK-011**"), ("requirement id", "SRS-BRK-011")),
])
def test_lint_catches(ctx, change, expect):
    old, new = change
    assert old in DRAFT
    assert expect in _rules(_lint(ctx, DRAFT.replace(old, new)))


def test_unreadable_requirement_ids_are_named(ctx):
    # a four-part id isn't read as a requirement; say so instead of reporting the SSF as unimplemented only
    r = _lint(ctx, DRAFT.replace("**SRS-ENG-020**", "**SRS-ENG-ECM-020**"))
    assert ("requirement id", "SRS-ENG-ECM-020") in _rules(r)


def test_unimplemented_safety_function_is_an_error(ctx):
    text = DRAFT.replace("Traces to: SSF-ENG-03, ICD-ENG-001 §2.1", "Traces to: ICD-ENG-001 §2.1")
    assert ("coverage", "SSF-ENG-03") in _rules(_lint(ctx, text))


def test_29148_heuristics_warn(ctx):
    text = DRAFT.replace("shall transmit EEC1 (PGN 61444) every 10 ms.",
                         "shall transmit EEC1 (PGN 61444) as appropriate and shall log TBD.")
    w = {(f["rule"], f["where"]) for f in _lint(ctx, text)["findings"] if f["level"] == "warning"}
    assert {("29148 singular (heuristic)", "SRS-ENG-030"), ("29148 unambiguous (heuristic)", "SRS-ENG-030"),
            ("29148 complete (heuristic)", "SRS-ENG-030")} <= w


def test_a_requirement_implementing_an_ssf_needs_its_swci(ctx):
    text = DRAFT.replace("Safety: SwCI 3 | Verification: T | Traces to: SSF-ENG-02", "Verification: T | Traces to: SSF-ENG-02")
    assert ("safety", "SRS-ENG-010") in _rules(_lint(ctx, text))


def test_tbd_timing_and_tool_names(ctx):
    text = DRAFT.replace("Timing: 20 ms max", "Timing: TBD").replace(
        "shall transmit EEC1 (PGN 61444) every 10 ms.", "shall transmit EEC1 (PGN 61444) every 10 ms [read_brief].")
    r = _lint(ctx, text)
    assert ("29148 complete (heuristic)", "SRS-ENG-020") in {(f["rule"], f["where"]) for f in r["findings"]}
    assert ("form", "SRS-ENG-030") in _rules(r)


def test_evidence_pack(ctx, tmp_path):
    brief, a, lib, onto = ctx
    lint = _lint(ctx, DRAFT)
    files = evidence_pack(DRAFT, brief, a, lint, lib, tmp_path,
                          {"run_id": "test", "environment": "local", "model": "none", "generated": "now"})
    index = (tmp_path / "evidence_pack.md").read_text()
    assert "not a certification" in index and "candidate" in index
    matrix = (tmp_path / "compliance_matrix.md").read_text()
    assert "| SSF-ENG-01 |" in matrix and "SwCI 2" in matrix and "4.4.2 Software Safety Criticality Matrix" in matrix
    assert (tmp_path / "traceability_matrix.csv").read_text().count("SRS-ENG-0") == 4
    assert set(files) == {"draft", "traceability_matrix", "compliance_matrix", "lint", "evidence_pack"}
