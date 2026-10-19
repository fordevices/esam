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
from pydantic import BaseModel, Field, PrivateAttr

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


# ── Pipeline A and the interactive mode ─────────────────────────────────────
#
# Tools share one Workbench per run: the environment's settings and stores
# (opened once, the embedding model loaded only if a tool needs it) and, for
# an intake run, the incoming document and the run folder. The agent never
# passes a file path or a database name; it can't point a tool elsewhere.

class Workbench:
    def __init__(self, settings, run_folder=None, state: dict | None = None):
        self.s, self.folder, self.state = settings, run_folder, state if state is not None else {}
        self._graph = self._emb = self._store = None

    @property
    def graph(self):
        if self._graph is None:
            from esamkit.factory import get_graph_store
            self._graph = get_graph_store(self.s)
        return self._graph

    @property
    def emb(self):
        if self._emb is None:
            from esamkit.factory import get_embedder
            self._emb = get_embedder(self.s)
        return self._emb

    @property
    def store(self):
        if self._store is None:
            from esamkit.factory import get_vector_store
            self._store = get_vector_store(self.emb, self.s)
        return self._store

    @property
    def onto(self):
        from esamkit.ontology import load_ontology
        return load_ontology(self.s.ontology_yaml)

    def document_text(self) -> str:
        return (self.folder / self.state["document"]).read_text()

    def save(self) -> None:
        (self.folder / "state.json").write_text(json.dumps(self.state, indent=1, default=str))


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str, ensure_ascii=False)


class WorkbenchTool(TrackedTool):
    _wb: Workbench | None = PrivateAttr(default=None)

    needs_document: bool = False

    def on(self, wb: Workbench) -> "WorkbenchTool":
        self._wb = wb
        return self

    def _run(self, **kwargs: Any) -> str:
        if self.needs_document and not (self._wb and self._wb.folder and self._wb.state.get("entry")):
            return f"{self.name} works only inside an intake run (python -m crew.main review <file>)."
        return super()._run(**kwargs)


class TermInput(BaseModel):
    term: str = Field(..., description="A document type (std, 'test description'), class, attribute, value set, relationship or rule word")


class OntologyLookupTool(WorkbenchTool):
    name: str = "ontology_lookup"
    description: str = ("Look a term up in the ESAM ontology: document types (with their MIL-STD-498 name and "
                        "synonyms), classes and attributes, value sets, relationships, and the rules that require it.")
    args_schema: type[BaseModel] = TermInput

    def _call(self, term: str, **_) -> str:
        o, q = self._wb.onto, term.strip().lower()
        hit = lambda k, v: q in k.lower() or q in json.dumps(v, default=str).lower()  # noqa: E731
        out = {"document_types": {k: v for k, v in o.document_types.items() if hit(k, v)},
               "classes": {k: v for k, v in o.classes.items() if q in k.lower()},
               "value_sets": {k: v for k, v in o.value_sets.items() if hit(k, v)},
               "relationships": {k: v for k, v in o.relationships.items() if q in k.lower()},
               "rules": [r for r in o.rules.get("required_documents", []) if hit("", r)]}
        out = {k: v for k, v in out.items() if v}
        return _dump(out) if out else f"Nothing in the ontology matches {term!r}."


class QueryInput(BaseModel):
    query: str = Field(..., description="What to look for, in plain words")
    artifact_id: str = Field("", description="Optional: search inside one document only, e.g. SRS-BRK-001")


class SearchDocumentsTool(WorkbenchTool):
    name: str = "search_documents"
    description: str = ("Semantic search over the ingested documents. Returns the best chunks with their "
                        "document id, chunk number, score and text. Cite the document id.")
    args_schema: type[BaseModel] = QueryInput

    def _call(self, query: str, artifact_id: str = "", **_) -> str:
        wb = self._wb
        hits = wb.store.search(wb.emb.embed_query(query), k=5, where={"artifact_id": artifact_id} if artifact_id else None)
        return _dump([{"document": h.chunk.artifact_id, "chunk": h.chunk.chunk_index, "score": round(h.score, 3),
                       "text": h.chunk.text[:900]} for h in hits]) if hits else "No matching chunks."


