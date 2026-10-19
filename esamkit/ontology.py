"""
The ESAM ontology (data/ontology/esam_ontology.yaml) and a checker that holds
the baseline to it.

    python -m esamkit.ontology            # check the baseline; exit 1 on errors

The ontology is the programme's vocabulary: which kinds of things, links,
values and documents may exist, and what each kind of subsystem must have.
The checker reads the RAW baseline YAML, not esamkit.baseline's parsed form,
because the loader silently drops attributes it doesn't know. A misspelt
`safty_critical: true` would vanish without a trace; here it's reported.

Findings are errors (the baseline breaks a rule of the vocabulary) or
warnings (allowed, but probably a mistake). Nothing here touches a database.
"""

import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .config import settings

# YAML keys that hold children, by class: they are structure, not attributes.
_CHILDREN = {"Vehicle": ("families", "VehicleFamily"), "VehicleFamily": ("variants", "Variant"),
             "Variant": ("systems", "System"), "System": ("subsystems", "Subsystem"),
             "Subsystem": ("functions", "Function")}
_WBS = re.compile(r"^\d+(\.\d+[a-z]?)*$")


@dataclass
class Finding:
    level: str      # "error" | "warning"
    where: str      # the entity id, or a section name
    message: str

    def __str__(self) -> str:
        return f"{'✗' if self.level == 'error' else '!'} {self.where}: {self.message}"


class Ontology:
    def __init__(self, data: dict):
        self.data = data
        self.value_sets: dict = data.get("value_sets", {})
        self.classes: dict = data.get("classes", {})
        self.relationships: dict = data.get("relationships", {})
        self.document_types: dict = data.get("document_types", {})
        self.rules: dict = data.get("rules", {})
        self.placement: dict = data.get("placement", {})

    def allowed(self, value_set: str) -> set[str]:
        if value_set == "document_types":
            return set(self.document_types)
        return {str(k) for k in self.value_sets.get(value_set, {})}

    # ── Used by the rest of ESAM ──

    def doc_type_cues(self) -> dict[str, list[str]]:
        """document type -> lower-case words in a question that mean this type
        would answer it: its `answers` plus its synonyms ("test procedure")."""
        return {t: [c.lower() for c in d.get("answers", []) + d.get("synonyms", [])]
                for t, d in self.document_types.items()}

    def required_documents(self, props: dict, has_interfaces: bool) -> dict[str, list[str]]:
        """What a subsystem must have under the rules: {doc_type: [why, ...]},
        in document_types order. `props` are the subsystem's attributes."""
        facts = {"software": bool(props.get("os_class")),
                 "safety_critical": props.get("safety_critical") is True,
                 "has_interfaces": has_interfaces}
        out: dict[str, list[str]] = {}
        for rule in self.rules.get("required_documents", []):
            if all(facts[c] for c in rule["when"]):
                for t in rule["require"]:
                    out.setdefault(t, []).append(rule.get("why", ""))
        return {t: out[t] for t in self.document_types if t in out}

    def required_types(self) -> list[str]:
        """Every document type some rule can require, in document_types order."""
        wanted = {t for r in self.rules.get("required_documents", []) for t in r.get("require", [])}
        return [t for t in self.document_types if t in wanted]

    def check_self(self) -> list[Finding]:
        """The ontology's own consistency: every reference inside it resolves."""
        out = []

        def check_attr(where, spec):
            vs = spec.get("values")
            if vs and vs != "document_types" and vs not in self.value_sets:
                out.append(Finding("error", where, f"refers to unknown value set {vs!r}"))
            if spec.get("type") == "ref" and spec.get("class") not in self.classes:
                out.append(Finding("error", where, f"refers to unknown class {spec.get('class')!r}"))

        for cname, c in self.classes.items():
            for parent in c.get("part_of", []):
                if parent not in self.classes:
                    out.append(Finding("error", f"class {cname}", f"part_of unknown class {parent!r}"))
            for aname, spec in (c.get("attributes") or {}).items():
                check_attr(f"class {cname}.{aname}", spec)
        for rname, r in self.relationships.items():
            for end in r.get("from", []) + r.get("to", []):
                if end not in self.classes:
                    out.append(Finding("error", f"relationship {rname}", f"unknown class {end!r}"))
            for pname, spec in (r.get("properties") or {}).items():
                check_attr(f"relationship {rname}.{pname}", spec)
        for i, rule in enumerate(self.rules.get("required_documents", []), 1):
            for t in rule.get("require", []):
                if t not in self.document_types:
                    out.append(Finding("error", f"rule {i}", f"requires unknown document type {t!r}"))
            for cond in rule.get("when", []):
                if cond not in ("software", "has_interfaces", "safety_critical"):
                    out.append(Finding("error", f"rule {i}", f"unknown condition {cond!r}"))
        return out


