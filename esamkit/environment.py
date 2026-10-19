"""
Step 0: what does THIS environment offer?

The same ESAM code runs on a learning laptop, on the IL5 pilot (gateway
models, VM-1 PostgreSQL, no internet) and later with JanusGraph. A profile
(config/environments/<name>.yaml) DECLARES what a place should have; the
probes here CHECK it, and `capabilities()` turns the result into the list of
tools the crew may use. A tool whose service is down is never handed to an
agent, so the crew says "the graph is unavailable" instead of failing halfway.

    python -m esamkit.environment                 # profile from ESAM_ENV (default: local)
    python -m esamkit.environment --env pilot-il5 --no-network

Secrets are named, never stored: a profile gives the NAME of an environment
variable (or a line in esam/.env) and probes report only "set" / "missing".
Probes never load the embedding model and never spend model tokens: an LLM
endpoint is checked by listing its models.
"""

import argparse
import json
import os
import platform
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .config import PROJECT_ROOT, Settings, settings as default_settings

PROFILE_DIR = PROJECT_ROOT / "config" / "environments"
OK = ("ok", "warning")

# Which services each crew tool needs. A tool is enabled only if all are OK.
TOOL_NEEDS = {
    "ontology_lookup": ["files"],
    "ontology_check": ["files"],
    "review_document": ["files"],
    "search_documents": ["vector", "embeddings"],
    "walk_graph": ["graph"],
    "check_coverage": ["graph"],
    "verified_by": ["graph"],
    "health_report": ["graph", "database"],
    "ingest_document": ["vector", "embeddings", "graph", "database"],
    "verify_ingestion": ["vector", "embeddings", "graph", "database"],
    "read_brief": ["files"],
    "read_source": ["files"],
    "standard_clause": ["files"],
    "search_standards": ["files"],
    "save_draft": ["files", "graph"],
    "check_draft": ["files", "graph"],
    "build_evidence_pack": ["files", "graph"],
}


@dataclass
class Probe:
    name: str
    status: str          # ok | warning | unavailable | not_configured | not_implemented
    detail: str
    data: dict = field(default_factory=dict)


# ── Profiles ────────────────────────────────────────────────────────────────

def profile_names() -> list[str]:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.yaml"))


def load_profile(name: str | None = None) -> dict:
    name = name or os.getenv("ESAM_ENV", "local")
    path = PROFILE_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no environment profile {name!r}; have {profile_names()}")
    return yaml.safe_load(path.read_text())


def _env(name: str | None) -> str:
    return os.getenv(name, "") if name else ""


def mask_dsn(dsn: str) -> str:
    """postgresql://user:secret@host/db -> postgresql://user:***@host/db"""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", dsn)


def resolve(profile: dict) -> dict:
    """Concrete values for this profile: URLs, keys (presence only), DSN."""
    roles = {}
    for role, spec in ((profile.get("llm") or {}).get("roles") or {}).items():
        if not spec:
            roles[role] = None
            continue
        roles[role] = {"model": spec.get("model", ""),
                       "base_url": spec.get("base_url") or _env(spec.get("base_url_env")),
                       "base_url_env": spec.get("base_url_env"),
                       "api_key_env": spec.get("api_key_env"),
                       "api_key_set": bool(_env(spec.get("api_key_env")))}
    db = profile.get("database") or {}
    dsn = _env(db.get("dsn_env")) or db.get("dsn_default", "")
    graph = profile.get("graph") or {}
    return {"roles": roles, "dsn": dsn, "dsn_masked": mask_dsn(dsn) if dsn else "",
            "dsn_source": ("env " + db.get("dsn_env", "")) if _env(db.get("dsn_env")) else
                          ("profile default (learning only)" if dsn else "none"),
            "graph_url": graph.get("url") or _env(graph.get("url_env"))}


def settings_for(profile: dict, base: Settings = default_settings) -> Settings:
    """esamkit Settings for this profile, so factories build the right backends."""
    r, reasoning = resolve(profile), (resolve(profile)["roles"].get("reasoning") or {})
    emb, vec, graph = profile.get("embeddings") or {}, profile.get("vector") or {}, profile.get("graph") or {}
    model_dir = Path(emb.get("model_dir", base.embed_model_dir))
    return replace(
        base,
        embed_backend=emb.get("backend", base.embed_backend),
        embed_model_id=emb.get("model_id", base.embed_model_id),
        embed_model_dir=model_dir if model_dir.is_absolute() else PROJECT_ROOT / model_dir,
        vector_backend=vec.get("backend", base.vector_backend),
        collection=vec.get("collection", base.collection),
        pg_dsn=r["dsn"] or base.pg_dsn,
        graph_backend=graph.get("backend", base.graph_backend),
        chat_base_url=reasoning.get("base_url", base.chat_base_url),
        chat_api_key=_env(reasoning.get("api_key_env")) or base.chat_api_key,
        chat_model=reasoning.get("model", base.chat_model),
    )


