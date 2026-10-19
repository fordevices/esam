"""The ontology is self-consistent, the baseline passes it, and it catches mistakes. No database."""

import copy

import pytest
import yaml

from esamkit.config import settings
from esamkit.ontology import check_baseline, load_ontology


@pytest.fixture(scope="module")
def onto():
    return load_ontology()


@pytest.fixture
def data():
    return yaml.safe_load(settings.baseline_yaml.read_text())


def _sub(data, eid):
    for fam in data["vehicle"]["families"]:
        for var in fam["variants"]:
            for sysm in var["systems"]:
                for sub in sysm["subsystems"]:
                    if sub["id"] == eid:
                        return sub
    raise KeyError(eid)


def messages(findings):
    return [f"{f.level} {f.where}: {f.message}" for f in findings]


def test_ontology_is_self_consistent(onto):
    assert onto.check_self() == []


def test_committed_baseline_passes(onto, data):
    assert messages(check_baseline(onto, data, settings.documents_dir)) == []


def test_misspelt_attribute_is_reported_not_dropped(onto, data):
    _sub(data, "SUB-DCL")["safty_critical"] = True
    assert any("'safty_critical' is not in the ontology" in m for m in messages(check_baseline(onto, data)))


def test_values_outside_the_vocabulary(onto, data):
    _sub(data, "SUB-BRK-ECU")["os_class"] = "hard-real-time"
    _sub(data, "SUB-BRK-ECU")["safety_classification"] = "very critical"
    data["interfaces"][2]["status"] = "maybe"
    m = messages(check_baseline(onto, data))
    assert any("os_class = 'hard-real-time': not an allowed value" in x for x in m)
    assert any("safety_classification = 'very critical'" in x for x in m)
    assert any("status = 'maybe'" in x for x in m)


def test_old_document_names_are_rejected(onto, data):
    art = next(a for a in data["artifacts"] if a["id"] == "SRS-BRK-001")
    art["type"] = "ssr"                                    # the pre-MIL-STD-498 name
    m = messages(check_baseline(onto, data))
    assert any("type = 'ssr': not an allowed value" in x for x in m)
    assert any("id says SRS but type is 'ssr'" in x for x in m)


def test_structure_errors(onto, data):
    bad = copy.deepcopy(data)
    bad["vehicle"]["families"][0]["variants"][0]["systems"][0]["wbs_code"] = "2.1.1.5"   # doesn't nest
    _sub(bad, "SUB-DCL")["id"] = "SUB-BRK-ECU"                                           # duplicate id
    bad["artifacts"][0]["attached_to"] = "SUB-NOPE"
    bad["artifacts"].append({"id": "STD-BRK-001", "name": "x", "type": "std", "revision": "A",
                             "attached_to": "SUB-BRK-ECU", "file": "STD-BRK-001.md"})
    m = messages(check_baseline(onto, bad, settings.documents_dir))
    assert any("doesn't sit under its parent's 1.1.1" in x for x in m)
    assert any("id used twice" in x for x in m)
    assert any("attached_to = 'SUB-NOPE' is not a Subsystem" in x for x in m)
    assert any("'STD-BRK-001.md' is listed but not in documents/" in x for x in m)


def test_rules_give_each_subsystem_its_own_requirements(onto):
    brake = {"safety_critical": True, "os_class": "rtos"}
    assert onto.required_documents(brake, has_interfaces=True) == {
        "srs": ["Software is specified and designed (MIL-STD-498)."],
        "sdd": ["Software is specified and designed (MIL-STD-498)."],
        "icd": ["Every interface is defined in a controlled interface document."],
        "std": ["Safety-critical software must have a documented test description."]}
    assert list(onto.required_documents({"safety_critical": True}, has_interfaces=True)) == ["icd"]   # a CAN backbone
    assert onto.required_documents({"safety_critical": False}, has_interfaces=False) == {}
    assert onto.required_types() == ["srs", "sdd", "icd", "std"]


def test_aliases_must_be_a_list_of_names(onto, data):
    _sub(data, "SUB-ENG-ECM")["aliases"] = "ECM"
    assert any("aliases = 'ECM': must be a list" in m for m in messages(check_baseline(onto, data)))
