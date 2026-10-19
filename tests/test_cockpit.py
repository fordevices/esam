"""SE Cockpit API + page rendering, against the test database (esam_test)."""

import json
import re

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from cockpit.app import app, get_graph, get_runs_dsn, render_page  # noqa: E402
from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore  # noqa: E402


@pytest.fixture(scope="module")
def client(pg_dsn, baseline):
    g = PostgresCTEGraphStore(pg_dsn)
    g.reset()
    g.load(baseline)
    app.dependency_overrides[get_graph] = lambda: g
    app.dependency_overrides[get_runs_dsn] = lambda: pg_dsn
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "vertices": 20, "edges": 22}


def test_cockpit_payload(client):
    data = client.get("/api/cockpit").json()
    assert len(data["graph"]["vertices"]) == 20 and len(data["graph"]["edges"]) == 22
    assert data["doc_types"] == ["srs", "sdd", "icd", "std"]          # every type a rule can require
    # what each subsystem must have comes from the ontology's rules
    assert {k: sorted(v) for k, v in data["required"].items()} == {
        "SUB-BRK-ECU": ["icd", "sdd", "srs", "std"], "SUB-DCL": ["icd", "sdd", "srs"],
        "SUB-J1939-BB": ["icd"], "SUB-ENG-ECM": ["icd"]}
    # the headline finding: the safety-critical brake ECU has no test description
    assert "SUB-BRK-ECU" in data["gaps"]["std"]
    assert "SUB-BRK-ECU" not in data["gaps"]["icd"]


def test_cockpit_shows_latest_run(client, pg_dsn):
    from esamkit.runlog import save_run
    save_run(pg_dsn, "missing-document", "Missing-document confidence test", True, {"scenarios": []})
    runs = {r["name"]: r for r in client.get("/api/cockpit").json()["runs"]}
    assert runs["missing-document"]["passed"] is True and runs["missing-document"]["run_at"]


def test_cockpit_types_param(client):
    data = client.get("/api/cockpit", params={"types": "ICD, srs"}).json()
    assert data["doc_types"] == ["icd", "srs"] and set(data["gaps"]) == {"icd", "srs"}


def test_index_is_live_page(client):
    html = client.get("/").text
    assert html.startswith("<!doctype html>") and "<title>ESAM SE Cockpit</title>" in html
    assert '<script id="esam-data" type="application/json">null</script>' in html


def test_snapshot_bakes_data_safely():
    data = {"graph": {"vertices": [{"eid": "X", "label": "Vehicle", "props": {"name": "</script><b>"}}], "edges": []},
            "doc_types": [], "gaps": {}, "generated_at": "2026-10-19T00:00:00+00:00"}
    page = render_page(data, source="snapshot")
    blob = re.search(r'<script id="esam-data" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert "</script>" not in blob                                  # can't break out of the block
    assert json.loads(blob)["graph"]["vertices"][0]["props"]["name"] == "</script><b>"
    assert json.loads(blob)["source"] == "snapshot"
    assert not page.lstrip().startswith("<!doctype")                # fragment: publishable as-is


def test_ontology_tab_payload(client):
    o = client.get("/api/ontology").json()
    assert o["path"].endswith("data/ontology/esam_ontology.yaml") and o["relative_path"] == "data/ontology/esam_ontology.yaml"
    assert o["check"] == []                                        # the committed baseline passes
    assert {"document_types", "rules", "classes", "relationships", "value_sets", "placement"} <= set(o["data"])
    assert client.get("/api/cockpit").json()["ontology"]["path"] == o["path"]   # snapshots carry it too


def test_health_report_blocks_when_nothing_is_searchable(client):
    # The test database has no ingested chunks, so integrity must block.
    rep = client.get("/api/health-report").json()
    assert rep["blocked"] and [c["status"] for c in rep["checks"]][4] == "blocks"
    assert any("no searchable chunks" in f["message"] for f in rep["checks"][4]["findings"])
    assert client.get("/api/cockpit").json()["health"]["blocked"] is True
