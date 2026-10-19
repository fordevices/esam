"""Lesson 06 scenarios end to end, WITHOUT the chat model (deterministic checks).
Needs the staged embedding model; uses its own collection in esam_test."""

import importlib.util

import pytest

from esamkit.config import PROJECT_ROOT, settings

LESSON = PROJECT_ROOT / "lessons" / "06_missing_document"


@pytest.fixture(scope="module")
def stack(pg_dsn, baseline):
    if not (settings.embed_model_dir / "config.json").exists():
        pytest.skip("model not staged; run lessons/00_setup/stage_model.py")
    from esamkit.embedders.local_st import LocalSentenceTransformerEmbedder
    from esamkit.graphstores.postgres_cte import PostgresCTEGraphStore
    from esamkit.ingest import ingest_file
    from esamkit.vectorstores.pgvector_store import PgVectorStore

    emb = LocalSentenceTransformerEmbedder(settings.embed_model_dir, settings.embed_model_id)
    store = PgVectorStore(pg_dsn, "pytest_lesson06", emb.model_id, emb.dim)
    store.reset()
    for rel, facts in baseline.artifact_index().items():
        ingest_file(settings.documents_dir / rel, rel, facts, emb, store)
    yield emb, store, PostgresCTEGraphStore(pg_dsn)
    store.reset()


def _scenarios():
    spec = importlib.util.spec_from_file_location("lesson06_scenarios", LESSON / "scenarios.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scenarios()


@pytest.mark.parametrize("name", ["brake-std", "engine-icd"])
def test_scenario_deterministic_checks(stack, tmp_path, name):
    from esamkit.confidence import run_scenario

    emb, store, graph = stack
    sc = next(s for s in _scenarios() if s.name == name)
    r = run_scenario(sc, emb, store, graph, settings.baseline_yaml, tmp_path, ask=None)
    assert r["passed"], [c for ph in ("before", "after") for c in r[ph]["checks"] if not c["ok"]]
    assert sc.artifact["id"] not in r["before"]["evidence"]
    assert sc.artifact["id"] in r["after"]["evidence"]


def test_restore_removes_scenario_documents(stack, baseline):
    from esamkit.confidence import restore

    emb, store, graph = stack
    restore(_scenarios(), store, graph, baseline)
    assert graph.load(baseline) == {"vertices": 20, "edges": 22}
    assert not store.search(emb.embed_query("test case"), k=50, where={"artifact_id": "STD-BRK-001"})
