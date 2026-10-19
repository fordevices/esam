"""
The SE-owned YAML baseline -> plain Python vertices and edges.

Both halves of the system read the baseline:
  * vector ingestion uses it to tag each chunk with artifact_id + wbs_code
  * the graph loader turns it into vertices and edges

So the parsing lives here, once, with no database code. That also makes it
testable without any server running (see tests/test_baseline.py).

Edge direction conventions (same as ESAM_Pilot_Implementation_v1.0.md):

    child     ──PART_OF──────────►  parent          (Subsystem -> System)
    owner     ──HAS_ARTIFACT─────►  DesignArtifact  (Subsystem -> ICD)
    producer  ──INTERFACES_WITH──►  consumer        (Brake ECU -> backbone)

Direction matters. "Walk DOWN the WBS" means following PART_OF edges
backwards; "walk UP" means following them forwards. Lesson 04 does both.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Vertex:
    label: str      # Vehicle, VehicleFamily, Variant, System, Subsystem, Function, DesignArtifact
    props: dict     # always has "id" (the entity id, e.g. "SUB-BRK-ECU") and "name"

    @property
    def eid(self) -> str:
        return self.props["id"]


@dataclass
class Edge:
    from_eid: str
    to_eid: str
    type: str
    props: dict = field(default_factory=dict)


@dataclass
class Baseline:
    metadata: dict
    vertices: list[Vertex]
    edges: list[Edge]
    artifacts: list[dict]   # raw artifact entries (with `file`), for ingestion

    def vertex(self, eid: str) -> Vertex:
        for v in self.vertices:
            if v.eid == eid:
                return v
        raise KeyError(eid)

    def artifact_index(self) -> dict[str, dict]:
        """document file (relative to data/documents/) -> facts to tag its chunks with."""
        parent_of = {e.from_eid: e.to_eid for e in self.edges if e.type == "PART_OF"}
        index = {}
        for art in self.artifacts:
            owner = art["attached_to"]
            # The owner may be a Subsystem; its WBS code is its System's code.
            system = parent_of.get(owner, owner)
            index[art["file"]] = {
                "artifact_id": art["id"],
                "artifact_type": art["type"],
                "title": art["name"],
                "revision": art.get("revision", ""),
                "subsystem_id": owner,
                "wbs_code": self.vertex(system).props.get("wbs_code", ""),
            }
        return index


# Optional subsystem / function fields copied onto the vertex when present.
_SUB_FIELDS = ("safety_critical", "safety_classification", "os_class", "aliases",
               "j1939_source_address", "tsn_profile", "repo_url", "jira_component")


def load_baseline(path: Path) -> Baseline:
    data = yaml.safe_load(Path(path).read_text())
    vertices: list[Vertex] = []
    edges: list[Edge] = []

    def add(label, props, parent_eid=None):
        vertices.append(Vertex(label, props))
        if parent_eid:
            edges.append(Edge(props["id"], parent_eid, "PART_OF"))

    veh = data["vehicle"]
    add("Vehicle", {"id": veh["id"], "name": veh["name"], "wbs_code": veh.get("wbs_code", "1.0"),
                    "programme": data.get("metadata", {}).get("programme", "")})
    for fam in veh.get("families", []):
        add("VehicleFamily", {"id": fam["id"], "name": fam["name"], "wbs_code": fam["wbs_code"]}, veh["id"])
        for var in fam.get("variants", []):
            add("Variant", {"id": var["id"], "name": var["name"], "wbs_code": var["wbs_code"],
                            "role": var.get("role", "variant")}, fam["id"])
            for sysm in var.get("systems", []):
                add("System", {"id": sysm["id"], "name": sysm["name"], "wbs_code": sysm["wbs_code"],
                               "universality": sysm.get("universality", "universal")}, var["id"])
                for sub in sysm.get("subsystems", []):
                    # No invented WBS code for subsystems (the old script made up
                    # "<system>.sub"). A subsystem's WBS code is its System's,
                    # reachable by one PART_OF hop.
                    props = {"id": sub["id"], "name": sub["name"],
                             "safety_critical": bool(sub.get("safety_critical", False))}
                    props.update({k: sub[k] for k in _SUB_FIELDS if k in sub})
                    add("Subsystem", props, sysm["id"])
                    for fn in sub.get("functions", []):
                        fprops = {"id": fn["id"], "name": fn["name"],
                                  "description": fn.get("description", "")}
                        if "safety_classification" in fn:
                            fprops["safety_classification"] = fn["safety_classification"]
                        add("Function", fprops, sub["id"])

    for iface in data.get("interfaces", []):
        edges.append(Edge(iface["from"], iface["to"], "INTERFACES_WITH", {
            "interface_type": iface["type"],
            "message": iface.get("message", ""),
            "status": iface.get("status", "candidate"),
            "source_artifact": iface.get("source_artifact", ""),
            "notes": iface.get("notes", ""),
        }))

    for art in data.get("artifacts", []):
        vertices.append(Vertex("DesignArtifact", {
            "id": art["id"], "name": art["name"], "artifact_type": art["type"],
            "revision": art.get("revision", ""), "file": art["file"]}))
        edges.append(Edge(art["attached_to"], art["id"], "HAS_ARTIFACT",
                          {"artifact_type": art["type"]}))

    # Catch typos in the baseline early: every edge must point at a real vertex.
    known = {v.eid for v in vertices}
    dangling = [e for e in edges if e.from_eid not in known or e.to_eid not in known]
    if dangling:
        raise ValueError("Baseline edges reference unknown ids: " +
                         ", ".join(f"{e.from_eid}->{e.to_eid}" for e in dangling))
    return Baseline(data.get("metadata", {}), vertices, edges, data.get("artifacts", []))
