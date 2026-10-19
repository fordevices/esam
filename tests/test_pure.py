"""Pure-Python tests: no database, no model."""

import pytest

from esamkit.baseline import load_baseline
from esamkit.chunking import chunk_markdown
from esamkit.config import settings


# ── baseline ────────────────────────────────────────────────────────────────

def test_baseline_counts(baseline):
    labels = [v.label for v in baseline.vertices]
    assert len(baseline.vertices) == 20
    assert labels.count("Subsystem") == 4
    assert labels.count("DesignArtifact") == 5
    types = [e.type for e in baseline.edges]
    assert (types.count("PART_OF"), types.count("HAS_ARTIFACT"), types.count("INTERFACES_WITH")) == (14, 5, 3)


def test_wbs_codes_are_real_881f_appendix_g(baseline):
    # G.3 table: 1.1.1.5 Suspension/Steering, .6 Vehicle Electronics, .7 Power Package/Drive Train
    systems = {v.eid: v.props["wbs_code"] for v in baseline.vertices if v.label == "System"}
    assert systems == {"SYS-SUSP": "1.1.1.5", "SYS-VETR": "1.1.1.6", "SYS-PWR": "1.1.1.7"}


def test_subsystems_have_no_invented_wbs_code(baseline):
    assert all("wbs_code" not in v.props for v in baseline.vertices if v.label == "Subsystem")


def test_artifact_index_links_documents_to_wbs(baseline):
    idx = baseline.artifact_index()
    assert idx["ICD-BRK-001.md"]["wbs_code"] == "1.1.1.5"
    assert idx["SRS-DCL-001.md"]["subsystem_id"] == "SUB-DCL"
    assert idx["SRS-DCL-001.md"]["wbs_code"] == "1.1.1.6"
    # every listed document exists on disk
    for f in idx:
        assert (settings.documents_dir / f).exists(), f


def test_dangling_edge_is_rejected(tmp_path):
    bad = settings.baseline_yaml.read_text().replace("to: SUB-DCL", "to: SUB-TYPO")
    p = tmp_path / "bad.yaml"
    p.write_text(bad)
    with pytest.raises(ValueError, match="SUB-TYPO"):
        load_baseline(p)


# ── chunking ────────────────────────────────────────────────────────────────

def test_chunks_carry_document_and_section_label():
    text = (settings.documents_dir / "SDD-BRK-001.md").read_text()
    chunks = chunk_markdown(text, 600, 80)
    task_set = next(c for c in chunks if "Task set" in c.splitlines()[0])
    assert task_set.startswith("[SDD-BRK-001 — Brake Control ECU Software Design › 2. Task set]")


def test_chunks_respect_size_and_never_split_words():
    words = " ".join(f"word{i}" for i in range(400))       # one 3 KB paragraph, no sentences
    chunks = chunk_markdown("# T\n\n## S\n\n" + words, size=300, overlap=40)
    assert len(chunks) > 5
    vocab = set(words.split())
    for c in chunks:
        body = c.split("\n", 1)[1]                          # drop the "[T › S]" label
        assert len(body) <= 300 + 40 + 5                    # size + overlap carry + ellipsis
        for w in body.replace("…", " ").split():
            assert w in vocab, f"split word: {w!r}"


def test_overlap_does_not_cross_sections():
    text = "# T\n\n## A\n\n" + "alpha " * 120 + "\n\n## B\n\n" + "beta " * 20
    chunks = chunk_markdown(text, size=300, overlap=60)
    b = next(c for c in chunks if "› B]" in c)
    assert "alpha" not in b
