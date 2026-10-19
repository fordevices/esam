"""
Pipeline B's deterministic core: an SRS drafted from an SE's brief, checked
against the standards it claims to follow, and packaged as evidence.

  brief       The SE's authoring brief (YAML): the document to write, the
              sources it derives from, and the safety assessment: for each
              safety-significant function (SSF), the severity (MIL-STD-882E
              Table I) and software control category (Table IV) the SE and the
              safety engineer assessed. ESAM never assesses safety itself.
  check_brief The SwCI of each SSF from Table V, its level-of-rigor tasks, and
              where the brief disagrees with the baseline.
  check_srs   The draft against DI-IPSC-81433A (outline), the ontology and SE
              manual §9.4 (requirement form and attributes), the brief (every
              SSF implemented, SwCI as assessed), the sources (every section
              accounted for), the graph (every interface addressed) and the
              standards library (every Basis citation real). Findings name the
              rule they apply. ISO/IEC/IEEE 29148 checks are ESAM heuristics
              named after its characteristics, not the standard's text.
  evidence_pack  Compliance matrix, traceability matrix and an index with
              provenance. It supports a certification; it is not one.
"""

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import PROJECT_ROOT, Settings, settings as default_settings
from .graphstores.base import GraphStore
from .intake import ontology_check, read_header
from .ontology import Ontology
from .standards import Library
from .traceability import read_requirements

# The SRS outline (DI-IPSC-81433A I.4: "The numbers shown designate the paragraph numbers to be used").
DID_OUTLINE = ["1", "1.1", "1.2", "1.3", "2", "3", "3.1", "3.2", "3.3", "3.3.1", "3.4", "3.5", "3.6", "3.7",
               "3.8", "3.9", "3.10", "3.10.1", "3.10.2", "3.10.3", "3.10.4", "3.11", "3.12", "3.13", "3.14",
               "3.15", "3.16", "3.17", "3.18", "4", "5", "6"]
_SECTION = re.compile(r"^#{1,4}\s+(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<title>.+?)\s*$")
_STATES_NONE = re.compile(r"tailored out|no requirements|not applicable|none\b|left to the design|so state|all requirements have equal weight", re.I)
# ESAM heuristics for three ISO/IEC/IEEE 29148 characteristics (names only; see the library).
_VAGUE = ["as appropriate", "as required", "if possible", "user-friendly", "adequate", "sufficient", "fast",
          "quickly", "minimize", "maximize", "etc.", "and/or", "approximately", "reasonable", "robust", "seamless",
          "state-of-the-art", "as soon as possible", "easy"]
_INCOMPLETE = re.compile(r"\b(TBD|TBR|TBS|TBC)\b")
_TOOLISH = re.compile(r"\[[a-z]+(?:_[a-z]+)+\]")
_TIME_VALUE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|µs|us|min|Hz|kHz)\b")
_TIMING = re.compile(r"^\d+(?:\.\d+)?\s?(?:ms|s|µs|us|min|Hz|kHz)\b")
_BOLD_ITEM = re.compile(r"^\s*-\s+\*\*(?P<id>[A-Z][A-Za-z0-9_.-]*-[A-Za-z0-9_.-]+)\*\*", re.M)
_SOURCE_REF = re.compile(r"^(?P<doc>[A-Z]+-[A-Z0-9]+-\d{3})(?:\s*§\s*(?P<sec>\d+(?:\.\d+)*))?$")


def _f(level: str, rule: str, where: str, message: str, basis: str = "") -> dict:
    return {"level": level, "rule": rule, "where": where, "message": message, "basis": basis}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ── The brief ───────────────────────────────────────────────────────────────

def load_brief(path: Path) -> dict:
    brief = yaml.safe_load(Path(path).read_text())
    brief["_path"], brief["_sha"] = str(path), sha(Path(path).read_text())
    return brief


def source_text(brief: dict, doc_id: str, s: Settings = default_settings) -> tuple[str, str] | None:
    """(text, where it came from) of a source document: the baseline copy if
    the document is ingested, else the file the brief names."""
    base = yaml.safe_load(s.baseline_yaml.read_text())
    art = next((a for a in base.get("artifacts") or [] if a["id"] == doc_id), None)
    if art and (s.documents_dir / art["file"]).exists():
        return (s.documents_dir / art["file"]).read_text(), f"baseline ({art['file']})"
    src = next((x for x in brief.get("sources") or [] if x.get("id") == doc_id), None)
    if src and src.get("file"):
        f = Path(src["file"]) if Path(src["file"]).is_absolute() else PROJECT_ROOT / src["file"]
        if f.exists():
            return f.read_text(), f"brief file {src['file']} (NOT in the baseline yet)"
    return None