def load_ontology(path: Path | None = None) -> Ontology:
    return Ontology(yaml.safe_load((path or settings.ontology_yaml).read_text()))


@lru_cache(maxsize=1)
def default_ontology() -> Ontology:
    """The configured ontology, read once per process."""
    return load_ontology()


# ── Checking the baseline ────────────────────────────────────────────────────

def _check_value(onto: Ontology, where: str, name: str, value, spec: dict) -> list[Finding]:
    t, out = spec.get("type", "text"), []
    bad = lambda msg: out.append(Finding("error", where, f"{name} = {value!r}: {msg}"))  # noqa: E731
    if t == "boolean" and not isinstance(value, bool):
        bad("must be true or false")
    elif t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            bad("must be a whole number")
        elif not spec.get("min", value) <= value <= spec.get("max", value):
            bad(f"must be between {spec.get('min')} and {spec.get('max')}")
    elif t == "enum":
        allowed = onto.allowed(spec["values"])
        if str(value) not in allowed:
            bad(f"not an allowed value; use one of {sorted(allowed)}")
    elif t == "wbs" and not _WBS.match(str(value)):
        bad("not a WBS code like 1.1.1.5")
    elif t == "text_list" and not (isinstance(value, list) and all(isinstance(v, str) and v for v in value)):
        bad("must be a list of names, e.g. [\"ECM\", \"engine ECU\"]")
    if spec.get("pattern") and isinstance(value, str) and not re.match(spec["pattern"], value):
        bad(f"doesn't match the required form {spec['pattern']}")
    return out


def _check_node(onto: Ontology, cname: str, node: dict, parent: dict | None, seen: dict) -> list[Finding]:
    cls, out = onto.classes[cname], []
    eid = node.get("id", f"<{cname} without id>")
    if "id" not in node:
        out.append(Finding("error", eid, "has no id"))
    elif not re.match(cls["id_pattern"], eid):
        out.append(Finding("error", eid, f"id doesn't match the {cname} form {cls['id_pattern']}"))
    if eid in seen:
        out.append(Finding("error", eid, f"id used twice (also a {seen[eid]})"))
    seen[eid] = cname
    if not node.get("name"):
        out.append(Finding("error", eid, "has no name"))

    attrs = cls.get("attributes") or {}
    child_key = _CHILDREN.get(cname, (None,))[0]
    for key, value in node.items():
        if key in ("id", "name") or key == child_key:
            continue
        if key not in attrs:
            out.append(Finding("warning", eid, f"attribute {key!r} is not in the ontology for {cname} "
                                               "(misspelt? or add it to the ontology)"))
            continue
        out += _check_value(onto, eid, key, value, attrs[key])
    for key, spec in attrs.items():
        if spec.get("required") and key not in node:
            out.append(Finding("error", eid, f"required attribute {key!r} is missing"))

    # WBS codes must nest: a child's code extends its parent's. MIL-STD-881
    # writes the Level 1 root as "1.0", and its children as "1.1", "1.2", ...
    if parent and node.get("wbs_code") and parent.get("wbs_code"):
        root = re.sub(r"\.0$", "", str(parent["wbs_code"]))
        if not str(node["wbs_code"]).startswith(root + "."):
            out.append(Finding("error", eid, f"WBS {node['wbs_code']} doesn't sit under its parent's "
                                             f"{parent['wbs_code']}"))

    if child_key:
        child_class = _CHILDREN[cname][1]
        for child in node.get(child_key) or []:
            out += _check_node(onto, child_class, child, node, seen)
    return out


