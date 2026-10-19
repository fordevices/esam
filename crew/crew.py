"""
The ESAM crew: agents and tasks built from config/agents.yaml and
config/tasks.yaml, with the model and tools the environment provides.

Unlike the RTOS crew's single @CrewBase pipeline, ESAM has several pipelines
(step 0 environment; A: ontology to verified ingestion; B: SRS authoring),
so each pipeline is assembled explicitly from the same YAML.
"""

from pathlib import Path

import yaml
from crewai import LLM, Agent, Crew, Process, Task

from esamkit.environment import load_profile, resolve
from esamkit.usage import UsageLedger

from .custom_tools import InspectEnvironmentTool

CONFIG = Path(__file__).parent / "config"


def _yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG / name).read_text())


def reasoning_llm(profile: dict) -> LLM:
    """The profile's reasoning model, over any OpenAI-compatible endpoint
    (OpenAI on the laptop, the IL5 API Gateway on the pilot)."""
    import os

    role = resolve(profile)["roles"]["reasoning"]
    return LLM(model=f"openai/{role['model']}", base_url=role["base_url"],
               api_key=os.getenv(role["api_key_env"] or "", ""))


class EsamCrew:
    def __init__(self, environment: str, ledger: UsageLedger, env_report: dict):
        self.environment = environment
        self.profile = load_profile(environment)
        self.ledger = ledger
        self.env_report = env_report
        self.agents_cfg = _yaml("agents.yaml")
        self.tasks_cfg = _yaml("tasks.yaml")
        self.llm = reasoning_llm(self.profile)

    def agent(self, name: str, tools: list) -> Agent:
        return Agent(config=self.agents_cfg[name], llm=self.llm, tools=[t.bind(self.ledger, name) for t in tools],
                     memory=False, allow_delegation=False, verbose=True,
                     max_iter=(self.profile.get("limits") or {}).get("max_llm_calls_per_run", 40))

    def task(self, name: str, agent: Agent, context: list[Task] | None = None, output_file: str | None = None) -> Task:
        cfg = dict(self.tasks_cfg[name])
        cfg.pop("agent", None)
        return Task(config=cfg, agent=agent, context=context or [], output_file=output_file)

    def environment_crew(self) -> Crew:
        """Step 0: the Environment Engineer reads the probes and gives a green light or fixes."""
        eng = self.agent("environment_engineer", [InspectEnvironmentTool().preset(self.env_report)])
        return Crew(agents=[eng], tasks=[self.task("check_environment", eng)], process=Process.sequential,
                    memory=False, verbose=True)
