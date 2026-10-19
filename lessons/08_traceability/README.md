# Lesson 08: Requirements and test cases as vertices (traceability)

The baseline lists documents. This lesson reads what's *inside* the SRS and STD documents and puts it in the
graph, so "which requirements have no test?" is a query, not a guess. SE-facing explanation: manual §10.

```
Requirement ──SPECIFIED_IN──► DesignArtifact (its SRS)
Requirement ──ALLOCATED_TO──► Subsystem      (the SRS's subsystem)
TestCase    ──SPECIFIED_IN──► DesignArtifact (its STD)
TestCase    ──VERIFIES──────► Requirement    (from its "Verifies:" line)
```

## Run it

`lessons/04_graph/load_graph.py` now loads traceability after the baseline:

```
After load #2: {'vertices': 20, 'edges': 22}   <- must equal load #1
Idempotent ✓. Re-loading an unchanged baseline changes nothing.

Traceability (from the documents): 16 requirements, 0 test cases, 0 verifies links
```

0 test cases because no STD is in the corpus. To see coverage move, keep lesson 06's STD loaded:

```bash
python lessons/06_missing_document/run_test.py --only brake-std --keep    # Brake ECU: 4 of 9 verified
python lessons/06_missing_document/run_test.py --only brake-std           # runs again and restores BOTH stores
```

## The code

| Where | What |
|---|---|
| `esamkit/traceability.py` | `read_requirements()` parses `- **SRS-…**` lines (continuations indented); `read_test_cases()` parses `## TC-… — title` headings and `Verifies:` / `Method:` / `Pass criteria:` fields. `load_traceability()` replaces what each document contributed (`remove_derived`), then upserts |
| `esamkit/graphstores/postgres_cte.py` | `UNTESTED_REQUIREMENTS`, `VERIFIED_BY`, `TRACE_SUMMARY`: one-hop anti-joins and counts, like lesson 04's Q4 |
| `esamkit/hybrid.py` step 3b | A verification question that names a requirement asks `verified_by()`; the prompt gets a `TRACEABILITY` section with `MISSING: no test case verifies …` or the verifying test case and its document |
| `esamkit/confidence.py` | Lesson 06 reloads traceability after ingesting, and the brake scenario checks `TC-BRK-011 verifies SRS-BRK-011` |
| `cockpit/` | Dashboard "Requirement coverage" panel; requirements/test cases in the Graph tab, with "Verified by" / "Verifies" in the details panel |
| `data/ontology/esam_ontology.yaml` | `Requirement` and `TestCase` (`source: document`); `VERIFIES`, `SPECIFIED_IN`, `ALLOCATED_TO` no longer "planned" |

## Design choices

- **Deterministic parsing, no AI.** These are controlled documents with a required format (manual §9). If a
  document doesn't follow it, its items are missing from the graph, which the coverage panel makes visible.
- **Every vertex records `source_artifact`.** Reloading a document deletes what its previous revision contributed
  first, so deleted requirements don't linger. Edges cascade with the vertices.
- **Dangling references are reported, not stored.** A `Verifies: SRS-BRK-999` with no such requirement becomes an
  `unresolved` entry (printed by `load_graph.py`) and stays in the test case's `verifies` property.
- **Separate from the baseline counts.** The baseline still loads to exactly 20 vertices / 22 edges (the
  idempotency check); traceability adds 16 requirements and 32 edges on top.

`tests/test_traceability.py` covers parsing, loading, revision replacement, the typo report and the prompt.
