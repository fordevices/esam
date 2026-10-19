# Lesson 01 — The synthetic corpus

Before any embeddings, look at what we're going to embed and *why it's shaped this way*.

## The two halves of an ESAM knowledge base

| | Where | What it holds | Who owns it | Searched by |
|---|---|---|---|---|
| **Structure** | `data/baseline/vehicle_decomposition.yaml` | Which parts exist, how they nest, how they connect | Systems Engineering (Git-controlled) | Graph traversal (lesson 04) |
| **Content** | `data/documents/*.md` | What the documents *say*: requirements, design rationale, interface details | Document authors | Vector similarity (lesson 03) |

The two meet at the **artifact id**. Each document file (`ICD-BRK-001.md`) is listed in the baseline
under `artifacts:` and `attached_to` a subsystem. Every chunk we embed carries that id, so a
search hit can always answer *"which part of the vehicle is this about?"*

## The vehicle, as a tree (all codes are real MIL-STD-881F Appendix G codes)

```
1.0      SYNTH-LTV Ground Vehicle System                       Level 1
└─1.1    Family of Vehicles                                     Level 2
  └─1.1.1  Lead Variant (Utility)                               Level 3 (per G.3 table)
     ├─1.1.1.5  Suspension/Steering        G.4.2.1.5a: "...brakes, and steering gears..."
     │   └─ SUB-BRK-ECU   Brake Control ECU (ABS)      RTOS   safety-critical, SwCI 1
     ├─1.1.1.6  Vehicle Electronics        G.4.2.1.6b,c: "data control and distribution",
     │   │                                             "controls and displays"
     │   ├─ SUB-J1939-BB  J1939 Vehicle Data Backbone
     │   └─ SUB-DCL       Driver Digital Cluster       Linux  not in any control path
     └─1.1.1.7  Power Package/Drive Train
         └─ SUB-ENG-ECM   Engine Control Module         (no documents. That matters later)
```

Everything below the 881F element (the `SUB-*` and `FN-*` ids, the documents) is invented.

## The interfaces (the edges)

```
SUB-BRK-ECU ──EBC1──► SUB-J1939-BB ──EBC1──► SUB-DCL          confirmed (ICD-BRK-001)
                                   └─EBC1──► SUB-ENG-ECM      CANDIDATE (nobody has checked)
```

EBC1 (*Electronic Brake Controller 1*, PGN 61441) is a real SAE J1939 message. The signal layout
in the ICD is simplified for teaching.

## Why RTOS on one side and Linux on the other

Both design docs state this explicitly, because it's the kind of rationale people later search for:

* **Brake ECU → RTOS.** The slip-control loop runs every 5 ms. If it's late, a wheel locks before
  pressure is released. That's a *hard* deadline, so the scheduler must have a provable worst
  case (`SDD-BRK-001` §1).
* **Cluster → Linux.** A late display frame is a cosmetic glitch. That's a *soft* deadline, so a
  general-purpose OS is fine and brings a graphics stack and developer pool. The one
  safety-relevant job, warning telltales, gets a `SCHED_FIFO` process and a pre-boot stage
  (`SDD-DCL-001` §2, §5).

## Seeded on purpose for later lessons

| Seed | Shows up in |
|---|---|
| The same fact in different words ("prevent wheel lock-up" / "anti-lock" / "ABS") | Lesson 02/03: meaning beats keywords |
| "lock" meaning brakes vs. a door lock | Lesson 02 step 4 |
| The 300 ms loss-of-EBC1 rule appears in the ICD, the cluster SDD and the cluster SRS | Lesson 03: several right answers, which one ranks first? |
| Engine ECM consumes EBC1 but has **no documents** and a **candidate** edge | Lesson 03 → 04: what vector search can't see, and the graph can |

## Do this

Open the YAML and one document side by side. Find the `ICD-BRK-001` entry under `artifacts:` and
follow `attached_to` back up the tree to `1.1.1.5`. That walk, from a document up to its WBS code,
is exactly what the code will do with metadata in lesson 03 and with graph queries in lesson 04.
