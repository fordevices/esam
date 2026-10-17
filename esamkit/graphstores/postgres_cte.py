"""
Graph store on plain PostgreSQL tables + recursive CTEs (the ESAM pilot design).

Schema = the pilot's graph_vertices / graph_edges (ESAM_Pilot_Implementation
§4.2, and the earlier embeddings.py), with one fix: a UNIQUE index on edges.
Without it, `ON CONFLICT DO NOTHING` never fires and every re-load of the
baseline duplicates every edge.

No pgvector needed for this part. The graph tables can live in the same
database as the vector chunks (they do on the pilot) or a different one.

The SQL is kept as named module-level constants so lesson 04 can print each
query next to its results. Read them top to bottom; each one is commented.
"""

import json

from ..baseline import Baseline
from .base import GraphStore

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS graph_vertices (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    label      TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The entity id ("SUB-BRK-ECU") lives in properties->>'id' and must be unique.
CREATE UNIQUE INDEX IF NOT EXISTS idx_gv_entity_id ON graph_vertices ((properties->>'id'));
CREATE INDEX IF NOT EXISTS idx_gv_label ON graph_vertices (label);

CREATE TABLE IF NOT EXISTS graph_edges (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    from_id    UUID NOT NULL REFERENCES graph_vertices(id) ON DELETE CASCADE,
    to_id      UUID NOT NULL REFERENCES graph_vertices(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    properties JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- THE FIX: one edge per (from, to, type, message). Two different J1939
-- messages between the same pair are two edges; the same one loaded twice is one.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ge_unique
    ON graph_edges (from_id, to_id, type, (COALESCE(properties->>'message', '')));
-- Recursive walks join on from_id and to_id over and over; index both.
CREATE INDEX IF NOT EXISTS idx_ge_from ON graph_edges (from_id, type);
CREATE INDEX IF NOT EXISTS idx_ge_to   ON graph_edges (to_id, type);
"""

UPSERT_VERTEX = """
INSERT INTO graph_vertices (label, properties)
VALUES (%(label)s, %(props)s::jsonb)
ON CONFLICT ((properties->>'id'))
DO UPDATE SET label = EXCLUDED.label, properties = EXCLUDED.properties, updated_at = now()
"""

# Edges are given by ENTITY ids; the sub-selects turn them into vertex UUIDs.
UPSERT_EDGE = """
INSERT INTO graph_edges (from_id, to_id, type, properties)
SELECT f.id, t.id, %(type)s, %(props)s::jsonb
FROM graph_vertices f, graph_vertices t
WHERE f.properties->>'id' = %(from_eid)s AND t.properties->>'id' = %(to_eid)s
ON CONFLICT (from_id, to_id, type, (COALESCE(properties->>'message', '')))
DO UPDATE SET properties = EXCLUDED.properties
"""

# ─────────────────────────────────────────────────────────────────────────────
# Q1 — DESCENDANTS: walk DOWN the WBS.
#
# A recursive CTE has two halves joined by UNION ALL:
#   * the ANCHOR runs once: "start here"
#   * the RECURSIVE part runs repeatedly, each time joining the rows found in
#     the PREVIOUS round to their neighbours, until a round finds nothing new.
#
# Children point at parents (child -PART_OF-> parent), so to go DOWN we look
# for edges whose to_id is the current row.
#
# `path` is the list of entity ids from the start to this row. It does two
# jobs: ORDER BY path prints the result as an indented tree, and
# `NOT ... = ANY(path)` stops the walk if the data ever contains a cycle.
# ─────────────────────────────────────────────────────────────────────────────
DESCENDANTS = """
WITH RECURSIVE tree AS (
    -- anchor: the starting vertex
    SELECT v.id, v.label, v.properties, 0 AS depth,
           ARRAY[v.properties->>'id'] AS path
    FROM graph_vertices v
    WHERE v.properties->>'id' = %(start)s

    UNION ALL

    -- recursive step: every vertex that is PART_OF something already found
    SELECT c.id, c.label, c.properties, t.depth + 1,
           t.path || (c.properties->>'id')
    FROM tree t
    JOIN graph_edges e    ON e.to_id = t.id AND e.type = 'PART_OF'
    JOIN graph_vertices c ON c.id = e.from_id
    WHERE NOT (c.properties->>'id') = ANY(t.path)
)
SELECT depth, label,
       properties->>'id'       AS eid,
       properties->>'name'     AS name,
       properties->>'wbs_code' AS wbs_code,
       properties->>'os_class' AS os_class,
       (properties->>'safety_critical')::boolean AS safety_critical
FROM tree
ORDER BY path
"""

# ─────────────────────────────────────────────────────────────────────────────
# Q2 — ANCESTORS: from a document UP to the vehicle.
#
# The first hop is different from the rest: artifact <-HAS_ARTIFACT- owner,
# then owner -PART_OF-> parent -PART_OF-> ... So the recursive step accepts
# EITHER edge type and picks the right end of it with a CASE.
# ─────────────────────────────────────────────────────────────────────────────
ANCESTORS = """
WITH RECURSIVE up AS (
    SELECT v.id, v.label, v.properties, 0 AS depth, ARRAY[v.properties->>'id'] AS path
    FROM graph_vertices v
    WHERE v.properties->>'id' = %(start)s

    UNION ALL

    SELECT p.id, p.label, p.properties, u.depth + 1, u.path || (p.properties->>'id')
    FROM up u
    JOIN graph_edges e ON (e.type = 'PART_OF'      AND e.from_id = u.id)   -- child -> parent
                       OR (e.type = 'HAS_ARTIFACT' AND e.to_id   = u.id)   -- artifact -> owner
    JOIN graph_vertices p
      ON p.id = CASE WHEN e.type = 'PART_OF' THEN e.to_id ELSE e.from_id END
    WHERE NOT (p.properties->>'id') = ANY(u.path)
)
SELECT depth, label,
       properties->>'id'       AS eid,
       properties->>'name'     AS name,
       properties->>'wbs_code' AS wbs_code
FROM up
ORDER BY depth
"""

# ─────────────────────────────────────────────────────────────────────────────
# Q3 — IMPACT: "if this subsystem changes message X, who is affected?"
#
# Follow INTERFACES_WITH edges outward, hop after hop: brake ECU -> backbone
# -> cluster, and -> engine ECM. Two things are carried along each path:
#   * `via`: the message on the last hop
#   * `weakest`: 'candidate' if ANY hop on the path is unconfirmed. An impact
#     that depends on an unreviewed edge must be reported as such, never as fact.
#
# After the walk, a LATERAL sub-query collects each reached subsystem's
# documents. "Affected" isn't actionable unless you know what to re-review.
# ─────────────────────────────────────────────────────────────────────────────
IMPACT = """
WITH RECURSIVE reach AS (
    SELECT v.id, 0 AS depth, ARRAY[v.properties->>'id'] AS path,
           'confirmed'::text AS weakest, ''::text AS via
    FROM graph_vertices v
    WHERE v.properties->>'id' = %(start)s

    UNION ALL

    SELECT n.id, r.depth + 1, r.path || (n.properties->>'id'),
           CASE WHEN r.weakest = 'candidate' OR e.properties->>'status' = 'candidate'
                THEN 'candidate' ELSE 'confirmed' END,
           e.properties->>'message'
    FROM reach r
    JOIN graph_edges e    ON e.from_id = r.id AND e.type = 'INTERFACES_WITH'
    JOIN graph_vertices n ON n.id = e.to_id
    WHERE r.depth < %(max_depth)s
      AND NOT (n.properties->>'id') = ANY(r.path)
      AND (%(message)s::text IS NULL OR e.properties->>'message' = %(message)s::text)
)
SELECT r.depth,
       v.properties->>'id'   AS eid,
       v.properties->>'name' AS name,
       (v.properties->>'safety_critical')::boolean AS safety_critical,
       r.weakest             AS status,
       r.via                 AS message,
       array_to_string(r.path, ' -> ') AS path,
       docs.artifacts
FROM reach r
JOIN graph_vertices v ON v.id = r.id
LEFT JOIN LATERAL (
    SELECT COALESCE(array_agg(a.properties->>'id' ORDER BY a.properties->>'id'), '{}') AS artifacts
    FROM graph_edges h
    JOIN graph_vertices a ON a.id = h.to_id
    WHERE h.from_id = r.id AND h.type = 'HAS_ARTIFACT'
) docs ON true
WHERE r.depth > 0
ORDER BY r.depth, eid
"""

# ─────────────────────────────────────────────────────────────────────────────
# ORIGINS: where does a message come from?
# A vertex with an outgoing edge for the message and NO incoming edge for it.
# (The backbone also sends EBC1 onwards, but it receives it first, so it's
# a relay, not an origin.) Lesson 05 uses this to start impact walks at the
# right end of an interface. Vector search has no notion of direction.
# ─────────────────────────────────────────────────────────────────────────────
MESSAGE_ORIGINS = """
SELECT DISTINCT v.properties->>'id' AS eid, v.properties->>'name' AS name
FROM graph_edges e
JOIN graph_vertices v ON v.id = e.from_id
WHERE e.type = 'INTERFACES_WITH' AND e.properties->>'message' = %(message)s
  AND NOT EXISTS (
      SELECT 1 FROM graph_edges i
      WHERE i.to_id = v.id AND i.type = 'INTERFACES_WITH'
        AND i.properties->>'message' = %(message)s
  )
ORDER BY eid
"""

# ─────────────────────────────────────────────────────────────────────────────
# Q4 — GAPS: subsystems with no artifact of a given type.
# Not recursive. A one-hop "anti-join" (NOT EXISTS). Graph questions aren't
# all deep walks; "what is MISSING a connection" is just as useful, and it's
# a question vector search can never answer (absent text has no vector).
# ─────────────────────────────────────────────────────────────────────────────
ARTIFACT_GAPS = """
SELECT s.properties->>'id'   AS eid,
       s.properties->>'name' AS name,
       (s.properties->>'safety_critical')::boolean AS safety_critical,
       sys.properties->>'wbs_code' AS wbs_code
FROM graph_vertices s
JOIN graph_edges po     ON po.from_id = s.id AND po.type = 'PART_OF'
JOIN graph_vertices sys ON sys.id = po.to_id
WHERE s.label = 'Subsystem'
  AND NOT EXISTS (
      SELECT 1
      FROM graph_edges h
      JOIN graph_vertices a ON a.id = h.to_id
      WHERE h.from_id = s.id AND h.type = 'HAS_ARTIFACT'
        AND a.properties->>'artifact_type' = %(artifact_type)s
  )
ORDER BY wbs_code, eid
"""


class PostgresCTEGraphStore(GraphStore):
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("ESAM_PG_DSN (or ESAM_GRAPH_DSN) must be set")
        import psycopg
        from psycopg.rows import dict_row

        self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self._conn.execute(SCHEMA)

    def _rows(self, sql: str, **params) -> list[dict]:
        return self._conn.execute(sql, params).fetchall()

    def _count(self) -> dict:
        return {
            "vertices": self._conn.execute("SELECT count(*) AS n FROM graph_vertices").fetchone()["n"],
            "edges": self._conn.execute("SELECT count(*) AS n FROM graph_edges").fetchone()["n"],
        }

    def load(self, baseline: Baseline) -> dict:
        # One transaction: a baseline loads completely or not at all, so the
        # graph is never left half-updated.
        with self._conn.transaction():
            for v in baseline.vertices:
                self._conn.execute(UPSERT_VERTEX, {"label": v.label, "props": json.dumps(v.props)})
            for e in baseline.edges:
                self._conn.execute(UPSERT_EDGE, {"type": e.type, "props": json.dumps(e.props),
                                                 "from_eid": e.from_eid, "to_eid": e.to_eid})
        return self._count()

    def reset(self) -> None:
        self._conn.execute("TRUNCATE graph_edges, graph_vertices")

    def descendants(self, eid: str) -> list[dict]:
        return self._rows(DESCENDANTS, start=eid)

    def ancestors(self, eid: str) -> list[dict]:
        return self._rows(ANCESTORS, start=eid)

    def impact(self, eid: str, message: str | None = None, max_depth: int = 5) -> list[dict]:
        return self._rows(IMPACT, start=eid, message=message, max_depth=max_depth)

    def message_origins(self, message: str) -> list[dict]:
        return self._rows(MESSAGE_ORIGINS, message=message)

    def artifact_gaps(self, artifact_type: str) -> list[dict]:
        return self._rows(ARTIFACT_GAPS, artifact_type=artifact_type)
