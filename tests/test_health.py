"""The six-check health report against the test database. No model needed."""

import copy

import pytest
import yaml

from esamkit.config import settings
from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore
from esamkit.health import health_report
from esamkit.ontology import load_ontology
from esamkit.traceability import load_traceability

ALL_CHUNKS = {"ICD-BRK-001": 8, "SDD-BRK-001": 6, "SDD-DCL-001": 6, "SRS-BRK-001": 4, "SRS-DCL-001": 4}


@pytest.fixture
def graph(pg_dsn, baseline):
    g = PostgresCTEGraphStore(pg_dsn)
    g.reset()
    g.load(baseline)
    load_traceability(g, baseline, settings.documents_dir)
    return g


@pytest.fixture
def data():
    return yaml.safe_load(settings.baseline_yaml.read_text())


def run(graph, data, chunks=ALL_CHUNKS):
    rep = health_report(graph, load_ontology(), data, settings.documents_dir, chunks)
    return rep, {c["id"]: c for c in rep["checks"]}


def test_synth_ltv_report(graph, data):
    rep, c = run(graph, data)
    assert not rep["blocked"] and c[5]["status"] == "pass"
    assert [(f["where"], f["critical"]) for f in c[1]["findings"]] == [
        ("SUB-BRK-ECU", True), ("SUB-DCL", False), ("SUB-ENG-ECM", False), ("SUB-J1939-BB", True)]
    assert len(c[3]["findings"]) == 1 and "candidate" in c[3]["findings"][0]["message"]
    assert len(c[4]["findings"]) == 16
    roll = {r["eid"]: (r["present"], r["required"], r["percent"]) for r in c[6]["rollup"]}
    assert roll["V-001"] == (5, 9, 56) and roll["SYS-SUSP"] == (3, 4, 75) and roll["SYS-PWR"] == (0, 1, 0)


def test_integrity_blocks_on_missing_or_orphan_chunks(graph, data):
    chunks = dict(ALL_CHUNKS)
    del chunks["SDD-DCL-001"]
    chunks["STD-BRK-001"] = 6                                      # left behind by a --keep run
    rep, c = run(graph, data, chunks)
    msgs = [f["where"] + ": " + f["message"] for f in c[5]["findings"]]
    assert rep["blocked"] and c[5]["status"] == "blocks"
    assert any(m.startswith("SDD-DCL-001: listed and on disk, but has no searchable chunks") for m in msgs)
    assert any(m.startswith("STD-BRK-001: 6 searchable chunks for a document the baseline doesn't list") for m in msgs)


def test_integrity_blocks_when_graph_and_baseline_disagree(graph, data):
    data = copy.deepcopy(data)
    data["artifacts"] = [a for a in data["artifacts"] if a["id"] != "SRS-DCL-001"]
    data["vehicle"]["families"][0]["variants"][0]["systems"][1]["subsystems"][1]["os_class"] = "linux"
    rep, c = run(graph, data, {k: v for k, v in ALL_CHUNKS.items() if k != "SRS-DCL-001"})
    msgs = [f["where"] + ": " + f["message"] for f in c[5]["findings"]]
    assert rep["blocked"]
    assert any("SRS-DCL-001: in the graph but no longer in the baseline" in m for m in msgs)
    assert any("os_class = 'linux'" in m for m in msgs)


def test_traceability_reports_unknown_requirement(graph, data, baseline, tmp_path):
    from esamkit.baseline import Baseline
    (tmp_path / "STD-BRK-001.md").write_text("## TC-BRK-001 — t\nVerifies: SRS-BRK-011, SRS-BRK-999.\nMethod: Test.\n")
    overlay = Baseline(baseline.metadata, baseline.vertices, baseline.edges,
                       baseline.artifacts + [{"id": "STD-BRK-001", "name": "x", "type": "std", "revision": "A",
                                              "attached_to": "SUB-BRK-ECU", "file": "STD-BRK-001.md"}])
    load_traceability(graph, overlay, tmp_path, only={"STD-BRK-001"})
    _, c = run(graph, data)
    msgs = [f["where"] + ": " + f["message"] for f in c[4]["findings"]]
    assert "TC-BRK-001: verifies SRS-BRK-999, which no SRS defines" in msgs
    assert not any(m.startswith("SRS-BRK-011:") for m in msgs)       # now verified