class EntityInput(BaseModel):
    entity_id: str = Field(..., description="A graph id: subsystem (SUB-BRK-ECU), system, document (SRS-BRK-001), requirement or test case")
    direction: str = Field("impact", description="up (what contains it), down (what it contains) or impact (who consumes what it sends)")


class WalkGraphTool(WorkbenchTool):
    name: str = "walk_graph"
    description: str = "Walk the baseline graph from one entity: up the tree, down the tree, or along interfaces (impact)."
    args_schema: type[BaseModel] = EntityInput

    def _call(self, entity_id: str, direction: str = "impact", **_) -> str:
        g = self._wb.graph
        fn = {"up": g.ancestors, "down": g.descendants, "impact": g.impact}.get(direction)
        if fn is None:
            return "direction must be up, down or impact"
        rows = fn(entity_id)
        return _dump(rows[:60]) if rows else f"{entity_id}: nothing found {direction} (is the id right?)"


class SubsystemInput(BaseModel):
    subsystem_id: str = Field(..., description="Subsystem id, e.g. SUB-BRK-ECU")


class CheckCoverageTool(WorkbenchTool):
    name: str = "check_coverage"
    description: str = ("Which documents the ontology's rules require for a subsystem, and which are present "
                        "or MISSING; plus its requirements with no verifying test case.")
    args_schema: type[BaseModel] = SubsystemInput

    def _call(self, subsystem_id: str, **_) -> str:
        wb = self._wb
        snap = wb.graph.snapshot()
        V = {v["eid"]: v for v in snap["vertices"]}
        sub = V.get(subsystem_id)
        if not sub or sub["label"] != "Subsystem":
            return f"{subsystem_id} is not a subsystem in the graph."
        linked = any(subsystem_id in (e["from_eid"], e["to_eid"]) for e in snap["edges"] if e["type"] == "INTERFACES_WITH")
        have = {V[e["to_eid"]]["props"].get("artifact_type"): e["to_eid"] for e in snap["edges"]
                if e["type"] == "HAS_ARTIFACT" and e["from_eid"] == subsystem_id}
        req = wb.onto.required_documents(sub["props"], linked)
        return _dump({"subsystem": subsystem_id, "name": sub["props"].get("name"),
                      "safety_critical": sub["props"].get("safety_critical"),
                      "required": {t: {"present": have.get(t) or "MISSING", "why": why} for t, why in req.items()},
                      "untested_requirements": [r["eid"] for r in wb.graph.untested_requirements()
                                                if r["subsystem"] == subsystem_id]})


class RequirementInput(BaseModel):
    requirement_id: str = Field(..., description="Requirement id, e.g. SRS-BRK-011")


class VerifiedByTool(WorkbenchTool):
    name: str = "verified_by"
    description: str = "The requirement's text and the test cases that verify it (none = untested)."
    args_schema: type[BaseModel] = RequirementInput

    def _call(self, requirement_id: str, **_) -> str:
        g = self._wb.graph
        req = next((v for v in g.snapshot()["vertices"] if v["eid"] == requirement_id and v["label"] == "Requirement"), None)
        if not req:
            return f"{requirement_id} is not a requirement in the graph."
        return _dump({"requirement": requirement_id, "text": req["props"].get("text"),
                      "document": req["props"].get("source_artifact"), "verified_by": g.verified_by(requirement_id)})


def health_summary(rep: dict) -> dict:
    return {"blocked": rep["blocked"], "checks": [
        {"id": c["id"], "title": c["title"], "status": c["status"], "findings": len(c["findings"]),
         "safety_critical_findings": [f"{f['where']}: {f['message']}" for f in c["findings"] if f["critical"]]}
        for c in rep["checks"]],
        "rollup": next(c for c in rep["checks"] if c["id"] == 6)["rollup"]}


class HealthReportTool(WorkbenchTool):
    name: str = "health_report"
    description: str = ("The baseline health report now: six checks (required documents, interfaces, awaiting "
                        "review, traceability, integrity, roll-up). Only integrity blocks approval. Takes no input.")

    def _call(self, **_) -> str:
        from esamkit.health import report_for
        return _dump(health_summary(report_for(self._wb.graph, self._wb.s)))


