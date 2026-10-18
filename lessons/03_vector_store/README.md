# Lesson 03: Vector store: chunk, embed, store, search

```bash
docker compose up -d --wait
python lessons/03_vector_store/ingest.py --reset
python lessons/03_vector_store/search.py --demo
python lessons/03_vector_store/search.py --artifact SSR-DCL-001 "telltale timing"
```

## The pipeline

```
document ──chunk──► 28 pieces ──embed──► 28 × 768 numbers ──upsert──► esam_documents_chunks
                         │
   baseline YAML ────────┘  each chunk tagged with artifact_id, wbs_code, artifact_type
```

Real ingest output:

```
  ✓ ICD-BRK-001.md     ICD-BRK-001   wbs 1.1.1.5   8 chunks
  ✓ SSDD-BRK-001.md    SSDD-BRK-001  wbs 1.1.1.5   6 chunks
  ✓ SSDD-DCL-001.md    SSDD-DCL-001  wbs 1.1.1.6   6 chunks
  ✓ SSR-BRK-001.md     SSR-BRK-001   wbs 1.1.1.5   4 chunks
  ✓ SSR-DCL-001.md     SSR-DCL-001   wbs 1.1.1.6   4 chunks
Ingested 28 chunks. Store now holds 28.
```

Run it again and every file reports `replaced N` with the same N. Chunk ids are deterministic
(`ICD-BRK-001::0003`), so re-ingesting replaces chunks rather than piling up duplicates.

## What's in Postgres

```sql
-- docker exec -it esam-postgres psql -U esam_user -d esam_pilot
\d esam_documents_chunks
   id          text  PRIMARY KEY          -- "ICD-BRK-001::0003"
   content     text                       -- the chunk, with "[doc title › section]" label
   source_file text
   artifact_id text                       -- bridge to the graph (lesson 04/05)
   metadata    jsonb                      -- wbs_code, artifact_type, title, ...
   embedding   vector(768)                -- the arrow
 Indexes: ..._hnsw  hnsw (embedding vector_cosine_ops)

SELECT * FROM esam_collections;
   esam_documents | BAAI/bge-base-en-v1.5 | 768    -- the model guard
```

A search is one SQL statement:

```sql
SELECT id, 1 - (embedding <=> :query_vector) AS score     -- <=> is cosine DISTANCE
FROM esam_documents_chunks
ORDER BY embedding <=> :query_vector                       -- nearest first
LIMIT 4;                                                   -- ORDER BY + LIMIT is what uses the HNSW index
```

## The demo questions (real results)

| Question | #1 hit | Why it's interesting |
|---|---|---|
| What does the driver see when anti-lock braking fails? | SSDD-BRK-001 §4 *Fail-safe behaviour* (0.679), then SSDD-DCL-001 §3 (0.658), ICD §4 (0.654) | The answer is spread over **three documents** from two subsystems. Vector search finds all three; none of them says "driver sees". |
| Why is Linux acceptable for the cluster but not for the brake controller? | SSDD-DCL-001 §1 *Why this unit runs Linux* (0.735), then SSDD-BRK-001 §1 *…runs an RTOS* (0.628) | Design **rationale**: the kind of thing people search for years later. Both halves of the comparison come back. |
| How does the display behave when the crew is using night vision goggles? | SSDD-DCL-001 §4 *Night operations* (0.668), then SSR-DCL-001 §3 (0.580) | The #2 requirement chunk never says "night vision", only "NVG-compatible" and "blackout", yet it ranks second. The model knows NVG ≈ night vision goggles. |
| If we change the EBC1 message period, which subsystems are affected? | ICD-BRK-001 §5 *Change control* (0.704): *"...requires review by every consuming subsystem **listed in the vehicle baseline**."* | See below. |

### The blind spot (question 4)

All four hits are *about* EBC1: the change-control rule, the timing, the message definition,
the transmit requirement. None of them is the answer. The best hit literally says the answer is
in the vehicle baseline: which subsystems consume EBC1 isn't stated in any one chunk. It's a
**relationship** spread over the baseline, and one consumer (the engine ECM) has no documents at
all, so it can never be a search result. That's lesson 04.

## Filters: search inside part of the corpus

```bash
python lessons/03_vector_store/search.py --artifact SSR-DCL-001 "telltale timing"
#1 score 0.455  SSR-DCL-001 chunk 2   (brake warning requirements: 200 ms / 300 ms)
```

Filters work on columns (`artifact_id`, `source_file`) and on any metadata key (`wbs_code`,
`artifact_type`). Lesson 05 uses this to fetch the best chunk from *each* affected document.

**A pgvector gotcha the code handles:** with an HNSW index, Postgres takes the index's ~40
nearest candidates *first* and applies the `WHERE` filter *after*. With a selective filter (one
document among thousands) that can return fewer than k rows, or none. pgvector 0.8 can keep
scanning until enough rows pass. `PgVectorStore` turns that on
(`SET hnsw.iterative_scan = strict_order`). The earlier script's IVFFlat index had a worse
version of this problem: built on an empty table, with `probes = 1`.

## The model guard

```
Collection 'esam_documents' was built with BAAI/bge-base-en-v1.5 (768-dim) but the current
embedder is BAAI/bge-small-en-v1.5 (384-dim). Vectors from different models cannot be compared...
```

That's what happens if someone changes `ESAM_EMBED_MODEL_ID` without re-ingesting. You get an
error, not quietly wrong answers.

## Take-aways

* Chunk so each vector is about one thing. Label chunks with their document and section.
* Store the link to the structure (`artifact_id`) with every chunk. It costs nothing and is what
  makes lesson 05 possible.
* Vector search finds what the documents **say**. It can't tell you what they **don't** say, or
  what connects to what.
