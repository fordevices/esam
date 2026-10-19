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

from .custom_tools import TOOLS, InspectEnvironmentTool, Workbench

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
    # Which tools each agent gets, per pipeline. A tool step 0 disabled is left out.
    PIPELINES = {
        "review": {"tasks": ["a1_ontology_check", "a2_document_review", "a2_approval_request"],
                   "tools": {"triage": ["ontology_check", "ontology_lookup", "check_coverage"],
                             "researcher": ["review_document", "verified_by", "search_documents", "walk_graph"],
                             "se_reviewer": ["ontology_check", "review_document"]}},
        "ingest": {"tasks": ["a3_ingest", "a4_verify"],
                   "tools": {"researcher": ["ingest_document"],
                             "se_reviewer": ["verify_ingestion", "health_report"]}},
        "author": {"tasks": ["b1_gather_basis", "b2_draft_srs", "b3_verify_srs", "b4_evidence_pack"],
                   "tools": {"triage": ["read_brief", "read_source", "check_coverage", "walk_graph", "search_standards",
                                        "standard_clause", "ontology_lookup"],
                             "researcher": ["read_brief", "read_source", "standard_clause", "search_standards",
                                            "save_draft", "check_draft"],
                             "se_reviewer": ["check_draft", "read_source", "standard_clause", "save_draft",
                                             "build_evidence_pack"]}},
        "ask": {"tasks": ["ask_triage", "ask_research", "ask_review"],
                "tools": {"triage": ["ontology_lookup", "walk_graph", "check_coverage"],
                          "researcher": ["search_documents", "walk_graph", "verified_by", "check_coverage", "ontology_lookup"],
                          "se_reviewer": ["check_coverage", "verified_by", "ontology_lookup"]}},
    }

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

    def missing_tools(self, pipeline: str) -> list[str]:
        """Tools this pipeline needs that step 0 did not enable."""
        enabled = set(self.env_report["capabilities"]["enabled"])
        return sorted({t for ts in self.PIPELINES[pipeline]["tools"].values() for t in ts} - enabled)

    def pipeline_crew(self, pipeline: str, wb: Workbench, folder: Path) -> Crew:
        """Triage → Researcher → SE Reviewer, each with its tools for this pipeline.
        (The runner writes each task's output to the run folder: CrewAI's
        output_file strips a leading '/' and would write relative to the cwd.)"""
        spec, enabled = self.PIPELINES[pipeline], set(self.env_report["capabilities"]["enabled"])
        agents = {name: self.agent(name, [TOOLS[t]().on(wb) for t in tools if t in enabled])
                  for name, tools in spec["tools"].items()}
        tasks: list[Task] = []
        for name in spec["tasks"]:
            agent = agents[self.tasks_cfg[name]["agent"]]
            tasks.append(self.task(name, agent, context=list(tasks)))
        return Crew(agents=list(agents.values()), tasks=tasks, process=Process.sequential, memory=False, verbose=True)