def apply_telemetry(profile: dict) -> None:
    """Turn CrewAI / OpenTelemetry phone-home off unless the profile says otherwise."""
    if (profile.get("telemetry") or {}).get("crewai", "off") == "off":
        os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
        os.environ["CREWAI_TRACING_ENABLED"] = "false"
        os.environ["OTEL_SDK_DISABLED"] = "true"


# ── Probes ──────────────────────────────────────────────────────────────────

def probe_host() -> Probe:
    cpu, flags, mem_gb = platform.processor() or "unknown", set(), 0.0
    try:
        info = Path("/proc/cpuinfo").read_text()
        cpu = re.search(r"model name\s*:\s*(.+)", info).group(1)
        flags = set(re.search(r"flags\s*:\s*(.+)", info).group(1).split())
        mem_gb = int(re.search(r"MemTotal:\s*(\d+)", Path("/proc/meminfo").read_text()).group(1)) / 1024 / 1024
    except (OSError, AttributeError):
        pass
    data = {"cpu": cpu, "cores": os.cpu_count(), "ram_gb": round(mem_gb, 1), "avx2": "avx2" in flags,
            "python": platform.python_version()}
    detail = f"{cpu}, {os.cpu_count()} cores, {data['ram_gb']} GB RAM, Python {data['python']}"
    if flags and "avx2" not in flags:
        return Probe("host", "warning", detail + ". No AVX2: local embedding can crash intermittently "
                                                 "(SIGILL); re-run if it does", data)
    return Probe("host", "ok", detail, data)


def probe_files(s: Settings) -> Probe:
    from .ontology import check_baseline, load_ontology

    missing = [str(p.relative_to(PROJECT_ROOT)) for p in (s.baseline_yaml, s.ontology_yaml, s.documents_dir)
               if not p.exists()]
    if missing:
        return Probe("files", "unavailable", "missing: " + ", ".join(missing))
    onto = load_ontology(s.ontology_yaml)
    findings = onto.check_self() + check_baseline(onto, yaml.safe_load(s.baseline_yaml.read_text()), s.documents_dir)
    errors = sum(f.level == "error" for f in findings)
    docs = len(list(s.documents_dir.glob("*.md")))
    try:
        from .standards import Library
        lib = Library()
        held = {sid: len(lib.clauses(sid)) for sid, v in lib.standards.items() if v.get("text")}
        standards = ", ".join(f"{k} {n} clauses" for k, n in held.items())
    except (OSError, KeyError, ValueError) as e:
        held, standards = {}, f"standards library unreadable ({type(e).__name__})"
    data = {"ontology_errors": errors, "ontology_warnings": len(findings) - errors, "documents": docs, "standards": held}
    status = "ok" if not errors and held and all(held.values()) else "warning"
    return Probe("files", status, f"baseline, ontology v{onto.data.get('metadata', {}).get('version', '?')}, "
                                  f"{docs} documents; baseline vs ontology: {errors} errors, "
                                  f"{len(findings) - errors} warnings; standards: {standards}", data)


def probe_database(profile: dict, r: dict) -> Probe:
    if (profile.get("database") or {}).get("backend", "none") == "none":
        return Probe("database", "not_configured", "profile has no database")
    if not r["dsn"]:
        return Probe("database", "not_configured",
                     f"set {(profile.get('database') or {}).get('dsn_env')} to the PostgreSQL connection string")
    try:
        import psycopg

        t = time.perf_counter()
        with psycopg.connect(r["dsn"], connect_timeout=3) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
            vec = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'").fetchone()
        ms = round((time.perf_counter() - t) * 1000)
    except Exception as e:  # report every failure the same way: unavailable, with the reason
        return Probe("database", "unavailable", f"{r['dsn_masked']}: {type(e).__name__}: {str(e).strip()[:160]}")
    data = {"server_version": version, "pgvector": vec[0] if vec else None, "connect_ms": ms,
            "dsn": r["dsn_masked"], "dsn_source": r["dsn_source"]}
    return Probe("database", "ok", f"PostgreSQL {version}, pgvector {data['pgvector'] or 'not installed'}, "
                                   f"{ms} ms, {r['dsn_masked']} ({r['dsn_source']})", data)


