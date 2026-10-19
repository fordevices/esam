# Lesson 06: "That document doesn't exist yet"

A system you can trust has to say **"I can't answer that, the document doesn't exist"**, and then
answer correctly once the document arrives. This lesson tests exactly that, end to end, with the
real model.

```bash
python lessons/06_missing_document/run_test.py                 # both scenarios (~1 min)
python lessons/06_missing_document/run_test.py --only brake-std
python lessons/06_missing_document/run_test.py --keep          # leave the new documents loaded
```

The result of the latest run shows on the Cockpit Dashboard under **Checks**. Each run replaces the previous one.

## Why plain RAG fails this test

Ask *"How is SRS-BRK-011 verified? Give the test steps and pass criteria."* with no test description loaded.
Vector search never returns "nothing": it returns the **closest thing that exists**. Here that's the
requirement itself (score 0.80): *"release brake pressure on a wheel within 15 ms of detecting incipient
lock-up."* That reads a lot like an answer, and a model handed it can write a plausible test procedure
from it. Nothing in the vector store can tell it the test description is *missing*, because a missing
document has no vectors.

## The fix: step 3b, COVERAGE (in `esamkit/coverage.py`, used by `esamkit/hybrid.py`)

Before the model sees anything, the pipeline works out:

1. **Which document type would answer this?** A keyword table: test/verify → STD; message/interface → ICD;
   design → SSDD; requirement → SSR. It's crude on purpose; an ontology should replace it (notes B3).
2. **Which subsystem is it about?** The one the question names ("Engine Control Module"), else the one
   the vector search landed on.
3. **Does the graph say that document exists?** If not, the prompt gets a plain line:
   `MISSING: SUB-BRK-ECU has no test description (STD). That document does not exist in the baseline yet.`
   The system prompt then requires the model to say so first and fill nothing in.

Steps 1–3 are deterministic: the graph decides what's missing, not the model.

## The two scenarios (`scenarios.py`, documents in `staged/`)

| Scenario | Question | Missing before | Ingested |
|---|---|---|---|
| `brake-std` | How is SRS-BRK-011 verified? Test steps and pass criteria? | Brake ECU has no **STD** (it's safety-critical) | `STD-BRK-001` (4 test cases, each with a `Verifies:` line) |
| `engine-icd` | Does the Engine Control Module use EBC1, and for what? | Engine ECM has **no documents**; its EBC1 link is only a *candidate* | `ICD-ENG-001` (ECM consumes EBC1 for traction-control torque reduction and to disable the engine brake during ABS) |

"Ingesting" does what an SE would do: add the artifact to the baseline and load the file. The committed
baseline YAML is never edited; the run loads a copy with the extra entry (`var/lesson06/`). Afterwards both
stores go back to the committed baseline, so the Dashboard keeps showing the real gaps (unless `--keep`).

## The checks

| Kind | Before | After |
|---|---|---|
| **deterministic** (the guarantee) | graph reports the document missing; prompt carries the MISSING line | graph has the document; it's retrieved as evidence |
| **answer** (text heuristics) | says it's missing; doesn't cite the new id; invents nothing (no made-up test case ids / "compression brake") | cites the new document (and test case `TC-BRK-011`) |

The answer checks are simple text rules, useful but not proof. `tests/test_confidence.py` runs both
scenarios through the deterministic checks without a model, so they're covered by `pytest`.

## Real output (gpt-5.1, 2026-10-19), abridged

```
Brake ECU test description (STD)  [brake-std]
  ── BEFORE (document does not exist)
  1. VECTOR  top hit: SSR-BRK-001 chunk 1 (score 0.803)
  3b. COVER  needs ['std'] for ['SUB-BRK-ECU']: MISSING SUB-BRK-ECU:STD
  ANSWER: This question cannot be answered as requested because the Brake ECU test description (STD)
    for subsystem SUB-BRK-ECU does not exist in the baseline yet. [...] SRS-BRK-011 is defined as: "The
    software shall release brake pressure on a wheel within 15 ms [...]" [SSR-BRK-001] [...] until that
    document exists, any detailed test sequence or thresholds would be an invention and must not be assumed.
  ── INGEST: STD-BRK-001 attached to SUB-BRK-ECU, 6 chunks
  ── AFTER
  1. VECTOR  top hit: STD-BRK-001 chunk 3 (score 0.886)
  3b. COVER  needs ['std'] for ['SUB-BRK-ECU']: all present
  ANSWER: The requirement SRS-BRK-011 is verified by test case TC-BRK-011 [...] [STD-BRK-001]
    1. Run the vehicle model at 40 km/h on a simulated 0.2 friction (ice) surface. [...]
    Pass criteria: Every one of the 200 releases (50 per wheel × 4 wheels) completes within 15 ms. [...]
  PASS: brake-std

Engine ECM interface (ICD)  [engine-icd]
  ── BEFORE
  3. GRAPH   impact from SUB-BRK-ECU on EBC1 (PGN 61441): ['SUB-J1939-BB', 'SUB-DCL', 'SUB-ENG-ECM?']
  3b. COVER  needs ['icd'] for ['SUB-ENG-ECM']: MISSING SUB-ENG-ECM:ICD
  ANSWER: The question cannot be fully answered because the Engine Control Module (SUB-ENG-ECM) has no
    Interface Control Document (ICD) in the baseline yet. [...] It is unknown from the current document
    set whether the Engine Control Module uses the EBC1 brake message at all. [...] marked as a
    "candidate" impact, so any assumption [...] is unconfirmed and requires systems engineering review.
  ── INGEST: ICD-ENG-001 attached to SUB-ENG-ECM, 5 chunks
  ── AFTER
  1. VECTOR  top hit: ICD-ENG-001 chunk 2 (score 0.817)
  3. GRAPH   SUB-ENG-ECM isn't where EBC1 (PGN 61441) originates; re-anchoring at its origin: SUB-BRK-ECU
  ANSWER: The Engine Control Module does use the EBC1 brake message. [...] "ASR engine control active"
    [...] reduces engine torque [...] "ABS active" [...] disables the engine compression brake [...]
    [ICD-ENG-001]. Note: The linkage [...] is marked as a candidate impact [...] reviewed by systems engineering.
  PASS: engine-icd

PASS: 2/2 scenarios. Stores restored to the committed baseline. Result saved for the Cockpit.
```

Two things to notice:
- **After** the engine ICD arrives, the vector search lands on the *consumer's* document, and step 3's
  safeguard (lesson 05) re-anchors the impact walk at the message's origin.
- The model still calls the ECM link **candidate** after reading a document that confirms it. That's right:
  the document is now *evidence* for confirming the link, but changing `candidate` → `confirmed` is an SE
  decision in the baseline, not something retrieval does.
