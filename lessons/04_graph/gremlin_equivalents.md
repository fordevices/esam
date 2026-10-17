# The same four questions in Gremlin (and Cypher)

The pilot runs graph queries as SQL recursive CTEs. The full deployment targets **JanusGraph**,
which is queried in **Gremlin** (Apache TinkerPop). Many graph tutorials use **Cypher** (Neo4j,
and the ISO GQL standard derived from it), so it's shown too, for reading other material.

> **Status:** written for teaching, **not executed**. No Gremlin server or Neo4j is available
> here. Treat these as the shape of each query, and test them against a real server before use.

## The mental-model shift

| SQL recursive CTE | Gremlin | Cypher |
|---|---|---|
| You describe *rounds*: anchor + "join previous round to neighbours" | You describe a *walker*: start here, step along these edges, repeat | You draw a *pattern*: `(a)-[:REL*1..5]->(b)` |
| Edges are rows you JOIN | Edges are things you step *across* (`out`, `in`, `outE`, `inV`) | Edges are arrows in the picture |
| Cycle guard: `NOT id = ANY(path)` | `simplePath()` | built in: a path never reuses a relationship |
| Depth limit: `WHERE depth < n` | `times(n)` | `*1..n` |

One naming note for the migration: our vertices keep the entity id in a property called `id`.
In Gremlin, `id` is easy to confuse with the element's own identifier (`T.id`), and some
providers reserve it. Rename it to `eid` when bulk-loading into JanusGraph. The examples below
assume that rename.

---

## Q1: descendants (walk down the WBS)

**Gremlin**
```groovy
g.V().has('Vehicle', 'eid', 'V-001').
  emit().                       // include the start vertex and every vertex we pass
  repeat(__.in('PART_OF')).     // step against PART_OF (child -PART_OF-> parent, so in() = down)
  path().by('eid')              // the route to each, e.g. [V-001, FAM-001, VAR-001, SYS-SUSP]
```

**Cypher**
```cypher
MATCH p = (v:Vehicle {id: 'V-001'})<-[:PART_OF*0..]-(x)
RETURN [n IN nodes(p) | n.id] AS path
```

## Q2: ancestors (document up to the vehicle)

**Gremlin**
```groovy
g.V().has('DesignArtifact', 'eid', 'ICD-BRK-001').
  in('HAS_ARTIFACT').           // first hop: artifact -> owning subsystem
  emit().repeat(out('PART_OF')).// then keep going up
  valueMap('eid', 'wbs_code')
```
The SQL needed a `CASE` to handle "first hop is a different edge type". In Gremlin it's simply
two steps in sequence.

**Cypher**
```cypher
MATCH (a:DesignArtifact {id: 'ICD-BRK-001'})<-[:HAS_ARTIFACT]-(s)-[:PART_OF*0..]->(up)
RETURN up.id, up.wbs_code
```

## Q3: impact of changing EBC1

**Gremlin**
```groovy
g.V().has('Subsystem', 'eid', 'SUB-BRK-ECU').
  repeat(
    outE('INTERFACES_WITH').has('message', 'EBC1 (PGN 61441)').  // only edges carrying EBC1
    inV().simplePath()                                           // cross it; never revisit
  ).emit().times(5).
  path().by('eid').by('status')   // alternates vertex, edge, vertex, ...:
                                  // [SUB-BRK-ECU, confirmed, SUB-J1939-BB, candidate, SUB-ENG-ECM]
```
The SQL carried a `weakest` column so the result could say "candidate". Here the path already
contains every edge's status, and the caller checks whether any of them is `candidate`.

**Cypher**
```cypher
MATCH p = (s:Subsystem {id: 'SUB-BRK-ECU'})-[r:INTERFACES_WITH*1..5]->(n)
WHERE all(e IN r WHERE e.message = 'EBC1 (PGN 61441)')
RETURN n.id,
       CASE WHEN any(e IN r WHERE e.status = 'candidate') THEN 'candidate' ELSE 'confirmed' END AS status,
       [x IN nodes(p) | x.id] AS path
```

## Q4: subsystems with no ICD

**Gremlin**
```groovy
g.V().hasLabel('Subsystem').
  not(out('HAS_ARTIFACT').has('artifact_type', 'icd')).
  values('eid')
```

**Cypher**
```cypher
MATCH (s:Subsystem)
WHERE NOT (s)-[:HAS_ARTIFACT]->(:DesignArtifact {artifact_type: 'icd'})
RETURN s.id
```

---

## So why not just use a graph database now?

ESAM's answer, which is a sound one: at pilot scale (a few thousand WBS nodes) recursive CTEs are
fast enough, and Postgres is already approved and running. The costs grow with depth and fan-out:
every round of a CTE is another join over the whole edge table. A native graph engine stores each
vertex's neighbours *with the vertex*, so a hop costs about the same however big the graph gets.
That's the honest reason JanusGraph is on the roadmap. Because callers only see
`esamkit.graphstores.GraphStore`, moving there means writing a `JanusGraphStore` whose four
methods send the Gremlin above. Lesson scripts and agent tools don't change.
