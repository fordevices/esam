"""
Crew tools. Every tool wraps a tested esamkit function: agents never write
SQL, never open a database connection and never see a secret.

Each tool records its own usage (duration, success) in the run's usage
ledger, so a run's cost in tool calls is visible next to its LLM tokens.
Tools are handed to agents only if step 0 enabled them for this environment
(esamkit.environment.capabilities).
"""

import json
import time
from typing import Any

from crewai.tools import BaseTool
from pydantic import PrivateAttr

from esamkit.usage import UsageLedger


class TrackedTool(BaseTool):
    """A tool that writes one usage row per call. Subclasses implement `_call`."""

    _ledger: UsageLedger | None = PrivateAttr(default=None)
    _agent: str = PrivateAttr(default="")

    def bind(self, ledger: UsageLedger, agent: str = "") -> "TrackedTool":
        self._ledger, self._agent = ledger, agent
        return self

    def _call(self, **kwargs: Any) -> str:
        raise NotImplementedError

    def _run(self, **kwargs: Any) -> str:
        t, ok = time.perf_counter(), True
        try:
            return self._call(**kwargs)
        except Exception as e:      # the agent gets the reason; the run carries on
            ok = False
            return f"TOOL ERROR ({self.name}): {type(e).__name__}: {e}"
        finally:
            if self._ledger:
                self._ledger.record("tool", tool=self.name, agent=self._agent, ok=ok,
                                    duration_ms=round((time.perf_counter() - t) * 1000),
                                    detail={k: str(v)[:200] for k, v in kwargs.items()})


class InspectEnvironmentTool(TrackedTool):
    name: str = "inspect_environment"
    description: str = (
        "Probe the configured ESAM environment: host, files, database, vector store, graph, "
        "embedding model, LLM endpoints and CrewAI. Returns each probe's status and detail, and "
        "which crew tools are enabled. Takes no input.")
    _report: dict | None = PrivateAttr(default=None)

    def preset(self, report: dict) -> "InspectEnvironmentTool":
        """Use a report the runner already made (so the agent and the runner see the same probes)."""
        self._report = report
        return self

    def _call(self, **kwargs: Any) -> str:
        from esamkit.environment import inspect

        rep = self._report or inspect()
        return json.dumps({"profile": rep["profile"], "probes": rep["probes"],
                           "capabilities": rep["capabilities"], "limits": rep["limits"]}, indent=1, default=str)