def source_sections(text: str) -> dict[str, str]:
    """{number: title} of the numbered headings in a source document."""
    return {m["num"]: m["title"] for line in text.splitlines() if (m := _SECTION.match(line))}


def check_brief(brief: dict, lib: Library, onto: Ontology, s: Settings = default_settings) -> dict:
    doc = brief.get("document") or {}
    entry = {"id": doc.get("id"), "name": doc.get("name"), "type": "srs", "revision": doc.get("revision"),
             "attached_to": doc.get("subsystem"), "file": f"{doc.get('id')}.md"}
    oc = ontology_check(onto, s.baseline_yaml, entry)
    findings = [_f(x["level"], "ontology", x["where"], x["message"]) for x in oc["findings"]]
    sevs = lib.catalogue["tables"]["MIL-STD-882E Table I"]["severity_categories"]
    ssfs = []
    for fn in (brief.get("safety_assessment") or {}).get("functions") or []:
        where = fn.get("id", "?")
        if fn.get("severity") not in sevs:
            findings.append(_f("error", "safety assessment", where, f"severity {fn.get('severity')!r} is not a Table I category",
                               "MIL-STD-882E Table I"))
            continue
        if fn.get("control_category") not in (1, 2, 3, 4, 5):
            findings.append(_f("error", "safety assessment", where, f"control category {fn.get('control_category')!r} is not 1-5",
                               "MIL-STD-882E Table IV"))
            continue
        swci = lib.swci(fn["severity"], fn["control_category"])
        ssfs.append({**fn, "swci": swci, "level_of_rigor": lib.level_of_rigor(swci),
                     "risk_if_lor_incomplete": lib.unmet_lor_risk(swci)})
    sources = []
    for src in brief.get("sources") or []:
        got = source_text(brief, src.get("id"), s)
        if got is None:
            findings.append(_f("error", "sources", src.get("id", "?"), "not in the baseline and no readable file given"))
        else:
            sources.append({"id": src["id"], "from": got[1], "sections": source_sections(got[0])})
            if "NOT in the baseline" in got[1]:
                findings.append(_f("warning", "sources", src["id"],
                                   "used from the brief's file; ingest it (Pipeline A) so the SRS cites a baselined document"))
    sub = oc["subsystem"]
    critical = [x for x in ssfs if x["severity"] in ("Catastrophic", "Critical")]
    if critical and not sub["safety_critical"]:
        findings.append(_f("warning", "baseline", sub["id"] or "?",
                           f"the assessment has {len(critical)} Catastrophic/Critical function(s), which MIL-STD-882E 3.2.31 calls "
                           "safety-critical, but the baseline says safety_critical: false. An SE must reconcile the baseline.",
                           "MIL-STD-882E 3.2.31"))
    base_sub = _baseline_subsystem(s, sub["id"])
    if base_sub is not None and not base_sub.get("os_class"):
        findings.append(_f("warning", "baseline", sub["id"],
                           "no os_class in the baseline, so the ontology's rules don't treat it as software and won't require "
                           f"this SRS{' (the brief says ' + brief['operating_system'] + ')' if brief.get('operating_system') else ''}."))
    worst = min((x["swci"] for x in ssfs), default=None, key=lambda v: int(v.split()[-1]))
    return {"document": entry, "subsystem": sub, "ssfs": ssfs, "csci_swci": worst, "sources": sources,
            "findings": findings, "ok": not any(f["level"] == "error" for f in findings)}


def _baseline_subsystem(s: Settings, sid: str | None) -> dict | None:
    from .intake import _subsystem
    return _subsystem(yaml.safe_load(s.baseline_yaml.read_text()), sid)


# ── The draft ───────────────────────────────────────────────────────────────

def draft_sections(text: str) -> dict[str, dict]:
    """{number: {title, body}} of the draft's numbered headings."""
    out, cur = {}, None
    for line in text.splitlines():
        if m := _SECTION.match(line):
            cur = out.setdefault(m["num"], {"title": m["title"], "lines": []})
        elif cur is not None:
            cur["lines"].append(line)
    return {k: {"title": v["title"], "body": "\n".join(v["lines"]).strip()} for k, v in out.items()}


