"""The crew's YAML and tool wiring agree with each other (no LLM call)."""

import re

import pytest
import yaml

from esamkit.config import PROJECT_ROOT
from esamkit.environment import TOOL_NEEDS

pytest.importorskip("crewai")

CONFIG = PROJECT_ROOT / "crew" / "config"
INPUTS = {"document", "run_id", "request", "environment", "needed_tools"}


def test_pipelines_use_known_tasks_agents_and_tools():
    from crew.crew import EsamCrew
    from crew.custom_tools import TOOLS

    agents = yaml.safe_load((CONFIG / "agents.yaml").read_text())
    tasks = yaml.safe_load((CONFIG / "tasks.yaml").read_text())
    for name, spec in EsamCrew.PIPELINES.items():
        for task in spec["tasks"]:
            assert task in tasks, (name, task)
            assert tasks[task]["agent"] in spec["tools"], (name, task)
        for agent, tools in spec["tools"].items():
            assert agent in agents
            for t in tools:
                assert t in TOOLS and t in TOOL_NEEDS, t


def test_placeholders_are_inputs_the_runner_gives():
    text = "\n".join(line for f in ("agents.yaml", "tasks.yaml") for line in (CONFIG / f).read_text().splitlines()
                     if not line.lstrip().startswith("#"))
    assert set(re.findall(r"\{(\w+)\}", text)) <= INPUTS


def test_ingest_needs_an_approval_and_a_run(tmp_path):
    from crew.custom_tools import TOOLS, Workbench
    from esamkit.config import settings

    assert "only inside an intake run" in TOOLS["ingest_document"]().on(Workbench(settings))._run()
    wb = Workbench(settings, tmp_path, {"entry": {"id": "STD-BRK-001"}, "document": "x.md"})
    assert TOOLS["ingest_document"]().on(wb)._run().startswith("REFUSED: no SE approval")
