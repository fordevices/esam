"""PgVectorStore mechanics with tiny hand-made vectors. No model needed."""

import math

import pytest

from esamkit.vectorstores import Chunk, ModelMismatchError
from esamkit.vectorstores.pgvector_store import PgVectorStore


def unit(*xs):
    n = math.sqrt(sum(x * x for x in xs))
    return [x / n for x in xs]


@pytest.fixture
def store(pg_dsn):
    s = PgVectorStore(pg_dsn, "pytest_vectors", "toy-3d", 3)
    s.reset()
    yield s
    s.reset()


def chunk(i, src, art, **meta):
    return Chunk(id=f"{art}::{i}", text=f"text {i}", source_file=src, chunk_index=i,
                 artifact_id=art, metadata=meta)


def test_nearest_first_and_cosine_scores(store):
    store.upsert([chunk(0, "a.md", "A"), chunk(1, "b.md", "B")],
                 [unit(1, 0, 0), unit(0, 1, 0)])
    hits = store.search(unit(1, 0.1, 0), k=2)
    assert [h.chunk.artifact_id for h in hits] == ["A", "B"]
    assert hits[0].score == pytest.approx(0.995, abs=1e-3)
    assert hits[1].score == pytest.approx(0.0995, abs=1e-3)


def test_upsert_replaces_by_id_and_delete_source(store):
    c = chunk(0, "a.md", "A")
    store.upsert([c], [unit(1, 0, 0)])
    store.upsert([c], [unit(0, 1, 0)])                       # same id: replace, not duplicate
    assert store.count() == 1
    assert store.delete_source("a.md") == 1
    assert store.count() == 0


def test_filters_on_columns_and_metadata(store):
    store.upsert([chunk(0, "a.md", "A", wbs_code="1.1.1.5"),
                  chunk(1, "b.md", "B", wbs_code="1.1.1.6")],
                 [unit(1, 0, 0), unit(1, 0.2, 0)])
    q = unit(1, 0, 0)
    assert [h.chunk.artifact_id for h in store.search(q, where={"artifact_id": "B"})] == ["B"]
    assert [h.chunk.artifact_id for h in store.search(q, where={"wbs_code": "1.1.1.6"})] == ["B"]
    assert store.search(q, where={"wbs_code": "9.9"}) == []


def test_model_guard(store, pg_dsn):
    with pytest.raises(ModelMismatchError):
        PgVectorStore(pg_dsn, "pytest_vectors", "some-other-model", 3)


def test_rejects_unsafe_collection_name(pg_dsn):
    with pytest.raises(ValueError):
        PgVectorStore(pg_dsn, "x; DROP TABLE y", "toy", 3)
