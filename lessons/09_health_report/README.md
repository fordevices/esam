# Lesson 09: The baseline health report

Six checks that say what is still **missing or wrong** (the Diff Matrix says what's *changing*). SE-facing
explanation: manual §5.2–5.3. Code: `esamkit/health.py`.

```bash
python -m esamkit.health          # exit 1 if anything blocks approval
```

It also shows at the top of the Cockpit Dashboard (`GET /api/health-report`), and a one-line summary is printed
at the end of `lessons/03_vector_store/ingest.py` and `lessons/04_graph/load_graph.py`:

```
Health: nothing blocks; required documents 4 (2 ⚠), interfaces 3 (1 ⚠), awaiting SE review 1 (1 ⚠), traceability 16 (9 ⚠), roll-up 3. Details: python -m esamkit.health
```

| # | Check | Built from | Blocks? |
|---|---|---|---|
| 1 | Required documents | `Ontology.required_documents()` per subsystem (lesson 07) vs HAS_ARTIFACT edges | no |
| 2 | Interfaces | INTERFACES_WITH edges: receiving end without an ICD; confirmed without `source_artifact`; `message_origins()` empty | no |
| 3 | Awaiting SE review | INTERFACES_WITH with `status: candidate` | no |
| 4 | Traceability | `untested_requirements()`; TestCase `verifies` refs that aren't Requirement vertices (lesson 08) | no |
| 5 | Integrity | `check_baseline()` errors (lesson 07); baseline artifacts vs graph DesignArtifacts; listed-and-on-disk documents with 0 chunks; chunks for unlisted documents (`chunk_counts()`) | **yes** |
| 6 | Roll-up | `graph.ancestors()` from every subsystem: the recursive walk UP (lesson 04), summing required/present per System, Variant, Vehicle | no |

Every finding carries `critical: true` when it touches a safety-critical subsystem (⚠).

## Design choices

- **No embedding model.** Chunk counts come straight from the `<collection>_chunks` table
  (`pgvector_store.chunk_counts()`), so the report is fast and the Cockpit never loads torch.
- **Only integrity blocks.** Gaps are the normal state of a programme in progress; a broken load is not.
- **Overlap is deliberate.** A missing ICD shows in check 1 (the rule) and check 2 (the interface that has no
  document at its receiving end): same fact, two questions an SE asks.

## Try it

1. `python lessons/06_missing_document/run_test.py --only brake-std --keep`, then the report: check 1 loses the
   Brake ECU STD finding, check 4 drops from 16 to 12, check 6 moves Suspension/Steering to 100%... and check 5
   **blocks**, because STD-BRK-001's chunks are searchable but the committed baseline doesn't list it. That is the
   integrity check doing its job. Run lesson 06 again without `--keep` to restore.
2. Misspell an `os_class` value in the baseline: check 5 blocks with the ontology error.

`tests/test_health.py` covers the SYNTH-LTV findings and each way check 5 can block.