def check_srs(text: str, brief: dict, assessment: dict, graph: GraphStore, lib: Library, onto: Ontology,
              s: Settings = default_settings) -> dict:
    F: list[dict] = []
    doc = assessment["document"]

    # Header: what the draft says it is must be what the brief asked for.
    head = read_header(text)
    for key in ("id", "revision", "attached_to", "type"):
        if str(head.get(key, "")) != str(doc.get(key, "")):
            F.append(_f("error", "header", key, f"header says {head.get(key)!r}, the brief asks for {doc.get(key)!r}",
                        "SE manual §9.3"))

    # Outline: every DID paragraph present; an empty one says so.
    secs = draft_sections(text)
    reqs = read_requirements(text, doc["id"], doc["attached_to"]).vertices
    req_section = {v.eid: v.props["section"].split()[0].rstrip(".") for v in reqs}
    for num in DID_OUTLINE:
        did = lib.clauses("DI-IPSC-81433A")[num]
        if num not in secs:
            F.append(_f("error", "outline", num, f"paragraph {num} {did['title']} is missing; a tailored paragraph keeps its number "
                        "and title with 'This paragraph has been tailored out.'", "DI-IPSC-81433A I.4d"))
            continue
        children = [k for k in secs if k.startswith(num + ".")]
        has_reqs = any(sec == num or sec.startswith(num + ".") for sec in req_section.values())
        if not secs[num]["body"] and not children and not has_reqs:
            F.append(_f("error", "outline", num, "empty: if there are no requirements in a paragraph, it shall so state",
                        "DI-IPSC-81433A 3"))

    # Requirements: form and attributes.
    prefix = doc["id"].rsplit("-", 1)[0] + "-"
    pattern = onto.classes["Requirement"]["id_pattern"]
    read = {v.eid for v in reqs}
    for m in _BOLD_ITEM.finditer(text):         # looks like a requirement, but ESAM can't read it as one
        if m["id"] not in read:
            F.append(_f("error", "requirement id", m["id"], f"not readable as a requirement: use the form "
                        f"{prefix}0NN (e.g. {prefix}010), so it matches {pattern}", "SE manual §9.6"))
    elsewhere = {v["eid"]: v["props"].get("source_artifact") for v in graph.snapshot()["vertices"]
                 if v["label"] == "Requirement" and v["props"].get("source_artifact") != doc["id"]}
    ids = [v.eid for v in reqs]
    ssf = {x["id"]: x for x in assessment["ssfs"]}
    graph_ids = {v["eid"] for v in graph.snapshot()["vertices"]}
    src_secs = {x["id"]: x["sections"] for x in assessment["sources"]}
    methods, swcis = onto.allowed("verification_method"), onto.allowed("software_criticality")
    for v in reqs:
        p, rid, low = v.props, v.eid, f" {v.props['text'].lower()} "
        if not re.match(pattern, rid) or not rid.startswith(prefix):
            F.append(_f("error", "requirement id", rid, f"renumber it {prefix}010 to {prefix}099 (001-009 are document numbers: "
                        f"{doc['id']} is this document); the form is {pattern}", "ontology Requirement.id_pattern"))
        if ids.count(rid) > 1:
            F.append(_f("error", "requirement id", rid, "used twice in this document", "DI-IPSC-81433A 3"))
        if rid in elsewhere:
            F.append(_f("error", "requirement id", rid, f"already specified in {elsewhere[rid]}", "DI-IPSC-81433A 3"))
        n_shall = low.count(" shall ")
        if n_shall == 0:
            F.append(_f("error", "form", rid, "no 'shall'", "SE manual §9.4"))
        elif n_shall > 1:
            F.append(_f("warning", "29148 singular (heuristic)", rid, f"{n_shall} 'shall's: split into one requirement each",
                        "ISO/IEC/IEEE-29148 (characteristic: singular)"))
        for w in _VAGUE:
            if f" {w} " in low or f" {w}," in low or f" {w}." in low:
                F.append(_f("warning", "29148 unambiguous (heuristic)", rid, f"vague term {w!r}",
                            "ISO/IEC/IEEE-29148 (characteristic: unambiguous)"))
        if _TOOLISH.search(p["text"]):
            F.append(_f("error", "form", rid, f"cites {_TOOLISH.search(p['text'])[0]}, which looks like a tool or code name, "
                        "not a document", "SE manual §9.6"))
        if _INCOMPLETE.search(p["text"]) or _INCOMPLETE.search(str(p.get("timing") or "")):
            F.append(_f("warning", "29148 complete (heuristic)", rid, "contains TBD/TBR/TBS/TBC",
                        "ISO/IEC/IEEE-29148 (characteristic: complete)"))
        ver = p.get("verification") or []
        if not ver:
            F.append(_f("error", "qualification", rid, "no Verification attribute (I, A, D or T)", "DI-IPSC-81433A 4"))
        for m in ver:
            if m not in methods:
                F.append(_f("error", "qualification", rid, f"Verification {m!r} is not one of {sorted(methods)}", "DI-IPSC-81433A 4"))
        if _TIME_VALUE.search(p["text"]):
            if not p.get("timing"):
                F.append(_f("warning", "attributes", rid, "states a time or rate but has no Timing attribute", "SE manual §9.4"))
            elif not _TIMING.match(p["timing"]):
                F.append(_f("warning", "attributes", rid, f"Timing {p['timing']!r} should be a value with units", "SE manual §9.4"))
        traces = p.get("traces_to") or []
        if not traces:
            F.append(_f("error", "traceability", rid, "no Traces to attribute", "DI-IPSC-81433A 5a"))
        for t in traces:
            ok = t in ssf or t in graph_ids or t in src_secs
            if not ok and (m := _SOURCE_REF.match(t)) and m["doc"] in src_secs:
                ok = m["sec"] is None or m["sec"] in src_secs[m["doc"]]
            if not ok:
                F.append(_f("error", "traceability", rid, f"traces to {t!r}, which is not an SSF in the brief, an id in the "
                            "graph, or a section of a source document", "DI-IPSC-81433A 5a"))
        implements = [t for t in traces if t in ssf]
        is_safety = req_section[rid] == "3.7" or p.get("safety") or implements
        if is_safety:
            if p.get("safety") not in swcis:
                why = f"it implements {', '.join(implements)}" if implements else "it is a safety requirement"
                F.append(_f("error", "safety", rid, f"{why}, so it needs Safety: one of {sorted(swcis)} (a SwCI is assigned to "
                            "each safety-significant requirement)", "MIL-STD-882E B.2.2.4a"))
            for t in traces:
                if t in ssf and p.get("safety") and p["safety"] != ssf[t]["swci"]:
                    F.append(_f("error", "safety", rid, f"{t} is {ssf[t]['swci']} by Table V ({ssf[t]['severity']}, control category "
                                f"{ssf[t]['control_category']}); the requirement says {p['safety']}", "MIL-STD-882E Table V"))
            if not any(b.startswith("MIL-STD-882E") for b in p.get("basis") or []):
                F.append(_f("error", "standards trace", rid, "a safety requirement needs a Basis in MIL-STD-882E", "MIL-STD-882E 4.4"))
        for b in p.get("basis") or []:
            if b in src_secs or _SOURCE_REF.match(b):
                F.append(_f("error", "standards trace", rid, f"Basis {b!r} is a source document: move it to Traces to (as "
                            f"'{b.split()[0]} §<section>'); Basis holds standard clauses only, e.g. 'MIL-STD-882E 4.4.2'",
                            "SE manual §9.4"))
                continue
            r = lib.resolve(b)
            if not r["ok"]:
                F.append(_f("error", "standards trace", rid, f"Basis {b!r}: {r['reason']}", "standards library"))

    # Coverage: SSFs, source sections, interfaces.
    traced = {t for v in reqs for t in v.props.get("traces_to") or []}
    for x in assessment["ssfs"]:
        if x["id"] not in traced:
            F.append(_f("error", "coverage", x["id"], f"no requirement implements this {x['swci']} function",
                        "MIL-STD-882E B.2.2.1"))
    for src in assessment["sources"]:
        leaves = [n for n in src["sections"] if not any(k.startswith(n + ".") for k in src["sections"]) and n != "1"]
        for n in leaves:
            if f"{src['id']} §{n}" not in traced:
                F.append(_f("warning", "coverage", f"{src['id']} §{n}", f"'{src['sections'][n]}' is traced by no requirement",
                            "DI-IPSC-81433A 5b"))
    sub = doc["attached_to"]
    messages = {e["props"].get("message") for e in graph.snapshot()["edges"]
                if e["type"] == "INTERFACES_WITH" and sub in (e["from_eid"], e["to_eid"]) and e["props"].get("message")}
    interface_text = "\n".join(b["body"] for k, b in secs.items() if k == "3.3" or k.startswith("3.3."))
    for msg in sorted(messages):
        if msg.split()[0] not in interface_text:
            F.append(_f("warning", "coverage", msg, "an interface of this subsystem in the graph is not addressed in 3.3",
                        "DI-IPSC-81433A 3.3.1"))
    if any(v.props.get("safety") for v in reqs) and "3.18" in secs:
        crit = [v.eid for v in reqs if v.props.get("safety")]
        if not any(r in secs["3.18"]["body"] for r in crit):
            F.append(_f("warning", "outline", "3.18", "names none of the safety requirements", "DI-IPSC-81433A 3.18"))

    errors = sum(f["level"] == "error" for f in F)
    return {"ok": errors == 0, "errors": errors, "warnings": len(F) - errors, "findings": F,
            "requirements": [{"id": v.eid, "section": req_section[v.eid], **{k: v.props.get(k) for k in
                              ("text", "verification", "safety", "timing", "traces_to", "basis")}} for v in reqs],
            "draft_sha": sha(text)}


