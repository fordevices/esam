# Lesson 04: Graph queries with recursive CTEs

Lesson 03 ended on a question vector search couldn't answer: *"if EBC1 changes, which subsystems
are affected?"* The answer isn't written in any chunk. It's a **chain of relationships** spread
across the baseline. This lesson answers it by following edges.

Needs only Postgres (`docker compose up -d --wait`). No embedding model.

```bash
python lessons/04_graph/load_graph.py --reset
python lessons/04_graph/queries.py          # SQL + results for Q1–Q4
python lessons/04_graph/queries.py q3       # just the impact query
```

## Vectors vs. graph, side by side

| | Vector store (lesson 03) | Graph (this lesson) |
|---|---|---|
| Stores | text chunks + 768 numbers each | things (vertices) + typed connections (edges) |
| Question shape | "what *says* something like this?" | "what is *connected* to this, how, and how far?" |
| Answer is | ranked, approximate (scores) | exact, complete within the data (rows) |
| Can it find something with **no documents**? | never: no text, no vector | yes: the engine ECM shows up in Q3 |
| Can it find something nobody **linked**? | yes, if the text is similar | never: no edge, no path |

They fail in opposite places, which is why ESAM uses both (lesson 05).

## How a recursive CTE thinks

```
WITH RECURSIVE reach AS (
    <anchor>          -- round 0: the start row(s)
    UNION ALL
    <recursive step>  -- round n+1: rows from round n, joined one hop further
)
SELECT ... FROM reach;
```

Postgres runs the anchor once, then runs the step again and again, each time feeding it **only
the rows found in the previous round**, until a round adds nothing. For Q3 on our data:

| Round | Rows added | Via edge |
|---|---|---|
| 0 | SUB-BRK-ECU | (anchor) |
| 1 | SUB-J1939-BB | BRK-ECU → BB, EBC1, confirmed |
| 2 | SUB-DCL, SUB-ENG-ECM | BB → DCL confirmed; BB → ECM **candidate** |
| 3 | nothing new, so it stops | |

Three guards keep it safe. All three are in the SQL with comments:

1. `path` array plus `NOT id = ANY(path)` stops cycles (A → B → A → …).
2. `depth < max_depth` sets a hard ceiling, in case the data is wider than expected.
3. A `weakest` status carried along the path means one unconfirmed hop makes the whole result
   `candidate`. An agent must never report a candidate impact as fact. This is the
   candidate/confirmed rule from SCHEMA-GRAPH-003.

## What changed from the earlier `embeddings.py`

| Earlier script | Here |
|---|---|
| Graph queries were Python loops over a JSON copy of the graph | Real recursive CTEs in the database, as the ESAM pilot doc specifies |
| `ON CONFLICT DO NOTHING` with no unique constraint on edges, so re-runs duplicated every edge | Unique index on `(from_id, to_id, type, message)`. `load_graph.py` loads twice to prove it |
| Subsystem `wbs_code` invented as `"<system>.sub"` | Not stored; derived by one `PART_OF` hop (Q2) |
| Document ↔ artifact link guessed from file names | Explicit `file:` in the baseline |

## Read next

[`gremlin_equivalents.md`](gremlin_equivalents.md) shows the same four queries in Gremlin
(JanusGraph, your full-deployment target) and Cypher, with the mental-model differences.

## Try this

1. In the YAML, change the engine ECM edge to `status: confirmed`, re-run `load_graph.py`, then
   run `queries.py q3`. The ECM row flips to `confirmed`, and that's the only change: no
   duplicates.
2. Add a second consumer: give `SUB-DCL` an outgoing `INTERFACES_WITH` edge to a new
   `SUB-TCU` (telematics) carrying EBC1. Q3 now reaches depth 3 with no change to the query.
3. Run Q3 with `message="DM1 (PGN 65226)"` from a Python shell. It returns no rows, because no
   DM1 edges exist in the baseline. What does that tell you about where the baseline is
   incomplete?
