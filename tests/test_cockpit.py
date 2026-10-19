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


# ── Tasks tab ───────────────────────────────────────────────────────────────

@pytest.fixture()
def runs_dir(tmp_path, monkeypatch, pg_dsn):
    """A run folder awaiting approval, in a temporary work/runs; an empty task list."""
    from esamkit import runs
    from esamkit.tasks import TaskQueue

    monkeypatch.setattr(runs, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(runs, "DRAFTS", tmp_path / "drafts")
    folder = tmp_path / "runs" / "20261019_120000_review_STD-BRK-001"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"run_id": folder.name, "kind": "intake", "state": "awaiting_approval",
                                                   "recommendation": "INGEST", "entry": {"id": "STD-BRK-001"}}))
    (folder / "terminal.html").write_text("<html>log</html>")
    with TaskQueue(pg_dsn)._conn() as c:
        c.execute("TRUNCATE esam_tasks RESTART IDENTITY")
    return folder


def test_tasks_lists_runs_and_waiting_decisions(client, runs_dir):
    d = client.get("/api/tasks").json()
    assert d["queue"] == "ok" and d["tasks"] == []
    assert [r["state"] for r in d["runs"]] == ["awaiting_approval"]
    assert "terminal.html" in d["runs"][0]["files"]


def test_queue_ask_and_cancel(client, runs_dir):
    r = client.post("/api/tasks", json={"kind": "ask", "arg": "Which brake requirements have no test?", "by": "A. Engineer"})
    assert r.status_code == 200
    tid = r.json()["id"]
    task = client.get("/api/tasks").json()["tasks"][0]
    assert task["status"] == "queued" and task["created_by"] == "A. Engineer"
    assert client.post(f"/api/tasks/{tid}/cancel").status_code == 200
    assert client.post(f"/api/tasks/{tid}/cancel").status_code == 409


@pytest.mark.parametrize("body,code", [
    ({"kind": "ask", "arg": "q", "by": " "}, 422),                       # nobody said who
    ({"kind": "review", "arg": "/etc/passwd", "by": "x"}, 422),         # only offered files
    ({"kind": "deploy", "arg": "", "by": "x"}, 422),
    ({"kind": "approve", "arg": "20261019_999999_nope", "by": "x"}, 404),
])
def test_queue_refusals(client, runs_dir, body, code):
    assert client.post("/api/tasks", json=body).status_code == code


def test_approve_queues_a_task_under_the_se_name(client, runs_dir):
    r = client.post("/api/tasks", json={"kind": "approve", "arg": runs_dir.name, "by": "A. Engineer", "note": "checked"})
    assert r.status_code == 200 and r.json()["payload"]["run"] == runs_dir.name
    t = client.get("/api/tasks").json()["tasks"][0]
    assert t["kind"] == "approve" and t["created_by"] == "A. Engineer"


def test_approve_against_the_recommendation_needs_a_note(client, runs_dir):
    s = json.loads((runs_dir / "state.json").read_text())
    (runs_dir / "state.json").write_text(json.dumps({**s, "recommendation": "DO NOT INGEST"}))
    body = {"kind": "approve", "arg": runs_dir.name, "by": "A. Engineer"}
    assert client.post("/api/tasks", json=body).status_code == 409
    assert client.post("/api/tasks", json={**body, "against_recommendation": True}).status_code == 409
    assert client.post("/api/tasks", json={**body, "against_recommendation": True, "note": "ICD rev B fixes it"}).status_code == 200


def test_reject_records_who_and_why(client, runs_dir):
    url = f"/api/runs/{runs_dir.name}/reject"
    assert client.post(url, json={"by": "A. Engineer", "note": ""}).status_code == 409
    r = client.post(url, json={"by": "A. Engineer", "note": "wrong revision"})
    assert r.status_code == 200 and r.json()["approval"]["by"] == "A. Engineer"
    assert client.post(url, json={"by": "A. Engineer", "note": "again"}).status_code == 409    # no longer awaiting


def test_run_files_are_served_only_from_run_folders(client, runs_dir):
    assert client.get(f"/runs/{runs_dir.name}/terminal.html").text == "<html>log</html>"
    assert client.get(f"/runs/{runs_dir.name}/state.py").status_code == 404
    assert client.get(f"/runs/{runs_dir.name}/..%2Fstate.json").status_code == 404
    assert client.get("/runs/..%2F..%2F/.env").status_code == 404


def test_page_has_the_tasks_tab():
    page = render_page()
    assert 'id="tab-tasks"' in page and 'id="task-form"' in page and "renderTasks" in page
