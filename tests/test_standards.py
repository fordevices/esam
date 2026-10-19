"""The standards library: citations resolve only to real clauses, and the
tables held as data match the text extracted from the standard."""

import re

import pytest

from esamkit.standards import default_library

LIB = default_library()


@pytest.mark.parametrize("citation,clause", [
    ("MIL-STD-882E 4.4.2", "4.4.2"), ("MIL-STD-882E B.2.2.5a", "B.2.2.5"), ("MIL-STD-882E Table V", "4.4.2"),
    ("MIL-STD-882E Table IV", "4.4.1"), ("MIL-STD-882E 3.2.31", "3.2.31"), ("DI-IPSC-81433A 3.7", "3.7"),
    ("DI-IPSC-81433A 3.3.xe", "3.3.x"), ("DI-IPSC-81433A 4b", "4")])
def test_real_citations_resolve(citation, clause):
    r = LIB.resolve(citation)
    assert r["ok"] and r["clause"] == clause and r["text"]


@pytest.mark.parametrize("citation", [
    "MIL-STD-882E 4.4.9", "MIL-STD-882E B.2.2.5k", "MIL-STD-882 4.4.2", "DO-178C 6.3",
    "ISO/IEC/IEEE-29148 5.2.5", "MIL-STD-882E", "DI-IPSC-81433A 3.19"])
def test_made_up_or_unheld_citations_do_not(citation):
    assert not LIB.resolve(citation)["ok"]


def test_table_v_matches_the_extracted_text():
    text = LIB.resolve("MIL-STD-882E Table V")["text"]
    for scc in range(1, 6):
        row = re.search(rf"^\s*{scc}\s+(SwCI \d)\s+(SwCI \d)\s+(SwCI \d)\s+(SwCI \d)\s*$", text, re.M)
        assert row, scc
        for col, sev in enumerate(["Catastrophic", "Critical", "Marginal", "Negligible"]):
            assert LIB.swci(sev, scc) == row[col + 1]
    # the layout puts the 'SwCI n' label inside a hyphenated line break: take labels out first
    flat = " ".join(re.sub(r"-\s+SwCI \d\s+", "-", text).split())
    for swci in ("SwCI 1", "SwCI 2", "SwCI 3", "SwCI 4"):
        lor = " ".join(LIB.level_of_rigor(swci).split())
        assert lor in flat or lor in " ".join(re.sub(r"\s+SwCI \d\s+", " ", text).split()), swci


def test_table_vi_risk_levels_match_the_extracted_text():
    flat = " ".join(LIB.resolve("MIL-STD-882E Table VI")["text"].split())
    for swci in ("SwCI 1", "SwCI 2", "SwCI 3", "SwCI 4"):
        assert f"{swci} {LIB.unmet_lor_risk(swci)}" in flat


def test_did_outline_is_complete():
    want = ["1", "1.1", "1.2", "1.3", "2", "3", "3.1", "3.2", "3.3", "3.3.1", "3.4", "3.5", "3.6", "3.7", "3.8",
            "3.9", "3.10", "3.10.1", "3.10.2", "3.10.3", "3.10.4", "3.11", "3.12", "3.13", "3.14", "3.15", "3.16",
            "3.17", "3.18", "4", "5", "6"]
    assert set(want) <= set(LIB.clauses("DI-IPSC-81433A"))


def test_copyrighted_standard_is_held_by_name_only():
    s = LIB.standards["ISO/IEC/IEEE-29148"]
    assert s["text"] is None and "singular" in s["characteristics"]
