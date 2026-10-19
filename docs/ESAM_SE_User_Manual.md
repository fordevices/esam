# ESAM User Manual for Systems Engineers

**Audience:** Systems Engineers (SEs) and SE leads who own the vehicle baseline and the documents ESAM reads.
**You don't need to know:** Python, databases or AI internals. Where a technical idea matters to your work, this manual explains it in SE terms.
**Status:** living document, grown one topic at a time. Examples use a synthetic vehicle, SYNTH-LTV (a Brake Control ECU and a Driver Digital Cluster linked by a J1939 CAN backbone); it is not a real programme.

| § | Topic |
|---|---|
| 1 | [Creating and maintaining the YAML baseline](#1-creating-and-maintaining-the-yaml-baseline) |
| 2 | [How ESAM reads your documents: chunks and rows](#2-how-esam-reads-your-documents-chunks-and-rows) |
| 3 | [Vectors: what the 768 numbers are](#3-vectors-what-the-768-numbers-are) |
| 4 | [How ESAM walks the baseline (recursive CTEs)](#4-how-esam-walks-the-baseline-recursive-ctes) |
| 5 | [Finding gaps after ingestion](#5-finding-gaps-after-ingestion) |
| 6 | [When to move to a dedicated graph database](#6-when-to-move-to-a-dedicated-graph-database) |
| 7 | [When ESAM says "that document doesn't exist yet"](#7-when-esam-says-that-document-doesnt-exist-yet) |
| 8 | [The ESAM ontology: the programme's vocabulary](#8-the-esam-ontology-the-programmes-vocabulary) |
| 9 | [Where attributes go, and writing documents ESAM can read](#9-where-attributes-go-and-writing-documents-esam-can-read) |
| 10 | [Requirements and tests in the graph (traceability)](#10-requirements-and-tests-in-the-graph-traceability) |

---

## 1. Creating and maintaining the YAML baseline

### 1.1 What the baseline is

The YAML baseline is the **authoritative decomposition of the vehicle**: which systems and subsystems exist, how they nest, how they connect and which documents describe each one. ESAM builds its graph from this file. When ESAM says "the Brake ECU is part of Suspension/Steering, which is part of the Lead Variant," it is reading your baseline.

It is a **Systems Engineering artefact, not an IT configuration file.** The SE team owns it, edits it and keeps it under version control in Git (ESAM Pilot Implementation §4.3). Nothing an AI tool produces goes into the baseline until an SE has reviewed and approved it.


### 1.2 Where the first version comes from

You rarely start from a blank page. There are three practical sources, and most programmes use a mix of them.

| Source | What it gives you | How it gets into YAML | Trust level |
|---|---|---|---|
| **A. The programme's contract WBS** *(start here)* | Systems and subsystems, WBS codes, names, nesting (MIL-STD-881F Levels 1–3, often 4) | Export from wherever the WBS lives (Excel, MS Project, cost tool) and run a converter script | High: the WBS is already contractually reviewed |
| **B. The MBSE model** (e.g. Cameo / SysML), if one exists | Block hierarchy, ports, interfaces between blocks | Export the model's blocks and connectors and run a converter | Medium: only as good as how current the model is. Check first. |
| **C. AI-drafted proposals from documents** | Functions, messages, interfaces and document-to-subsystem links that no WBS carries | ESAM reads your design documents and ICDs and **proposes** additions | Low until reviewed: every proposal is marked `candidate` |

**Rule:** sources A and B give you the skeleton. Source C helps fill in detail. **You** decide what becomes `confirmed`.

### 1.3 The workflow, step by step

| Step | Who | What happens |
|---|---|---|
| 1 | SE + converter script | Export the contract WBS and convert it into a skeleton YAML (the tree of systems and subsystems with WBS codes). |
| 2 | SE | Add the attributes a WBS doesn't carry, such as `safety_critical`, `safety_classification`, `os_class`, functions, interfaces, and which document file belongs to which node. AI-drafted proposals (source C) can be reviewed here. |
| 3 | Automatic check | A schema check catches errors before commit: malformed WBS codes, duplicate IDs, references to nodes that don't exist, documents listed but missing. |
| 4 | SE | Commit to Git with a meaningful message ("Add EBC1 consumer: Engine ECM"). Git gives you the full change history and who made each change. |
| 5 | IT/SW (or automatic) | The ingestion utility reads the YAML and updates the graph database. |
| 6 | SE | Review the **Diff Matrix** in the SE Cockpit (exactly which nodes and links will change), then press **Approve and Lock Baseline**. |

### 1.4 What goes in each entry

A minimal subsystem entry, from the SYNTH-LTV example baseline:

```yaml
- id: SUB-BRK-ECU                          # unique, stable ID. Never reuse or rename
  name: "Brake Control ECU (ABS)"
  safety_critical: true                    # SE attribute, not in the WBS
  safety_classification: "MIL-STD-882E SwCI 1"
  os_class: rtos                           # rtos | gpos
  functions:
    - id: FN-BRK-STATUS
      name: "Brake status broadcast"
      description: "Publishes ABS/brake state on the J1939 backbone (EBC1)."
```

Good habits:
- **IDs are permanent.** Rename the `name`, never the `id`. Documents, links and history all point at the ID.
- **Use the real WBS code** from the contract WBS for anything at MIL-STD-881F Level 3 or above. Don't invent codes for lower levels; ESAM works them out from where the node sits in the tree.
- **Write a one-sentence `description`** for functions and any node you add yourself. ESAM uses descriptions to match incoming documents to the right node.
- **Link every document explicitly** (`file:` under `artifacts:`). Don't rely on file names to imply which subsystem a document belongs to.

### 1.5 Tooling status

The WBS-to-YAML converter (step 1) and the automatic schema check (step 3) are planned pilot tooling and not yet available. Until they are, the baseline is written by hand.

---

## 2. How ESAM reads your documents: chunks and rows

### 2.1 Why documents are cut into chunks

ESAM can't usefully search a whole document as one unit. A 1,000-page SSDD is "about" everything, so as a single search entry it matches nothing in particular. Instead, ESAM cuts each document into **chunks** (short passages of roughly one paragraph to half a page) and searches those.

Think of it as a **card index**. Every chunk becomes one index card holding:
- the **text excerpt**,
- a **label** saying which document and section it came from, and
- a **fingerprint** (the *vector*, see §3) that lets ESAM find cards with similar meaning, even when the wording differs.

### 2.2 One chunk = one row

Each chunk is stored as **exactly one row** in the database, and a row is never split further. A document cut into 100 chunks becomes 100 rows. In the SYNTH-LTV example, five short documents became 28 rows:

| Document | Rows |
|---|---|
| ICD-BRK-001 | 8 |
| SDD-BRK-001 | 6 |
| SDD-DCL-001 | 6 |
| SRS-BRK-001 | 4 |
| SRS-DCL-001 | 4 |

What one row holds:

| Field | Example | Purpose |
|---|---|---|
| `id` | `ICD-BRK-001::0002` | Unique key: document plus chunk number |
| `chunk_index` | `2` | Position in the document, so chunks can be put back in order |
| `artifact_id` | `ICD-BRK-001` | Which document it came from. This is the link to the graph (your baseline). |
| `content` | `[ICD-BRK-001 — … › 2. …]` + text | The words, with the section label in front |
| `metadata` | tags | Extra labels that searches can filter on |
| `embedding` | `[-0.0417, -0.0059, …]` | The fingerprint: one list of numbers (§3) |

**Loading a new revision replaces the old one.** ESAM deletes every row for a document before loading the new version, so Rev B never mixes with Rev A.

### 2.3 What happens to a large Word document (SSDD, System Test Description)

**Scale, for a 1,000-page document:**

| | Estimate |
|---|---|
| Text | ~500,000 words (~3 million characters) |
| Chunks (rows) | ~2,500–5,000 |
| Time to process on the CPU-only pilot VM | roughly 5–15 minutes, once per revision |
| Database space | ~15–25 MB |

Size is not the problem. **How cleanly the document splits is.**

**The steps:**

| Step | What ESAM does | Why |
|---|---|---|
| Convert | Reads the .docx and turns Word heading styles (Heading 1/2/3) into a section tree | The section tree is how every chunk knows where it sits |
| Clean | Drops the table of contents, revision history, lists of figures, and repeated headers and footers; accepts tracked changes | Otherwise the table of contents matches every search |
| Split on structure | Cuts at section numbers first, then at **requirement IDs** (SRS, SDD) or **test case IDs** (test documents); only splits by length inside a long section | One chunk per requirement or test case is far more useful than an arbitrary slice |
| Label | Prefixes every chunk with its full path, e.g. `SDD-BRK-001 › 3.2 Wheel-slip control › 3.2.4 Pressure modulation` | On page 600, "the rate shall be 100 ms" means nothing without its label |
| Tag | Records the IDs a chunk mentions, e.g. `verifies: SRS-BRK-012` | These become links in the graph: this test *verifies* that requirement |

**Requirements and design documents (SRS, SDD, SSDD)** are mostly prose and requirements. They split by section, then by requirement.

**Test documents (STD/STP)** are mostly tables. ESAM keeps **one test case per chunk** where possible: ID, objective, requirements verified, setup, steps and expected results together. A very long step table is split by step, and every piece repeats the test case ID and the table header. Table rows are rewritten as sentences ("Step 4: Apply 50% pedal → Expected: EBC1 ABS-active flag = 1") because search models read sentences better than grids.

### 2.4 How to write documents that chunk well

| Do | Avoid |
|---|---|
| Use real Word **heading styles** for every section | Bold or enlarged text used as fake headings |
| Number sections consistently (`3.2.4`) | Unnumbered or hand-renumbered sections |
| Write requirement and test IDs in one consistent format (`SRS-BRK-012`) | Mixed formats (`SRS BRK 12`, `Req 12`) |
| Give each test case a fixed **"Verifies:"** field | Traceability buried in free text |
| Put each requirement in its own paragraph | Several "shall" statements in one paragraph |
| Keep tables as real Word tables | Tables pasted in as images |

### 2.5 The two filing rules in plain English

ESAM files your documents like a good records clerk would, by two rules.

**Rule 1: one topic per index card.** A card that mixes brake timing, display fonts and power supply is hard to find and only partly relevant when found. A card about one thing is easy to find and easy to trust. **In SE terms, one chunk should cover one requirement, one test case, one message definition or one design decision.** That's why ESAM splits on requirement and test case IDs instead of every *N* characters.

**Rule 2: every card says where it came from.** On its own, *"The message shall be transmitted every 100 ms"* is useless: which message, which system? Labelled **"ICD-BRK-001 › 2. EBC1 message definition,"** it becomes a usable fact. The label lets ESAM **find** the chunk (a question about the brake ECU matches even though the sentence never says "brake"), lets you **trust** the answer (it can cite "per ICD-BRK-001 §2") and **links** the chunk to the right node in your baseline.

> *One-line version:* ESAM files your documents as index cards, one topic per card and each card stamped with its document and section, so it can find the right fact and always show you where it came from.

### 2.6 Tooling status

ESAM currently reads Markdown documents. Word (.docx) conversion, the cleaning step, splitting at requirement and test case IDs, and ID tagging are planned but not yet available.

---

## 3. Vectors: what the 768 numbers are

**How vectors work.** Every chunk (and every question you ask) is turned into one list of numbers called a **vector**. Latitude and longitude are two numbers that place a city on a map; a vector is a few hundred numbers that place a passage of text on a "map of meaning." Passages that mean similar things land close together even when the wording differs, so "ABS status broadcast" sits near "EBC1 brake message." A search turns your question into a vector and returns the chunks whose vectors are closest. The **score** (1.0 means the same meaning, near 0 means unrelated) measures that closeness.

**What the numbers mean.** Individually, nothing you can read: no single number stands for "brakes" or "timing." Meaning comes only from comparing one whole list with another.

**The number depends on the model.** The length of the list is fixed by the embedding model, not chosen by us. ESAM uses `bge-base-en-v1.5`, which produces **768** numbers per chunk, so the pilot database column is declared as 768-dim. Other models produce 384, 1,024, 1,536 or 3,072 numbers. Consequences:
- **Vectors from different models can't be compared**, even if they happen to be the same length. Each model draws its own map.
- **Changing the model means re-processing every document**, and if the length changes, the database column changes too. ESAM refuses to mix vectors from different models.
- **Storage is small:** 768 numbers ≈ 3 KB per chunk, so ~15 MB for a 5,000-chunk document.

---

## 4. How ESAM walks the baseline (recursive CTEs)

Your baseline is a tree (vehicle → family → variant → system → subsystem → documents) with interfaces running across it. To answer questions like *"where does this document sit?"* or *"what's affected if this changes?"*, ESAM has to **walk** that structure. In the pilot it does this with a database technique called a **recursive CTE**.

### 4.1 What the term means

- **CTE (Common Table Expression)** is a named, temporary result inside one database query. It works like a scratch-pad table that exists only while the question is being answered.
- **Recursive** means the scratch-pad builds on itself: start somewhere, take one step, then take another step from wherever you landed, and repeat until there's nowhere new to go.

**Analogy: walking an org chart, or a WBS.** "Who is in this division?" means: find the division, then everyone who reports to it, then everyone who reports to them, and so on until nobody new turns up. You don't need to know in advance whether that's 2 levels or 7.

### 4.2 Why ESAM needs it

An ordinary database query follows a **fixed** number of links ("go to the parent, then the grandparent"). A vehicle decomposition has no fixed depth: one document may sit three levels down, another six. A recursive CTE follows **however many levels there are**. This is what lets the pilot use a standard PostgreSQL database as a graph, without a separate graph database product.

### 4.3 What one looks like

The question *"Where does ICD-BRK-001 sit in the vehicle?"* is written as:

```sql
WITH RECURSIVE up AS (
    -- 1. START: the document
    SELECT ... WHERE id = 'ICD-BRK-001'
  UNION ALL
    -- 2. STEP: from whatever has been found, follow one link upward
    --    (document → its owning subsystem, subsystem → its parent, ...)
    SELECT parent ... FROM up JOIN graph_edges ...
    -- 3. SAFETY: never revisit something already on the path (prevents endless loops)
    WHERE NOT parent = ANY(path)
)
SELECT depth, id, name, wbs FROM up;
```

The answer, from the example baseline:

```
 depth | id          | name                                       | wbs
-------+-------------+--------------------------------------------+---------
     0 | ICD-BRK-001 | Brake ECU J1939 Interface Control Document |
     1 | SUB-BRK-ECU | Brake Control ECU (ABS)                    |
     2 | SYS-SUSP    | Suspension/Steering                        | 1.1.1.5
     3 | VAR-001     | Lead Variant (Utility)                     | 1.1.1
     4 | FAM-001     | SYNTH-LTV Family of Vehicles               | 1.1
     5 | V-001       | SYNTH-LTV Ground Vehicle System            | 1.0
```

One question returned five levels of answer, and nobody had to say how many levels there were.

### 4.4 The three parts, and the three kinds of question

Every recursive CTE has the same three parts:

| Part | Meaning |
|---|---|
| **Start** | The node the question is about |
| **Step** | Which kind of link to follow (part-of, has-document, sends-message, ...) |
| **Safety** | Don't revisit nodes, so a loop in the data can't run forever |

ESAM uses them in three directions:

| Question | Direction | Example |
|---|---|---|
| "What's under this system?" | Down the tree | Everything inside Suspension/Steering |
| "Where does this sit?" | Up the tree | The ICD-BRK-001 example above |
| "If this changes, who's affected?" | Across interfaces | Brake ECU changes the EBC1 message, so the Driver Cluster and the Engine ECM are affected |

### 4.5 The same idea in graph database languages

The full deployment targets JanusGraph, which uses the **Gremlin** language. **Cypher** is the language of Neo4j, which is shown for comparison only (it's not approved for this programme). All three express the same "start, step, repeat":

| Language | "Where does ICD-BRK-001 sit?" |
|---|---|
| Recursive CTE (PostgreSQL, the pilot) | ~10 lines, as in §4.3 |
| Gremlin (JanusGraph, full deployment) | `g.V().has('id','ICD-BRK-001').in('HAS_ARTIFACT').emit().repeat(out('PART_OF')).path()` |
| Cypher (Neo4j, comparison only) | `MATCH (d {id:'ICD-BRK-001'})<-[:HAS_ARTIFACT]-(s)-[:PART_OF*0..]->(p) RETURN p` |

Graph languages are shorter to write. The answers are the same.

---

## 5. Finding gaps after ingestion

Searching documents can only find what exists. **A missing document has no text, so no search can find it.** The baseline graph knows what *should* exist, which is what lets ESAM report what doesn't.

### 5.1 An example finding

"Which subsystems have no document of each type?" on the SYNTH-LTV example:

| Missing type | Subsystems without one |
|---|---|
| ICD | Driver Cluster, **J1939 Backbone (safety-critical)**, Engine ECM |
| SDD | **J1939 Backbone (safety-critical)**, Engine ECM |
| SRS | **J1939 Backbone (safety-critical)**, Engine ECM |
| STD (test description) | **Brake ECU (safety-critical)**, Driver Cluster, **J1939 Backbone (safety-critical)**, Engine ECM |

The key finding: **the safety-critical Brake ECU has no test description.**

### 5.2 The baseline health report

ESAM checks the baseline, its documents and the graph, and shows the result at the top of the SE Cockpit Dashboard. It runs again every time the page loads, and a one-line summary is printed at the end of every document ingest and graph load. You see both *what is changing* (the Diff Matrix) and *what is still missing* before you approve.

| # | Check | What it finds (SYNTH-LTV today) | Effect |
|---|---|---|---|
| 1 | **Required documents**, from the ontology's rules (§8.4) | Brake ECU has no STD; the J1939 backbone, the cluster and the engine ECM have no ICD | Reported |
| 2 | **Interfaces** | A subsystem receives a message but has no interface document saying how it uses it; a confirmed link that cites no interface document; a message nobody originates | Reported |
| 3 | **Awaiting SE review** | Candidate links: "EBC1 to the engine ECM is a candidate" | Reported |
| 4 | **Traceability** (§10) | Requirements no test case verifies (16 today); a test case whose `Verifies:` line names a requirement that doesn't exist | Reported |
| 5 | **Integrity** | The baseline breaks the ontology; a listed document file is missing; the graph is out of step with the baseline; a document has no searchable text, or searchable text exists for a document the baseline no longer lists | **Blocks approval** |
| 6 | **Roll-up**, walking *up* the tree (§4) | Suspension/Steering 75%, Vehicle Electronics 50%, Power Pack/Drive Train 0%, the whole vehicle 56% of required documents | Reported |

Gaps are normal mid-programme, so checks 1–4 and 6 inform you and never block. Check 5 means the load itself is broken (what ESAM searches no longer matches what the baseline says), so it blocks approval until fixed. Findings on safety-critical items are marked ⚠.

**The rules are yours.** Which document types each kind of subsystem needs is SE policy, kept in the ontology (§8.4) and versioned in Git like the baseline. Change a rule and checks 1 and 6 change with it.

### 5.3 What to do with the findings

| Finding | Usual action |
|---|---|
| Required document missing | Plan the document, or, if the rule doesn't fit this subsystem, change the rule |
| Interface without a document at the receiving end | Get the receiving subsystem's ICD written, or confirm the link isn't real and remove it |
| Candidate link | Review it: confirm it (cite the interface document) or remove it |
| Untested requirement | Add a test case with a `Verifies:` line, or record why the requirement is verified another way |
| Integrity problem | Re-run the ingest or the graph load, or fix the baseline; then check again before approving |

---

## 6. When to move to a dedicated graph database

The pilot stores the baseline graph inside PostgreSQL and walks it with recursive CTEs (§4). The full deployment targets **JanusGraph**, a dedicated graph database. This section explains what should drive that move, so that SEs and PMs can recognise the signals.

### 6.1 Size is rarely the trigger

A full vehicle programme is not a large graph by database standards:

| Content | Rough scale for one programme |
|---|---|
| Systems, subsystems, functions | thousands |
| Requirements, tests, documents | ~100,000 |
| Links (part-of, traces-to, verifies, interfaces) | ~1 million |

PostgreSQL handles this comfortably for ESAM's everyday questions. These figures are rules of thumb; the pilot will measure real numbers (§6.4).

### 6.2 The real signals

| Signal | What you'd experience |
|---|---|
| **Deep "all paths" impact questions** | "Every path by which a change to EBC1 reaches any safety-critical function" slows from instant to many seconds as interfaces grow |
| **Graph analysis** | You want shortest paths, the most-connected subsystems, clusters or cycle detection across the whole baseline, and ESAM can't answer without a developer exporting data |
| **New questions are slow to add** | Each new kind of question takes developers days, not hours |
| **You want to explore the graph yourself** | Visual, click-through exploration and writing your own queries without a developer |
| **Many programmes or many users at once** | One system serving several programmes, with performance that hardware upgrades no longer fix |

In practice, the people-side signals (slow to add questions, SEs wanting self-service) usually arrive first. Size usually arrives last.

### 6.3 What moving costs

- **Two databases to keep in step.** Document search stays in PostgreSQL while the graph moves out, and the two can disagree if an update half-fails. Today one update covers both.
- **Not automatically faster.** JanusGraph is built to spread very large graphs over many servers. On a graph of programme size, PostgreSQL is often as fast.
- **More to operate and accredit.** A new product in the IL5 enclave means a new approval footprint and new skills.

A possible middle step is **Apache AGE**, a PostgreSQL extension that adds a graph query language while keeping a single database. It would need its own approval and evaluation.

### 6.4 The plan

1. **Stay on PostgreSQL for the pilot.** It fits the IL5 constraints, keeps one database, and the data is small.
2. **Measure during the pilot.** Record how long each kind of question takes on real programme data.
3. **Agree the trigger in advance**, for example: *"if a typical impact question takes longer than about 2 seconds, or SEs need graph analysis they will actually use, we begin the move to JanusGraph."*
4. **The move is designed to be contained.** ESAM's graph questions all go through one internal interface, so changing the database doesn't change how you use ESAM.

---

## 7. When ESAM says "that document doesn't exist yet"

### 7.1 Why this matters

A document search always returns *something*: the closest passage that exists. Ask "How is SRS-BRK-011 verified?" when no test description has been written, and the closest passage is the requirement itself. It mentions 15 ms and wheel lock-up, so it looks like an answer, and an AI model given it could write a plausible test procedure that nobody ever approved.

ESAM prevents this by checking the **baseline** before it answers. The baseline knows which documents each subsystem has, so ESAM can tell the difference between "the documents don't say" and "the document doesn't exist."

### 7.2 What ESAM does with each question

1. **Works out which document type would answer it.** Questions about testing or verification need a test description (STD); questions about messages or interfaces need an ICD; design questions need an SDD; requirement questions need an SRS.
2. **Works out which subsystem it's about**: the one you name, or else the one whose documents best match the question.
3. **Checks the baseline.** If that subsystem has no document of that type, ESAM tells you first and plainly, then reports only what the existing documents say, without filling the gap.

### 7.3 What it looks like

Asked how SRS-BRK-011 is verified, before a test description exists:

> *This question cannot be answered as requested because the Brake ECU test description (STD) for subsystem SUB-BRK-ECU does not exist in the baseline yet. … SRS-BRK-011 is defined as: "The software shall release brake pressure on a wheel within 15 ms of detecting incipient lock-up on that wheel." [SRS-BRK-001] … until that document exists, any detailed test sequence or thresholds would be an invention and must not be assumed.*

The same question after STD-BRK-001 is added to the baseline and loaded:

> *The requirement SRS-BRK-011 is verified by test case TC-BRK-011 … [STD-BRK-001] … Pass criteria: every one of the 200 releases (50 per wheel × 4 wheels) completes within 15 ms …*

### 7.4 The confidence test

ESAM runs this exact sequence as an automated test: ask, confirm it reports the gap, add the document, ask again, confirm the answer now cites it. It is checked in two ways:

| Check | What it proves |
|---|---|
| **Baseline checks** | The gap is detected from the baseline, not guessed by the AI. These are the guarantee. |
| **Answer checks** | The AI's wording reports the gap, invents nothing, and cites the new document once it exists. These are simple text rules, a useful signal rather than proof. |

The SE Cockpit Dashboard shows the latest run under **Checks**: date, pass or fail, and the before and after answers.

### 7.5 What this means for you

- **"Doesn't exist yet" is a finding, not a failure.** It usually points at the same gap the Dashboard's document coverage table shows in red.
- **A new document doesn't confirm a link by itself.** When an ICD shows that a subsystem really does use a message, ESAM will cite it, but still calls a *candidate* interface unconfirmed. Changing it to confirmed is your decision, made in the baseline.
- **ESAM recognises document types and subsystems through the ontology** (§8): each document type's words and synonyms, and each subsystem's name and aliases. If people use a term ESAM misses, add it to the ontology or the baseline `aliases`; no code change is needed.

---

## 8. The ESAM ontology: the programme's vocabulary

### 8.1 What it is

The baseline (§1) says **what this vehicle is made of**. The ontology says **what kinds of things, links, values and documents are allowed to exist at all**, and what each kind of subsystem must have. It is the programme's agreed vocabulary, written down so a machine can check it.

Like the baseline, it is a YAML file owned by the SE team and kept in Git (`data/ontology/esam_ontology.yaml`). It builds on the standards the programme already uses:

| Standard | What the ontology takes from it |
|---|---|
| MIL-STD-881F Appendix G | The WBS levels (Vehicle, Family, Variant, System) and their codes |
| MIL-STD-498 / J-STD-016 | Document type names: SRS, SDD, SSDD, STD, STP, STR |
| MIL-STD-882E | Software criticality index, SwCI 1 to 5 |
| SAE J1939 | Message names and PGNs on interfaces |

### 8.2 What it contains

| Part | Example |
|---|---|
| **Kinds of things** (classes), each with an ID form | Subsystem IDs look like `SUB-BRK-ECU`; document IDs like `SRS-BRK-001` |
| **Kinds of links** | *is part of*, *has document*, *interfaces with*; planned: *verifies*, *specified in*, *allocated to* |
| **Allowed values** | `os_class`: `rtos` or `gpos`. Interface status: `candidate` or `confirmed`. Verification method: I, A, D or T |
| **Document types**, with standard names and synonyms | **STD** = Software Test Description; also known as "test description", "test procedure" |
| **Placement**: where each kind of fact belongs | See §9 |
| **Rules**: what each kind of subsystem must have | See §8.4 |

### 8.3 Document names follow MIL-STD-498

The example documents were originally named SSR (requirements) and SSDD (software design). Neither matches the standard: MIL-STD-498 calls a software requirements document an **SRS** and a software design document an **SDD**, and uses **SSDD** for a different document, the *System/Subsystem* Design Description. The documents were renamed to the standard names. The old names are kept in the ontology as **synonyms**, so a question that says "SSR" still finds the SRS, but a baseline that lists a document as type `ssr` is rejected.

"ICD" is not a MIL-STD-498 name. It is kept as the programme's term, and the ontology records that it covers the standard's IRS (Interface Requirements Specification) and IDD (Interface Design Description).

### 8.4 Rules: what each subsystem must have

Instead of checking every subsystem for the same list of documents, the ontology states rules. A subsystem must have every document required by every rule that applies to it:

| When the subsystem… | It must have | Why |
|---|---|---|
| is software (has an `os_class`) | SRS, SDD | Software is specified and designed |
| is software **and** safety-critical | STD | Safety-critical software needs a documented test description |
| has any interface | ICD | Every interface is defined in a controlled document |

For SYNTH-LTV, these rules give:

| Subsystem | Must have | Has | Missing |
|---|---|---|---|
| Brake ECU (software, safety-critical, interfaces) | SRS, SDD, STD, ICD | SRS, SDD, ICD | **STD** |
| J1939 backbone (safety-critical, interfaces, not software) | ICD | none | **ICD** |
| Driver cluster (software, interfaces) | SRS, SDD, ICD | SRS, SDD | ICD |
| Engine ECM (interfaces) | ICD | none | ICD |

That's 9 required documents, 5 present and 4 missing, 2 of them on safety-critical subsystems. A fixed checklist of four types for every subsystem would report 16 required and 11 missing, most of them documents nobody needs (an SDD for a CAN backbone).

The rules are SE policy. Change them in the ontology and every check that uses them changes with it.

### 8.5 Checking the baseline against the ontology

ESAM checks the baseline against the ontology and reports:

| Error (must fix) | Warning (probably a mistake) |
|---|---|
| A value outside the vocabulary (`os_class: hard-real-time`) | An attribute the ontology doesn't know (`safty_critical`), which would otherwise be silently ignored |
| An ID in the wrong form, or used twice | A confirmed interface that cites no interface document |
| A WBS code that doesn't sit under its parent's | A document file in the folder that the baseline doesn't list |
| A document whose ID and type disagree (`SRS-…` listed as type `ssr`) | |
| A document listed in the baseline whose file is missing | |

The SYNTH-LTV baseline currently passes with no errors and no warnings.

### 8.6 What uses the ontology today

| Where | What it takes from the ontology |
|---|---|
| **Baseline check** | Everything: classes, ID forms, allowed values, document types |
| **SE Cockpit Ontology tab** | Shows the whole ontology, the path to the file, and whether the baseline passes it. If the file has been edited since the Cockpit started, it says so: restart the Cockpit to apply the changes |
| **SE Cockpit Dashboard** | The rules: the document coverage table shows what each subsystem *must* have, marks the rest "not required", and lists the rules under the table |
| **Answering questions** (§7) | Document-type words and synonyms: "test procedure" means an STD, "IRS" means an ICD, the old "SSR" still means an SRS |
| **Recognising subsystems in questions** | Each subsystem's `aliases` in the baseline ("ECM", "brake ECU"), declared in the ontology |

| **Requirements and test cases** (§10) | The `Requirement` and `TestCase` classes and the *verifies*, *specified in* and *allocated to* links |

The health report (§5.2) uses the same rules.

---

## 9. Where attributes go, and writing documents ESAM can read

### 9.1 One question decides it

*Is this a fact about the **thing**, or about one **requirement**?*

| It describes… | It goes in… | Examples |
|---|---|---|
| The subsystem or function itself | **The baseline** | Safety-critical, MIL-STD-882E SwCI, `os_class: rtos`, J1939 source address |
| The document as a whole | **The document's header table** | Document ID, type, revision, owning subsystem, classification |
| One requirement | **An attribute line under that requirement** | Safety-related, timing, verification method, parent requirement |
| One test case | **The fixed fields of that test case** | Which requirements it verifies, method |

The baseline says what the system **is**; the requirement attributes say what each requirement **demands**. The document ID ties them together. Because both use the same vocabulary, ESAM can cross-check them: for example, a subsystem listed as SwCI 1 whose SRS contains no safety-related requirement.

### 9.2 In the baseline

Use only the attributes and values the ontology allows (§8):

```yaml
- id: SUB-BRK-ECU
  name: "Brake Control ECU (ABS)"
  safety_critical: true
  safety_classification: "MIL-STD-882E SwCI 1"
  os_class: rtos
```

### 9.3 In every document: a header table

The first thing in every document, so ESAM can place it without guessing:

| Document | Type | Revision | Subsystem |
|---|---|---|---|
| STD-BRK-001 | STD | A | SUB-BRK-ECU |

### 9.4 In an SRS: an attribute line under each requirement

Today a requirement reads:

> **SRS-BRK-021** On detection of an internal fault, the software shall revert the hydraulic unit to conventional braking within 20 ms.

A person can see it's safety-related and has a 20 ms deadline. ESAM can't do so reliably. Written for ESAM:

> **SRS-BRK-021** On detection of an internal fault, the software shall revert the hydraulic unit to conventional braking within 20 ms.
> Safety: SwCI 1 | Timing: 20 ms max | Verification: T | Traces to: SYS-BRK-004

Each field uses the ontology's values: **Safety** a SwCI from MIL-STD-882E (only if the requirement is safety-related), **Timing** a value with units (`20 ms max`, `100 ms ± 10 ms`), **Verification** I, A, D or T, **Traces to** the parent requirement IDs.

### 9.5 In an STD: fixed fields in every test case

```
TC-BRK-011 — Pressure release time on incipient wheel lock-up
Verifies: SRS-BRK-011.
Method: Test.
Steps: …
Pass criteria: …
```

`Verifies:` is what lets ESAM answer "which requirements have no test?" and "what does this test cover?".

### 9.6 Checklist, in priority order

| # | Change | Why |
|---|---|---|
| 1 | A header table on every document | ESAM places and checks the document without guessing |
| 2 | One requirement ID format per document type (`SRS-BRK-021`) | Traceability breaks on mixed formats |
| 3 | An attribute line under every requirement, using the ontology's values | Turns prose into facts ESAM can check and link |
| 4 | Refer to things by ID ("per ICD-BRK-001 §3"), not by description ("per the brake interface document") | Every ID mention can become a link automatically |
| 5 | A `Verifies:` field in every test case | Powers the traceability check (§5.2, check 4) |
| 6 | Real heading styles, one "shall" per paragraph, consistent units (`ms`, not `msec`) | Clean chunking (§2.4) |

A Word template with the header table and a styled attribute line makes items 1 and 3 a matter of filling in fields.

---

## 10. Requirements and tests in the graph (traceability)

### 10.1 What changed

Until now the graph knew about **documents**: the Brake ECU has an SRS. It now also knows what is **inside** them:

| Item | Read from | Linked to |
|---|---|---|
| **Requirement** | Each `SRS-…` line in an SRS (§9.4) | *specified in* its SRS; *allocated to* the SRS's subsystem |
| **Test case** | Each `TC-…` heading in an STD, with its fixed fields (§9.5) | *specified in* its STD; **verifies** each requirement on its `Verifies:` line |

Nothing is guessed. ESAM reads the formats this manual asks you to write, and every item remembers which document it came from. When a new revision of a document is loaded, everything the old revision contributed is replaced, so a deleted requirement or test case doesn't linger.

### 10.2 What you can now ask

| Question | How ESAM answers it |
|---|---|
| Which requirements have no test? | Requirements with no test case verifying them |
| What verifies SRS-BRK-011? | The test cases whose `Verifies:` line names it, and the STD each is in |
| How well is each subsystem covered? | Requirements allocated to it, and how many are verified |
| Does any test cite a requirement that doesn't exist? | Reported when the STD is loaded (e.g. a typo `SRS-BRK-999`) |

### 10.3 Where you see it

- **SE Cockpit Dashboard, Requirement coverage:** per subsystem, how many requirements and how many are verified, plus the list of untested requirements. For SYNTH-LTV today: **0 of 16 verified**, because no test description exists yet. When the Brake ECU's STD is added, four of its nine requirements become verified.
- **SE Cockpit Graph tab:** requirements sit under their SRS and test cases under their STD. Selecting a requirement shows what verifies it, or "no test case verifies this".
- **Answers to questions:** asked "How is SRS-BRK-011 verified?", ESAM now checks the requirement itself, not just whether an STD exists, and reports *"no test case verifies SRS-BRK-011"* when that is the case. Once the STD exists, it names TC-BRK-011 and its document.

### 10.4 What it depends on

Traceability is only as good as the documents' structure:

- Requirement lines in the `- **SRS-BRK-011** …` form, one requirement per line item.
- Test cases as `TC-…` headings with a `Verifies:` line listing requirement IDs exactly as written in the SRS.
- Consistent IDs (§9.6). A `Verifies:` line that says "SRS BRK 11" links to nothing.

---