def probe_vector(profile: dict, r: dict, db: Probe) -> Probe:
    vec, emb = profile.get("vector") or {}, profile.get("embeddings") or {}
    if vec.get("backend", "none") == "none":
        return Probe("vector", "not_configured", "profile has no vector store: document search is off")
    if vec["backend"] != "pgvector":
        return Probe("vector", "not_implemented", f"vector backend {vec['backend']!r} has no store in this build")
    if db.status not in OK:
        return Probe("vector", "unavailable", f"needs the database ({db.status.replace('_', ' ')})")
    import psycopg

    from .vectorstores.pgvector_store import chunk_counts

    counts = chunk_counts(r["dsn"], vec.get("collection", "esam_documents"))
    with psycopg.connect(r["dsn"], connect_timeout=3) as conn:
        has = conn.execute("SELECT to_regclass('esam_collections')").fetchone()[0]
        stored = conn.execute("SELECT embed_model_id FROM esam_collections WHERE name = %s",
                              (vec.get("collection", "esam_documents"),)).fetchone() if has else None
    data = {"collection": vec.get("collection"), "chunks": sum(counts.values()), "documents": len(counts),
            "stored_model": stored[0] if stored else None}
    if stored and stored[0] != emb.get("model_id"):
        return Probe("vector", "unavailable", f"collection was embedded with {stored[0]} but the profile uses "
                                              f"{emb.get('model_id')}: re-ingest or change the profile", data)
    if not counts:
        return Probe("vector", "warning", "collection is empty: run the ingest", data)
    return Probe("vector", "ok", f"{data['chunks']} chunks from {data['documents']} documents, "
                                 f"embedded with {data['stored_model']}", data)


def probe_graph(profile: dict, r: dict, db: Probe) -> Probe:
    backend = (profile.get("graph") or {}).get("backend", "none")
    if backend == "none":
        return Probe("graph", "not_configured", "profile has no graph: graph, coverage and health tools are off")
    if backend == "janusgraph":
        url = r["graph_url"]
        if not url:
            return Probe("graph", "not_implemented", "JanusGraph declared; no URL set and no JanusGraph store in this build")
        u = urlparse(url)
        try:
            socket.create_connection((u.hostname, u.port or 8182), timeout=3).close()
            reach = "reachable"
        except OSError as e:
            reach = f"not reachable ({e.__class__.__name__})"
        return Probe("graph", "not_implemented", f"JanusGraph at {url} is {reach}, but this build has no "
                                                 "JanusGraph graph store yet", {"url": url})
    if backend != "postgres":
        return Probe("graph", "not_implemented", f"graph backend {backend!r} unknown")
    if db.status not in OK:
        return Probe("graph", "unavailable", f"needs the database ({db.status.replace('_', ' ')})")
    import psycopg

    with psycopg.connect(r["dsn"], connect_timeout=3) as conn:
        if conn.execute("SELECT to_regclass('graph_vertices')").fetchone()[0] is None:
            return Probe("graph", "warning", "graph tables not created yet: run the graph load")
        rows = dict(conn.execute("SELECT label, count(*) FROM graph_vertices GROUP BY label").fetchall())
        edges = conn.execute("SELECT count(*) FROM graph_edges").fetchone()[0]
    data = {"vertices": sum(rows.values()), "edges": edges, "by_label": rows}
    if not rows:
        return Probe("graph", "warning", "graph is empty: run the graph load", data)
    return Probe("graph", "ok", f"PostgreSQL recursive CTEs: {data['vertices']} vertices, {edges} edges "
                                f"({rows.get('Requirement', 0)} requirements, {rows.get('TestCase', 0)} test cases)", data)


def probe_embeddings(profile: dict, s: Settings) -> Probe:
    emb = profile.get("embeddings") or {}
    backend = emb.get("backend", "none")
    if backend == "none":
        return Probe("embeddings", "not_configured", "profile has no embedding model: document search is off")
    if backend == "openai":
        return Probe("embeddings", "warning", "OpenAI-compatible embeddings configured; this backend is untested")
    cfg = s.embed_model_dir / "config.json"
    if not cfg.exists():
        return Probe("embeddings", "unavailable", f"model not staged in {s.embed_model_dir}: run lessons/00_setup/stage_model.py")
    size = sum(f.stat().st_size for f in s.embed_model_dir.rglob("*") if f.is_file()) / 1e6
    dim = json.loads(cfg.read_text()).get("hidden_size")
    return Probe("embeddings", "ok", f"{emb.get('model_id')} staged locally ({size:.0f} MB, {dim}-dim, CPU, offline)",
                 {"model_id": emb.get("model_id"), "dim": dim, "mb": round(size)})