class OntologyCheckTool(WorkbenchTool):
    needs_document: bool = True
    name: str = "ontology_check"
    description: str = ("Check the incoming document against the ontology: what it says it is (id, type, "
                        "revision, subsystem), the findings adding it to the baseline would introduce, whether "
                        "the rules require it, and whether it replaces an earlier revision. Takes no input.")

    def _call(self, **_) -> str:
        from esamkit.intake import ontology_check
        wb = self._wb
        res = ontology_check(wb.onto, wb.s.baseline_yaml, wb.state["entry"])
        wb.state["ontology"] = res
        wb.save()
        return _dump(res)


class ReviewDocumentTool(WorkbenchTool):
    needs_document: bool = True
    name: str = "review_document"
    description: str = ("Read the incoming document deterministically: its sections and chunk count; for a test "
                        "description every test case with its method, pass criteria and the TEXT of each "
                        "requirement it verifies; for an SRS every requirement. Lists the issues found. Takes no input.")

    def _call(self, **_) -> str:
        from esamkit.intake import review_document
        wb = self._wb
        res = review_document(wb.document_text(), wb.state["entry"], wb.graph, wb.s)
        wb.state["review"] = res
        wb.save()
        return _dump(res)


class IngestDocumentTool(WorkbenchTool):
    needs_document: bool = True
    name: str = "ingest_document"
    description: str = ("Ingest the incoming document: add it to the baseline, store its chunks, reload the graph. "
                        "Works only after an SE approved this run. Takes no input.")

    def _call(self, **_) -> str:
        from esamkit.health import report_for
        from esamkit.intake import ingest, ontology_check, review_document

        wb = self._wb
        if (wb.folder / "manifest.json").exists():
            return "Already ingested in this run:\n" + (wb.folder / "manifest.json").read_text()
        if (wb.state.get("approval") or {}).get("decision") != "approved":
            return "REFUSED: no SE approval recorded for this run. Ingestion needs `python -m crew.main approve <run>`."
        oc = ontology_check(wb.onto, wb.s.baseline_yaml, wb.state["entry"])   # the baseline may have moved on
        if not oc["ok"]:
            return "REFUSED: the ontology check now fails:\n" + _dump(oc["findings"])
        wb.state["health_before"] = report_for(wb.graph, wb.s)
        wb.state["review"] = review_document(wb.document_text(), wb.state["entry"], wb.graph, wb.s)
        manifest = ingest(wb.folder / wb.state["document"], wb.state["entry"], wb.emb, wb.store, wb.graph,
                          wb.folder / "replaced", wb.s)
        (wb.folder / "manifest.json").write_text(_dump(manifest))
        wb.state["state"] = "ingested"
        wb.save()
        return _dump(manifest)


class VerifyIngestionTool(WorkbenchTool):
    needs_document: bool = True
    name: str = "verify_ingestion"
    description: str = ("Verify the ingest deterministically: chunks stored, document attached in the graph, its "
                        "test cases / requirements and VERIFIES links present, search finds it, integrity passes; "
                        "plus the health report before vs after (findings closed and opened). Takes no input.")

    def _call(self, **_) -> str:
        from esamkit.health import report_for
        from esamkit.intake import health_diff, verify_ingestion

        wb = self._wb
        if not (wb.folder / "manifest.json").exists():
            return "Nothing to verify: the document has not been ingested in this run."
        manifest = json.loads((wb.folder / "manifest.json").read_text())
        after = report_for(wb.graph, wb.s)
        res = verify_ingestion(wb.state["entry"], wb.state["review"], manifest, wb.emb, wb.store, wb.graph, after, wb.s)
        res["health"] = health_diff(wb.state["health_before"], after)
        wb.state.update(verification=res, health_after=after, state="verified" if res["verified"] else "verification_failed")
        wb.save()
        return _dump(res)


TOOLS = {t.model_fields["name"].default: t for t in (
    OntologyLookupTool, SearchDocumentsTool, WalkGraphTool, CheckCoverageTool, VerifiedByTool,
    HealthReportTool, OntologyCheckTool, ReviewDocumentTool, IngestDocumentTool, VerifyIngestionTool)}
