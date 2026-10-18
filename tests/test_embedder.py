"""The real local BGE embedder. Skipped if the model hasn't been staged."""

import numpy as np
import pytest

from esamkit.config import settings


@pytest.fixture(scope="module")
def emb():
    if not (settings.embed_model_dir / "config.json").exists():
        pytest.skip("model not staged; run lessons/00_setup/stage_model.py")
    from esamkit.embedders.local_st import LocalSentenceTransformerEmbedder
    return LocalSentenceTransformerEmbedder(settings.embed_model_dir, settings.embed_model_id)


def test_dimension_and_normalization(emb):
    v = emb.embed_documents(["The brake ECU broadcasts EBC1 every 100 ms."])[0]
    assert emb.dim == 768 and len(v) == 768
    assert np.linalg.norm(v) == pytest.approx(1.0, abs=1e-5)


def test_deterministic(emb):
    a = emb.embed_documents(["same text"])[0]
    b = emb.embed_documents(["same text"])[0]
    assert np.allclose(a, b, atol=1e-6)


def test_query_instruction_is_applied(emb):
    # the query path adds BGE's instruction, so it must differ from the raw text
    q = emb.embed_query("brake data lost")
    raw = emb.embed_raw(["brake data lost"])[0]
    assert not np.allclose(q, raw, atol=1e-3)


def test_meaning_beats_shared_words(emb):
    anchor = emb.embed_documents(["The ABS prevents the wheels from locking under hard braking."])[0]
    same_meaning, same_word = emb.embed_documents([
        "Anti-skid control keeps tyres rolling during an emergency stop.",
        "The crew door lock actuator reported a fault.",
    ])
    assert np.dot(anchor, same_meaning) > np.dot(anchor, same_word)