def probe_llm(profile: dict, r: dict, role: str, network: bool) -> Probe:
    spec = r["roles"].get(role)
    name = f"llm.{role}"
    if spec is None:
        return Probe(name, "not_configured", "no model for this role in the profile")
    if not spec["base_url"]:
        return Probe(name, "not_configured", f"set {spec['base_url_env']} to the endpoint URL")
    if not spec["api_key_set"]:
        return Probe(name, "not_configured", f"{spec['model']} at {spec['base_url']}: set {spec['api_key_env']}")
    host = urlparse(spec["base_url"]).hostname or ""
    public = not (host.endswith(".local") or host.startswith(("10.", "192.168.", "127.")) or host == "localhost")
    if not network:
        return Probe(name, "warning", f"{spec['model']} at {spec['base_url']}: key set; not contacted (--no-network)",
                     {"model": spec["model"]})
    if public and not (profile.get("network") or {}).get("internet", False):
        return Probe(name, "unavailable", f"{spec['base_url']} is a public host but the profile is air-gapped")
    req = urllib.request.Request(spec["base_url"].rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {_env(spec['api_key_env'])}"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            models = [m.get("id") for m in json.loads(resp.read()).get("data", [])]
    except urllib.error.HTTPError as e:
        return Probe(name, "unavailable", f"{spec['base_url']}: HTTP {e.code} (check {spec['api_key_env']})")
    except Exception as e:
        return Probe(name, "unavailable", f"{spec['base_url']}: {type(e).__name__}: {str(e)[:120]}")
    ms = round((time.perf_counter() - t) * 1000)
    data = {"model": spec["model"], "endpoint": spec["base_url"], "models_listed": len(models), "latency_ms": ms}
    if models and spec["model"] not in models:
        return Probe(name, "unavailable", f"{spec['model']} is not offered at {spec['base_url']} "
                                          f"({len(models)} models listed)", data)
    return Probe(name, "ok", f"{spec['model']} at {spec['base_url']}, key from {spec['api_key_env']}, {ms} ms", data)


def probe_crewai(profile: dict) -> Probe:
    try:
        import importlib.metadata as md

        version = md.version("crewai")
    except Exception:
        return Probe("crewai", "unavailable", "CrewAI not installed: pip install -r requirements-crew.txt")
    tele = (profile.get("telemetry") or {})
    detail = f"CrewAI {version}; telemetry {tele.get('crewai', 'off')}, crew memory {tele.get('crew_memory', 'off')}"
    if tele.get("crew_memory", "off") != "off" and (profile.get("embeddings") or {}).get("backend") != "openai":
        return Probe("crewai", "warning", detail + ": memory needs an embeddings API this profile doesn't have",
                     {"version": version})
    return Probe("crewai", "ok", detail, {"version": version})


# ── Putting it together ─────────────────────────────────────────────────────

def inspect(profile_name: str | None = None, network: bool = True) -> dict:
    profile = load_profile(profile_name)
    r, s = resolve(profile), settings_for(load_profile(profile_name))
    db = probe_database(profile, r)
    probes = [probe_host(), probe_files(s), db, probe_vector(profile, r, db), probe_graph(profile, r, db),
              probe_embeddings(profile, s)]
    probes += [probe_llm(profile, r, role, network) for role in r["roles"]]
    probes.append(probe_crewai(profile))
    return {"profile": profile["name"], "description": profile.get("description", ""),
            "probes": [asdict(p) for p in probes], "capabilities": capabilities(probes),
            "limits": profile.get("limits") or {}, "usage_sink": (profile.get("usage") or {}).get("sink", "file")}


def capabilities(probes: list[Probe]) -> dict:
    """Which crew tools may be used, and why a tool is off."""
    state = {p.name: p.status for p in probes}
    tools = {}
    for tool, needs in TOOL_NEEDS.items():
        bad = [n for n in needs if state.get(n) not in OK]
        tools[tool] = {"enabled": not bad, "blocked_by": bad}
    return {"tools": tools, "agents_can_run": state.get("llm.reasoning") in OK,
            "enabled": [t for t, v in tools.items() if v["enabled"]]}


def main() -> None:
    ap = argparse.ArgumentParser(description="Check what this environment offers (ESAM step 0).")
    ap.add_argument("--env", default=None, help=f"profile name ({', '.join(profile_names())}); default ESAM_ENV or local")
    ap.add_argument("--no-network", action="store_true", help="don't contact model endpoints")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = inspect(args.env, network=not args.no_network)
    if args.json:
        print(json.dumps(rep, indent=1))
        return
    mark = {"ok": "✓", "warning": "!", "unavailable": "✗", "not_configured": "–", "not_implemented": "–"}
    print(f"Environment: {rep['profile']}  ({rep['description']})\n")
    for p in rep["probes"]:
        print(f"  {mark[p['status']]} {p['name']:<14} {p['status']:<15} {p['detail']}")
    caps = rep["capabilities"]
    print("\nTools the crew may use:")
    for t, v in caps["tools"].items():
        print(f"  {'✓' if v['enabled'] else '✗'} {t:<17}" + ("" if v["enabled"] else f" off: needs {', '.join(v['blocked_by'])}"))
    print("\nAgents can run." if caps["agents_can_run"] else "\nAgents CANNOT run: no reasoning model is available.")
    sys.exit(0 if caps["agents_can_run"] else 1)


if __name__ == "__main__":
    main()
