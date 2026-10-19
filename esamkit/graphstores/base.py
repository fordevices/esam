"""
The GraphStore interface.

A vector store answers "what TEXT is similar to this?". A graph store answers
"what is CONNECTED to this, and how?". Different question, different tool.

The pilot implements this with two plain tables (graph_vertices, graph_edges)
and SQL recursive CTEs. The full deployment moves to JanusGraph + Gremlin.
Because lesson scripts and agent tools only see this interface, that
migration means writing one new class, not touching the callers.

Every query method returns plain dicts so callers (and an LLM prompt) can
use the results without knowing which engine produced them.
"""

from abc import ABC, abstractmethod

from ..baseline import Baseline


class GraphStore(ABC):
    @abstractmethod
    def load(self, baseline: Baseline) -> dict:
        """Upsert all vertices and edges. Idempotent: loading the same baseline
        twice changes nothing. Returns counts {"vertices": n, "edges": m}."""

    @abstractmethod
    def reset(self) -> None:
        """Drop all graph data."""

    @abstractmethod
    def descendants(self, eid: str) -> list[dict]:
        """Walk DOWN the decomposition from `eid` (e.g. the whole WBS tree)."""

    @abstractmethod
    def ancestors(self, eid: str) -> list[dict]:
        """Walk UP from `eid` (an artifact or subsystem) to the vehicle root."""

    @abstractmethod
    def impact(self, eid: str, message: str | None = None, max_depth: int = 5) -> list[dict]:
        """Everything reachable from `eid` along INTERFACES_WITH edges
        (optionally only edges carrying `message`), with the path taken and
        whether every hop on it is confirmed."""

    @abstractmethod
    def message_origins(self, message: str) -> list[dict]:
        """Subsystems that SEND `message` but don't receive it from anyone:
        where the message originates, and so where an impact walk must start."""

    @abstractmethod
    def artifact_gaps(self, artifact_type: str) -> list[dict]:
        """Subsystems that have NO artifact of the given type (e.g. 'icd')."""

    @abstractmethod
    def snapshot(self) -> dict:
        """Every vertex and edge, keyed by ENTITY id (never internal UUIDs):
        {"vertices": [{eid, label, props}], "edges": [{type, from_eid, to_eid, props}]}.
        For drawing the whole graph (the SE Cockpit). Fine at baseline size
        (thousands of vertices); a million-edge graph would need paging."""

    # ── Traceability (requirements and test cases; see esamkit/traceability.py) ──

    @abstractmethod
    def remove_derived(self, source_artifact: str) -> int:
        """Delete the Requirement/TestCase vertices (and their edges) that came
        from one document, before that document is reloaded. Returns how many."""

    @abstractmethod
    def existing_ids(self, label: str) -> set[str]:
        """Entity ids of every vertex with this label."""

    @abstractmethod
    def untested_requirements(self) -> list[dict]:
        """Requirements no test case VERIFIES, with their subsystem and document."""

    @abstractmethod
    def verified_by(self, requirement_id: str) -> list[dict]:
        """Test cases that VERIFY the requirement, with the document each is in."""

    @abstractmethod
    def trace_summary(self) -> list[dict]:
        """Per subsystem: how many requirements are allocated to it, and how many
        have at least one test case verifying them."""
