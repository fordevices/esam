"""Pipeline A's deterministic core (esamkit.intake), without any agent:
read a document, check it against the ontology, review it, ingest it,
verify it, undo it. Ingest runs on a COPY of the baseline and documents."""

import shutil
from dataclasses import replace

import pytest
import yaml

from esamkit.config import PROJECT_ROOT, settings
from esamkit.intake import baseline_line, ontology_check, read_header
from esamkit.ontology import default_ontology

STAGED = PROJECT_ROOT / "lessons" / "06_missing_document" / "staged" / "STD-BRK-001.md"


def test_read_header_from_title_and_table():
    e = read_header(STAGED.read_text())
    assert e == {"id": "STD-BRK-001", "name": "Brake Control ECU Software Test Description", "type": "std",
                 "revision": "A", "attached_to": "SUB-BRK-ECU", "file": "STD-BRK-001.md"}
    # the baseline line is valid YAML that reads back as the same entry
    assert yaml.safe_load(baseline_line(e))[0] == e


def test_read_header_infers_type_but_not_revision_or_subsystem():
    e = read_header("# SRS-ENG-001 — Engine ECM Software Requirements\n\nno table here\n")
    assert e["type"] == "srs" and "revision" not in e and "attached_to" not in e


def test_ontology_check_reports_only_what_the_new_document_adds():
    e = read_header(STAGED.read_text())
    ok = ontology_check(default_ontology(), settings.baseline_yaml, e)
    assert ok["ok"] and ok["findings"] == []
    assert ok["required_by_rules"] and ok["subsystem"]["safety_critical"]
    bad = ontology_check(default_ontology(), settings.baseline_yaml,
                         dict(e, type="sdd", revision="a1", attached_to="SUB-NOPE"))
    msgs = " | ".join(f["message"] for f in bad["findings"])
    assert not bad["ok"] and "id says STD" in msgs and "revision" in msgs and "SUB-NOPE" in msgs


def test_ontology_check_needs_a_later_revision_to_replace():
    same = {"id": "SRS-BRK-001", "name": "x", "type": "srs", "revision": "A", "attached_to": "SUB-BRK-ECU",
            "file": "SRS-BRK-001.md"}
    assert not ontology_check(default_ontology(), settings.baseline_yaml, same)["ok"]
    later = ontology_check(default_ontology(), settings.baseline_yaml, dict(same, revision="B"))
    assert later["ok"] and later["replaces_revision"] == "A"


@pytest.fixture()
def bench(pg_dsn, baseline, tmp_path):
    """A copy of the baseline + documents, its own collection, the test database's graph."""
    if not (settings.embed_model_dir / "config.json").exists():
        pytest.skip("model not staged; run lessons/00_setup/stage_model.py")
    from esamkit.embedders.local_st import LocalSentenceTransformerEmbedder
    from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore
    from esamkit.ingest import ingest_file
    from esamkit.intake import reload_graph
    from esamkit.vectorstores.pgvector_store import PgVectorStore

    docs = tmp_path / "documents"
    shutil.copytree(settings.documents_dir, docs)
    base_yaml = tmp_path / "baseline.yaml"
    shutil.copy2(settings.baseline_yaml, base_yaml)
    s = replace(settings, baseline_yaml=base_yaml, documents_dir=docs, pg_dsn=pg_dsn, collection="pytest_intake")
    emb = LocalSentenceTransformerEmbedder(settings.embed_model_dir, settings.embed_model_id)
    store = PgVectorStore(pg_dsn, s.collection, emb.model_id, emb.dim)
    store.reset()
    for rel, facts in baseline.artifact_index().items():
        ingest_file(docs / rel, rel, facts, emb, store, s)
    graph = PostgresCTEGraphStore(pg_dsn)
    reload_graph(graph, s)
    yield s, emb, store, graph, tmp_path
    store.reset()


def test_review_ingest_verify_undo(bench):
    from esamkit.health import report_for
    from esamkit.intake import health_diff, ingest, review_document, undo, verify_ingestion

    s, emb, store, graph, tmp = bench
    entry = read_header(STAGED.read_text())
    original = s.baseline_yaml.read_text()

    review = review_document(STAGED.read_text(), entry, graph, s)
    assert review["issues"] == []
    assert [c["id"] for c in review["test_cases"]] == ["TC-BRK-010", "TC-BRK-011", "TC-BRK-021", "TC-BRK-030"]
    tc011 = next(c for c in review["test_cases"] if c["id"] == "TC-BRK-011")
    assert "15 ms" in tc011["verifies"][0]["text"]                   # the requirement text, for the comparison
    assert "SRS-BRK-012" in review["still_untested"]

    before = report_for(graph, s)
    manifest = ingest(STAGED, entry, emb, store, graph, tmp / "replaced", s)
    assert manifest["chunks"] == review["chunks"] and manifest["traceability"]["test_cases"] == 4
    assert s.baseline_yaml.read_text() == original.rstrip("\n") + "\n" + manifest["line"] + "\n"
    assert "# Artifacts." in s.baseline_yaml.read_text()             # the SE's comments survive

    after = report_for(graph, s)
    res = verify_ingestion(entry, review, manifest, emb, store, graph, after, s)
    assert res["verified"], [c for c in res["checks"] if not c["ok"]]
    diff = health_diff(before, after)
    assert any("SUB-BRK-ECU" in k and "std" in k.lower() for k in diff["closed"]), diff["closed"]
    assert not diff["opened"]

    out = undo(manifest, store, graph, None, s)
    assert out["baseline_line_removed"] and out["chunks_removed"] == manifest["chunks"]
    assert s.baseline_yaml.read_text() == original
    assert not (s.documents_dir / "STD-BRK-001.md").exists()
    assert graph.existing_ids("TestCase") == set()
