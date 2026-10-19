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
    assert data["doc_types"] == ["icd", "sdd", "srs", "std"]
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
