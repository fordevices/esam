# Lesson 05: Hybrid retrieval: vectors find the way in, the graph finds the reach

> **Renamed 2026-10-19:** documents now use MIL-STD-498 names (SSR→SRS, SSDD→SDD). IDs in the captured output below were updated to match; the scores were recorded under the old names and may differ slightly on a re-run.

```bash
# prerequisites: lesson 03 ingest.py and lesson 04 load_graph.py
python lessons/05_hybrid/hybrid.py "If we change the EBC1 message period, what is affected?"
python lessons/05_hybrid/hybrid.py --show-prompt "Why does the cluster run Linux?"
```

Lessons 03 and 04 each failed where the other succeeded:

| | Vector store | Graph |
|---|---|---|
| Understands what documents *say* | ✅ | ❌ |
| Knows what *connects* to what, in which direction | ❌ | ✅ |
| Sees a subsystem with no documents | ❌ | ✅ |

This lesson chains them. Real run:

```
1. VECTOR  top hit: ICD-BRK-001 chunk 7 (score 0.688)
2. LINK    ICD-BRK-001 belongs to SUB-BRK-ECU (Brake Control ECU (ABS))
3. GRAPH   impact from SUB-BRK-ECU on EBC1 (PGN 61441): ['SUB-J1939-BB', 'SUB-DCL', 'SUB-ENG-ECM?']
4. VECTOR  evidence from ['ICD-BRK-001', 'SDD-BRK-001', 'SRS-BRK-001', 'SDD-DCL-001', 'SRS-DCL-001']
```

## The five steps

| # | Store | What happens | Why |
|---|---|---|---|
| 1 | vector | Embed the question; best chunk = **entry point** | The question is free text; only similarity can map it onto the corpus |
| 2 | graph | `ancestors(artifact)` → owning subsystem | The `artifact_id` stored on every chunk (lesson 03) is the bridge |
| 3 | graph | `impact(subsystem, message)` | Multi-hop, direction-aware, includes undocumented subsystems, flags `candidate` edges |
| 4 | vector | For every document of every reached subsystem: the best chunk, **filtered to that document** | Evidence from each affected place, not just the top 4 overall |
| 5 | chat | Answer only from that evidence, with citation rules | The model writes; it doesn't decide what's true |

> **Since lesson 06** the pipeline lives in `esamkit/hybrid.py` and has a step **3b. COVER**: it checks
> in the graph whether the document type the question needs exists, and tells the model plainly when it
> doesn't. Output below predates that step; see lesson 06.

## Step 3's safeguard: start where the message *originates*

Impact flows **downstream** from the sender of a message. Vector search has no idea which end of an
interface a document describes. The cluster's design doc talks about EBC1 at length, and during
offline testing (with a crude stand-in embedder) step 1 landed there. The walk from the cluster
found nothing, because nothing is downstream of a receiver.

So when the question names a message, step 3 asks the graph for its **origin**: a subsystem that
sends it but doesn't receive it (`message_origins` in `postgres_cte.py`). If the entry point
isn't the origin, it re-anchors:

```
3. GRAPH   SUB-DCL isn't where EBC1 (PGN 61441) originates; re-anchoring at its origin: SUB-BRK-ECU
```

With the real BGE model, step 1 picks the brake ECU's own ICD, so no re-anchor is needed. The
safeguard stays, because which document wins step 1 depends on wording.

## What the chat model receives

Without `ESAM_CHAT_*` set, the script stops here and prints the prompt (dry run):

```
QUESTION: If we change the EBC1 message period, what is affected?

ENTRY POINT (from vector search): SUB-BRK-ECU via document [ICD-BRK-001] (score 0.688)
IMPACT ORIGIN (from graph): SUB-BRK-ECU

GRAPH: subsystems reachable over interfaces (depth = hops from the impact origin)
  - SUB-J1939-BB ...: depth 1, status confirmed, safety_critical=True,  documents: NO DOCUMENTS
  - SUB-DCL ...:      depth 2, status confirmed, safety_critical=False, documents: SDD-DCL-001, SRS-DCL-001
  - SUB-ENG-ECM ...:  depth 2, status candidate, safety_critical=False, documents: NO DOCUMENTS

EVIDENCE (most relevant chunk per document):
[ICD-BRK-001]  ...Any change to EBC1 content, period or source address is a Class I interface change...
[SRS-DCL-001]  ...SRS-DCL-021 ... if no EBC1 frame is received for 300 ms...
...
```

The system prompt's rules turn graph facts into honest wording: cite `[artifact ids]`, call
`candidate` impacts **unconfirmed**, and say plainly when a subsystem has **no documents** to
assess. A good answer here should point out something no single document says: that a slower
EBC1 period moves closer to the cluster's 300 ms loss-of-signal timeout (SRS-DCL-021).

## A real answer (GPT-5.1 via OpenAI, 2026-10-18)

Excerpts, unedited:

> **Driver Digital Cluster (SUB-DCL), confirmed impacted consumer**
> – It assumes that **loss of EBC1 for 300 ms** means anti-lock braking is unavailable and lights
>   the ABS telltale with a "BRAKE DATA LOST" message. [SDD-DCL-001; SRS-DCL-001, SRS-DCL-021]
> – If the EBC1 period were increased such that messages could legitimately arrive more than
>   300 ms apart, the cluster would incorrectly declare "BRAKE DATA LOST" and treat ABS as
>   unavailable. [SDD-DCL-001; SRS-DCL-001]
>
> **J1939 Vehicle Data Backbone (SUB-J1939-BB)**: there are **no documents provided** for
> SUB-J1939-BB. The specific impact on the backbone (e.g. bandwidth, timing assumptions)
> **cannot be assessed from the documents**.
>
> **Engine Control Module (SUB-ENG-ECM), candidate impact (UNCONFIRMED)**: any impact on the
> Engine Control Module is **UNCONFIRMED** and **cannot be assessed from the documents**. A systems
> engineering review with ECM interface documentation is recommended.

What made this answer possible, by source:

| Part of the answer | Came from |
|---|---|
| The four affected subsystems, their depth, the ECM being unconfirmed | **graph** (Q3 impact walk) |
| "No documents" for the backbone and the ECM | **graph** (empty `HAS_ARTIFACT`) |
| 100 ms period, 300 ms timeout, Class I change rule | **vector** (best chunk per document) |
| "A longer period trips the cluster's loss-of-signal logic" | **the model**, combining SRS-BRK-030 with SRS-DCL-021. No single document says it |

## Connecting a model

Point it at any OpenAI-compatible chat endpoint (on the pilot, the IL5 API Gateway):

```bash
# esam/.env
ESAM_CHAT_BASE_URL=https://<gateway>/v1
ESAM_CHAT_API_KEY=...
ESAM_CHAT_MODEL=<model name>
```

## Try this

1. `--show-prompt "Why does the cluster run Linux?"`: no message is named, the graph walk finds
   nothing downstream, and the evidence comes from the cluster's own documents only. Hybrid
   degrades gracefully into plain RAG.
2. Mark the ECM edge `confirmed` in the YAML, re-run `load_graph.py`, and watch the prompt change.
3. Add an `SDD-ECM-001.md` (and list it under `artifacts:`), re-ingest, and the "NO DOCUMENTS"
   line becomes evidence.