# ── The evidence pack ───────────────────────────────────────────────────────

def evidence_pack(text: str, brief: dict, assessment: dict, lint: dict, lib: Library, folder: Path,
                  provenance: dict) -> dict:
    """Write the pack into `folder`; returns {file: path}."""
    folder.mkdir(parents=True, exist_ok=True)
    doc, reqs, secs = assessment["document"], lint["requirements"], draft_sections(text)
    files = {}

    draft = folder / f"{doc['id']}.md"
    draft.write_text(text)
    files["draft"] = draft

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["requirement", "section", "text", "verification", "safety", "timing", "traces_to", "basis", "verified_by"])
    for r in reqs:
        w.writerow([r["id"], r["section"], r["text"], " ".join(r["verification"] or []), r["safety"] or "",
                    r["timing"] or "", "; ".join(r["traces_to"] or []), "; ".join(r["basis"] or []), "none yet (no STD)"])
    (folder / "traceability_matrix.csv").write_text(buf.getvalue())
    files["traceability_matrix"] = folder / "traceability_matrix.csv"

    by_req = {}
    for f in lint["findings"]:
        by_req.setdefault(f["where"], []).append(f)
    L = [f"# Compliance matrix: {doc['id']} rev {doc['revision']} (candidate)", "",
         f"Draft SHA-256 (first 16): `{lint['draft_sha']}`. Generated {provenance['generated']}.", "",
         "## DI-IPSC-81433A: SRS outline", "", "| Paragraph | DID title | In the draft | Requirements | Findings |",
         "|---|---|---|---|---|"]
    for num in DID_OUTLINE:
        did = lib.clauses("DI-IPSC-81433A")[num]["title"]
        n = sum(1 for r in reqs if r["section"] == num)
        state = ("missing" if num not in secs else
                 "tailored out" if "tailored out" in secs[num]["body"].lower() else
                 "states none" if not n and _STATES_NONE.search(secs[num]["body"]) else "present")
        L.append(f"| {num} | {did} | {state} | {n or ''} | {len(by_req.get(num, [])) or ''} |")
    L += ["", "## MIL-STD-882E 4.4: safety-significant functions", "",
          "| SSF | Function | Severity (Table I) | Control category (Table IV) | SwCI (Table V) | Implemented by | Verification |",
          "|---|---|---|---|---|---|---|"]
    for x in assessment["ssfs"]:
        imp = [r for r in reqs if x["id"] in (r["traces_to"] or [])]
        L.append(f"| {x['id']} | {x['name']} | {x['severity']} | {x['control_category']} | {x['swci']} | "
                 f"{', '.join(r['id'] for r in imp) or '**none**'} | {', '.join(sorted({m for r in imp for m in r['verification'] or []}))} |")
    L += ["", "Level of rigor these SwCIs require (MIL-STD-882E Table V). These are programme activities; this pack is "
          "**not** evidence that they were done:", ""]
    for swci in sorted({x["swci"] for x in assessment["ssfs"]}):
        L.append(f"- **{swci}**: {lib.level_of_rigor(swci)} If incomplete, the contribution to system risk is documented as "
                 f"**{lib.unmet_lor_risk(swci).upper()}** (Table VI).")
    L += ["", "## Standards trace: every Basis citation", "", "| Requirement | Citation | Resolves to |", "|---|---|---|"]
    for r in reqs:
        for b in r["basis"] or []:
            res = lib.resolve(b)
            L.append(f"| {r['id']} | {b} | {res['clause'] + ' ' + res['title'] if res['ok'] else '✗ ' + res['reason']} |")
    L += ["", "## ISO/IEC/IEEE 29148 characteristics", "",
          "Checked by ESAM heuristics named after the standard's characteristics (singular, unambiguous, complete, "
          "verifiable); the standard's text is not held. Findings are in `lint.md`."]
    (folder / "compliance_matrix.md").write_text("\n".join(L) + "\n")
    files["compliance_matrix"] = folder / "compliance_matrix.md"

    M = [f"# Lint: {doc['id']} ({lint['errors']} errors, {lint['warnings']} warnings)", ""]
    M += [f"- **{f['level']}** [{f['rule']}] {f['where']}: {f['message']}" + (f" _(basis: {f['basis']})_" if f['basis'] else "")
          for f in assessment["findings"] + lint["findings"]] or ["No findings."]
    (folder / "lint.md").write_text("\n".join(M) + "\n")
    (folder / "lint.json").write_text(json.dumps({"brief": assessment, "draft": lint}, indent=1, default=str))
    files["lint"] = folder / "lint.md"

    held = {sid: v.get("source", {}).get("sha256", "") for sid, v in lib.standards.items()}
    I = [f"# Evidence pack: {doc['id']} rev {doc['revision']}: {doc['name']}", "",
         "> **Status: candidate.** This draft has not been reviewed or approved by an SE and is not in the baseline.",
         "> Certification is the decision of the certifying authority. This pack is supporting evidence: it shows what",
         "> was checked, against which clauses, and what remains open. It is not a certification and not a claim of compliance.",
         "", "| | |", "|---|---|",
         f"| Subsystem | {doc['attached_to']} ({assessment['subsystem'].get('name')}) |",
         f"| CSCI SwCI (highest of its SSFs) | {assessment['csci_swci'] or 'none assessed'} |",
         f"| Requirements | {len(reqs)} ({sum(1 for r in reqs if r['safety'])} safety) |",
         f"| Lint | {lint['errors']} errors, {lint['warnings']} warnings (brief: {sum(f['level'] == 'error' for f in assessment['findings'])} errors, "
         f"{sum(f['level'] == 'warning' for f in assessment['findings'])} warnings) |",
         f"| Brief | `{brief['_path']}` (SHA-256 {brief['_sha']}) |",
         f"| Draft | `{draft.name}` (SHA-256 {lint['draft_sha']}) |",
         f"| Run | {provenance['run_id']}, environment {provenance['environment']}, model {provenance['model']} |",
         f"| Generated | {provenance['generated']} |", "",
         "## Contents", "", "- `" + draft.name + "`: the candidate SRS",
         "- `compliance_matrix.md`: DID outline, SSFs and SwCI, every standards citation resolved",
         "- `traceability_matrix.csv`: every requirement with its attributes (DI-IPSC-81433A 5a)",
         "- `lint.md`, `lint.json`: every finding with the rule and clause it applies",
         "- `se_reviewer_memo.md` (when the crew ran): the SE Reviewer agent's value, basis and hazard review", "",
         "## Standards library used", ""]
    I += [f"- {sid}: {lib.standards[sid]['title']} ({lib.standards[sid].get('date')}); source SHA-256 `{h or 'n/a (held by name)'}`"
          for sid, h in held.items()]
    I += ["", "## Open items for the SE", ""]
    I += [f"- {f['where']}: {f['message']}" for f in assessment["findings"] + lint["findings"] if f["level"] == "error"] or \
         ["- No errors. Review the warnings in `lint.md`."]
    I += ["- No test description verifies these requirements yet: write the STD (verification column of the traceability matrix).",
          "- Next: `python -m crew.main review " + str(draft) + "` puts the draft through Pipeline A (ontology check, review, SE approval)."]
    (folder / "evidence_pack.md").write_text("\n".join(I) + "\n")
    files["evidence_pack"] = folder / "evidence_pack.md"
    return {k: str(v) for k, v in files.items()}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
