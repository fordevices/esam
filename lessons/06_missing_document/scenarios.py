"""The two missing-document scenarios (lesson 06). Imported by run_test.py and the tests."""

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
STAGED = HERE / "staged"


def scenarios():
    from esamkit.confidence import Scenario

    arts = {a["id"]: a for a in yaml.safe_load((STAGED / "artifacts.yaml").read_text())}
    return [
        Scenario(
            name="brake-std",
            title="Brake ECU test description (STD)",
            question=("How is SRS-BRK-011 (release brake pressure within 15 ms of incipient wheel "
                      "lock-up) verified? Give the test steps and pass criteria."),
            subsystem="SUB-BRK-ECU", doc_type="std",
            document=STAGED / "STD-BRK-001.md", artifact=arts["STD-BRK-001"],
            expect_cited=["STD-BRK-001", "TC-BRK-011"],
            forbid_before=r"TC-[A-Z]+-\d+",           # an invented test case id
        ),
        Scenario(
            name="engine-icd",
            title="Engine ECM interface (ICD)",
            question="Does the Engine Control Module use the EBC1 brake message, and for what?",
            subsystem="SUB-ENG-ECM", doc_type="icd",
            document=STAGED / "ICD-ENG-001.md", artifact=arts["ICD-ENG-001"],
            expect_cited=["ICD-ENG-001"],
            forbid_before=r"(?i)compression brake",   # only ICD-ENG-001 says this
        ),
    ]