def check_baseline(onto: Ontology, data: dict, documents_dir: Path | None = None) -> list[Finding]:
    """Check raw baseline YAML against the ontology. With `documents_dir`,
    also check that every listed document file exists and every file is listed."""
    seen: dict[str, str] = {}
    out = _check_node(onto, "Vehicle", data.get("vehicle", {}), None, seen)

    # Interfaces
    rel = onto.relationships["INTERFACES_WITH"]
    for i, iface in enumerate(data.get("interfaces") or [], 1):
        where = f"interface {i} ({iface.get('from')} → {iface.get('to')})"
        for end in ("from", "to"):
            if seen.get(iface.get(end)) != "Subsystem":
                out.append(Finding("error", where, f"{end!r} must be a subsystem in the baseline"))
        for key, spec in rel["properties"].items():
            if spec.get("required") and key not in iface:
                out.append(Finding("error", where, f"required property {key!r} is missing"))
            elif key in iface and iface[key] not in ("", None) and spec.get("type") == "enum":
                out += _check_value(onto, where, key, iface[key], spec)
        for key in iface:
            if key not in rel["properties"] and key not in ("from", "to", "notes"):
                out.append(Finding("warning", where, f"property {key!r} is not in the ontology"))
        if iface.get("type") == "signal" and not iface.get("message"):
            out.append(Finding("error", where, "a signal interface must name its message"))
        if iface.get("status") == "confirmed" and not iface.get("source_artifact"):
            out.append(Finding("warning", where, "confirmed, but no interface document is cited"))

    # Documents
    art_spec = onto.classes["DesignArtifact"]
    listed_files = set()
    arts = data.get("artifacts") or []
    art_type = {a.get("id"): a.get("type") for a in arts}
    for art in arts:
        aid = art.get("id", "<artifact without id>")
        if not re.match(art_spec["id_pattern"], aid):
            out.append(Finding("error", aid, f"document id doesn't match the form {art_spec['id_pattern']}"))
        elif art.get("type") and aid.split("-")[0].lower() != str(art["type"]).lower():
            out.append(Finding("error", aid, f"id says {aid.split('-')[0]} but type is {art['type']!r}"))
        if aid in seen:
            out.append(Finding("error", aid, f"id used twice (also a {seen[aid]})"))
        seen[aid] = "DesignArtifact"
        for key, spec in art_spec["attributes"].items():
            if key not in art:
                if spec.get("required"):
                    out.append(Finding("error", aid, f"required attribute {key!r} is missing"))
            elif spec.get("type") == "ref":
                if seen.get(art[key]) != spec["class"]:
                    out.append(Finding("error", aid, f"{key} = {art[key]!r} is not a {spec['class']} in the baseline"))
            else:
                out += _check_value(onto, aid, key, art[key], spec)
        if art.get("file"):
            listed_files.add(art["file"])
            if documents_dir and not (documents_dir / art["file"]).exists():
                out.append(Finding("error", aid, f"file {art['file']!r} is listed but not in {documents_dir.name}/"))
    for i, iface in enumerate(data.get("interfaces") or [], 1):
        src = iface.get("source_artifact")
        if src and art_type.get(src) != "icd":
            out.append(Finding("error", f"interface {i}", f"source_artifact {src!r} is not an ICD in the baseline"))
    if documents_dir and documents_dir.exists():
        for f in sorted(documents_dir.glob("*.md")):
            if f.name not in listed_files:
                out.append(Finding("warning", f.name, "file is in the documents folder but not listed in the baseline"))
    return out


def main() -> None:
    onto = load_ontology()
    data = yaml.safe_load(settings.baseline_yaml.read_text())
    findings = onto.check_self() + check_baseline(onto, data, settings.documents_dir)
    print(f"Ontology : {settings.ontology_yaml.relative_to(settings.ontology_yaml.parents[2])} "
          f"v{onto.data.get('metadata', {}).get('version', '?')}: {len(onto.classes)} classes, "
          f"{len(onto.relationships)} relationships, {len(onto.document_types)} document types")
    print(f"Baseline : {settings.baseline_yaml.relative_to(settings.baseline_yaml.parents[2])}\n")
    for f in findings:
        print(f"  {f}")
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    print(("\n" if findings else "  ") + f"{errors} errors, {warnings} warnings.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
