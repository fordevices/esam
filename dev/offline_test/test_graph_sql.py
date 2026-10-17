"""Run esamkit's graph SQL through psql (no psycopg available offline).
Renders %(name)s params as SQL literals, executes, prints results."""
import json, re, subprocess, sys
from pathlib import Path
ESAM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ESAM))
from esamkit.baseline import load_baseline
from esamkit.graphstores import postgres_cte as q

PSQL = ["psql", "-h", "127.0.0.1", "-p", "55433", "-U", "tester", "-d", "esamtest",
        "-v", "ON_ERROR_STOP=1", "-q", "-P", "pager=off"]

def lit(v):
    if v is None: return "NULL"
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("'", "''") + "'"

def render(sql, **p):
    return re.sub(r"%\((\w+)\)s", lambda m: lit(p[m.group(1)]), sql)

def run(sql, title=None):
    if title: print(f"\n===== {title}")
    r = subprocess.run(PSQL, input=sql, text=True, capture_output=True)
    if r.returncode: print(r.stderr); sys.exit(1)
    print(r.stdout.rstrip())

b = load_baseline(ESAM / "data/baseline/vehicle_decomposition.yaml")
load = [q.SCHEMA, "BEGIN;"]
for v in b.vertices:
    load.append(render(q.UPSERT_VERTEX, label=v.label, props=json.dumps(v.props)) + ";")
for e in b.edges:
    load.append(render(q.UPSERT_EDGE, type=e.type, props=json.dumps(e.props),
                       from_eid=e.from_eid, to_eid=e.to_eid) + ";")
load.append("COMMIT;")
count = "SELECT (SELECT count(*) FROM graph_vertices) AS vertices, (SELECT count(*) FROM graph_edges) AS edges;"
run("\n".join(load) + count, "load #1")
run("\n".join(load[1:]) + count, "load #2 (must be identical: idempotent)")
run(render(q.DESCENDANTS, start="V-001") + ";", "Q1 descendants V-001")
run(render(q.ANCESTORS, start="ICD-BRK-001") + ";", "Q2 ancestors ICD-BRK-001")
run(render(q.IMPACT, start="SUB-BRK-ECU", message="EBC1 (PGN 61441)", max_depth=5) + ";", "Q3 impact EBC1")
run(render(q.IMPACT, start="SUB-BRK-ECU", message=None, max_depth=1) + ";", "Q3b impact any message, depth 1")
run(render(q.IMPACT, start="SUB-BRK-ECU", message="DM1 (PGN 65226)", max_depth=5) + ";", "Q3c impact DM1 (expect none)")
run(render(q.ARTIFACT_GAPS, artifact_type="icd") + ";", "Q4 gaps icd")
run(render(q.ARTIFACT_GAPS, artifact_type="ssr") + ";", "Q4 gaps ssr")
