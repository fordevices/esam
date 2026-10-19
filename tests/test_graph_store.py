"""Graph store against real Postgres (database esam_test). No model needed."""

import pytest

from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore

EBC1 = "EBC1 (PGN 61441)"


@pytest.fixture(scope="module")
def graph(pg_dsn, baseline):
    g = PostgresCTEGraphStore(pg_dsn)
    g.reset()
    g.load(baseline)
    return g


def test_load_is_idempotent(graph, baseline):
    # the earlier script duplicated every edge on each re-run
    assert graph.load(baseline) == {"vertices": 20, "edges": 22}
    assert graph.load(baseline) == {"vertices": 20, "edges": 22}


def test_descendants_cover_whole_tree(graph):
    rows = graph.descendants("V-001")
    assert len(rows) == 15                                  # everything except the 5 artifacts
    assert rows[0]["eid"] == "V-001" and rows[0]["depth"] == 0
    assert max(r["depth"] for r in rows) == 5               # vehicle .. function


def test_ancestors_trace_document_to_wbs(graph):
    chain = [(r["eid"], r["wbs_code"]) for r in graph.ancestors("ICD-BRK-001")]
    assert chain == [("ICD-BRK-001", None), ("SUB-BRK-ECU", None), ("SYS-SUSP", "1.1.1.5"),
                     ("VAR-001", "1.1.1"), ("FAM-001", "1.1"), ("V-001", "1.0")]


def test_impact_is_multi_hop_and_flags_candidates(graph):
    rows = {r["eid"]: r for r in graph.impact("SUB-BRK-ECU", message=EBC1)}
    assert set(rows) == {"SUB-J1939-BB", "SUB-DCL", "SUB-ENG-ECM"}
    assert rows["SUB-J1939-BB"]["depth"] == 1
    assert rows["SUB-DCL"]["depth"] == 2 and rows["SUB-DCL"]["status"] == "confirmed"
    assert rows["SUB-ENG-ECM"]["status"] == "candidate"     # unreviewed edge on the path
    assert rows["SUB-DCL"]["artifacts"] == ["SSDD-DCL-001", "SSR-DCL-001"]
    assert rows["SUB-ENG-ECM"]["artifacts"] == []            # undocumented, yet found


def test_impact_respects_depth_and_message(graph):
    assert [r["eid"] for r in graph.impact("SUB-BRK-ECU", max_depth=1)] == ["SUB-J1939-BB"]
    assert graph.impact("SUB-BRK-ECU", message="DM1 (PGN 65226)") == []
    assert graph.impact("SUB-DCL", message=EBC1) == []       # nothing downstream of a receiver


def test_message_origin_is_the_producer_not_the_relay(graph):
    assert [r["eid"] for r in graph.message_origins(EBC1)] == ["SUB-BRK-ECU"]


def test_artifact_gaps(graph):
    assert [r["eid"] for r in graph.artifact_gaps("ssr")] == ["SUB-J1939-BB", "SUB-ENG-ECM"]


def test_snapshot_uses_entity_ids(graph):
    snap = graph.snapshot()
    eids = {v["eid"] for v in snap["vertices"]}
    assert len(eids) == 20 and len(snap["edges"]) == 22
    # every edge endpoint is an entity id the vertex list contains (no UUIDs leak)
    assert all(e["from_eid"] in eids and e["to_eid"] in eids for e in snap["edges"])
    candidate = [e for e in snap["edges"] if e["props"].get("status") == "candidate"]
    assert [(e["from_eid"], e["to_eid"]) for e in candidate] == [("SUB-J1939-BB", "SUB-ENG-ECM")]
