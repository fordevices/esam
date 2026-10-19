#!/usr/bin/env python3
"""
Lesson 04b — Graph questions as SQL recursive CTEs.

    python lessons/04_graph/queries.py            # all four, SQL shown
    python lessons/04_graph/queries.py --no-sql   # results only
    python lessons/04_graph/queries.py q3         # just one

  Q1  descendants : the whole WBS tree under the vehicle
  Q2  ancestors   : from a document UP to its WBS codes
  Q3  impact      : "the brake ECU changes EBC1, so who is affected?"
                    (the question lesson 03's vector search could NOT answer)
  Q4  gaps        : which subsystems have no ICD / no SRS?

Each query's SQL lives in esamkit/graphstores/postgres_cte.py with
line-by-line comments. This script prints it next to the result so you can
read the two together. To experiment, paste the SQL into psql:
    docker exec -it esam-postgres psql -U esam_user -d esam_pilot
replacing %(start)s etc. with quoted values, e.g. 'SUB-BRK-ECU'.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from esamkit.factory import get_graph_store  # noqa: E402
from esamkit.graphstores import postgres_cte as sql  # noqa: E402


def table(rows: list[dict], cols: list[str]) -> None:
    """Tiny fixed-width table printer (no extra dependency to carry across)."""
    if not rows:
        print("  (no rows)")
        return
    cells = [[("" if r[c] is None else str(r[c])) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    print("  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  " + "  ".join("─" * w for w in widths))
    for row in cells:
        print("  " + "  ".join(v.ljust(w) for v, w in zip(row, widths)))


def banner(title: str, query: str, show_sql: bool) -> None:
    print("\n" + "═" * 78 + f"\n{title}\n" + "═" * 78)
    if show_sql:
        print(query.strip() + "\n")


def q1(g, show):
    banner("Q1 — DESCENDANTS of V-001 (walk DOWN the WBS)", sql.DESCENDANTS, show)
    rows = g.descendants("V-001")
    for r in rows:
        r["tree"] = "  " * r["depth"] + r["eid"]
    table(rows, ["tree", "label", "wbs_code", "os_class", "safety_critical", "name"])
    print("""
The CTE ran one round per level: round 0 found the vehicle, round 1 its
family, ... round 5 the functions. It stopped by itself when a round found
no new rows. You never said how deep to go. Subsystems show no wbs_code of
their own: their code is their System's, one PART_OF hop up.""")


def q2(g, show):
    banner("Q2 — ANCESTORS of ICD-BRK-001 (document -> vehicle)", sql.ANCESTORS, show)
    table(g.ancestors("ICD-BRK-001"), ["depth", "label", "eid", "wbs_code", "name"])
    print("""
This is the walk you did by hand in lesson 01. Lesson 03 got the same
wbs_code from chunk METADATA, copied at ingest time. The graph computes it
live, so if an SE moves a subsystem to another System in the baseline, the
graph answer changes on the next load_graph. Chunk metadata stays stale
until the documents are re-ingested.""")


def q3(g, show):
    banner('Q3 — IMPACT: SUB-BRK-ECU changes "EBC1 (PGN 61441)"', sql.IMPACT, show)
    rows = g.impact("SUB-BRK-ECU", message="EBC1 (PGN 61441)")
    for r in rows:
        r["artifacts"] = ", ".join(r["artifacts"]) or "— none —"
    table(rows, ["depth", "eid", "status", "safety_critical", "path", "artifacts"])
    print("""
Compare with lesson 03, question 4. Vector search found chunks that MENTION
EBC1. The graph gives the actual list:
  * SUB-J1939-BB  depth 1: the bus itself. Safety-critical, and no documents.
  * SUB-DCL       depth 2: reached THROUGH the bus. Re-review SDD-DCL-001,
                  SRS-DCL-001.
  * SUB-ENG-ECM   depth 2, status CANDIDATE: possibly affected, but the edge
                  was never confirmed. It has no documents, so no amount of
                  text search could ever have surfaced it.

That last row is the whole argument for the graph layer.""")


def q4(g, show):
    banner("Q4 — GAPS: subsystems with no ICD, and with no SRS", sql.ARTIFACT_GAPS, show)
    print("No ICD:")
    table(g.artifact_gaps("icd"), ["eid", "wbs_code", "safety_critical", "name"])
    print("\nNo SRS:")
    table(g.artifact_gaps("srs"), ["eid", "wbs_code", "safety_critical", "name"])
    print("""
Not every gap is a defect. The cluster has no ICD of its own because the
EBC1 interface is documented once, by its producer (ICD-BRK-001). The SE
decides which gaps matter; the graph's job is to make them visible. A
safety-critical subsystem (the backbone) with NO documents at all is the one
to raise first.""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="?", choices=["q1", "q2", "q3", "q4"])
    ap.add_argument("--no-sql", action="store_true")
    args = ap.parse_args()

    g = get_graph_store()
    show = not args.no_sql
    for name, fn in (("q1", q1), ("q2", q2), ("q3", q3), ("q4", q4)):
        if args.only in (None, name):
            fn(g, show)


if __name__ == "__main__":
    main()
