# ESAM crew (CrewAI)

Runs ESAM work without a Claude session: an environment check, then pipelines of three agents
(Triage → Researcher/Author → SE Reviewer) that use **only** tested `esamkit` functions as tools.
Same 5-file structure as the RTOS crew (`aidev/crew/`): `main.py`, `crew.py`, `custom_tools.py`,
`config/agents.yaml`, `config/tasks.yaml`.

```bash
pip install -r requirements-crew.txt          # once; CrewAI 1.15.x (see the file for why not 1.10.1)
python -m esamkit.environment                 # step 0 probes only (no agent, no tokens)
python -m crew.main env                       # step 0 with the Environment Engineer agent
python -m esamkit.usage                       # tokens per model, calls per tool, last 7 days

python -m crew.main review path/to/STD-BRK-001.md   # Pipeline A: A1 + A2, then STOP for the SE
python -m crew.main inbox                           # the same for every .md in work/inbox/
python -m crew.main runs                            # runs and their state
python -m crew.main approve <run>                   # the SE's decision: A3 ingest + A4 verify
python -m crew.main reject <run> --note "why"
python -m crew.main undo <run>                      # reverse an ingest exactly
python -m crew.main ask "Which brake requirements have no test?"

python -m esamkit.standards                         # the standards library
python -m esamkit.standards show "MIL-STD-882E 4.4.2"
python -m crew.main author data/briefs/SRS-ENG-001.yaml   # Pipeline B → work/drafts/
```

| Step | Status |
|---|---|
| 0. Environment: profiles, probes, capabilities, usage ledger, Environment Engineer | ✅ |
| 1. Pipeline A (ontology → document review → **stop for SE approval** → ingest → verify), interactive + inbox modes | ✅ |
| 2. Standards library (MIL-STD-882E) + Pipeline B (SRS authoring, verified, certification evidence pack) | ✅ |
| 3. Task list in the database + worker | planned |
| 4. Cockpit Tasks tab | planned |

## Step 0: environments

A profile in `config/environments/<name>.yaml` declares what a place offers; `ESAM_ENV=<name>` or `--env`
selects it. Shipped: `local` (this laptop), `pilot-il5` (gateway GPT-5.1 + Nemotron, VM-1 PostgreSQL,
air-gapped; template), `full-janusgraph` (template).

| Probe | Checks | Never does |
|---|---|---|
| host | CPU (warns without AVX2: the local-embedding SIGILL), cores, RAM, Python | |
| files | baseline, ontology, documents; baseline vs ontology | |
| database | connect, PostgreSQL + pgvector versions, latency; DSN shown masked | print the password |
| vector | chunks per collection; the collection's embedding model matches the profile's | load the model |
| graph | PostgreSQL CTE tables and counts; JanusGraph reachability (reported *not implemented in this build*) | |
| embeddings | model staged on disk, size, dimension | load the model |
| llm.\<role\> | key variable set; endpoint lists the model | spend tokens; contact a public host from an air-gapped profile |
| crewai | installed version; telemetry and memory off | |

**Capabilities**: each crew tool lists the services it needs (`TOOL_NEEDS` in `esamkit/environment.py`);
a tool is handed to agents only if all of them are OK. **Secrets**: profiles name environment variables,
never values. **Telemetry**: CrewAI/OpenTelemetry telemetry is switched off; crew memory is off (it needs
an embeddings API the pilot doesn't have).

**Usage ledger** (`esamkit/usage.py`): one row per LLM call (model, agent, tokens), tool call (duration,
success) and run, in PostgreSQL `esam_usage` or `work/usage/usage.jsonl` when there is no database. Cost is
shown only if a profile has `llm.pricing` with your own rates.

Run folders: `work/runs/<timestamp>_<name>/` (gitignored).

## Step 1: Pipeline A (a document → verified ingestion)

Every pipeline command first runs the step 0 probes (no tokens) and refuses to start if a tool it needs is
disabled in this environment.

| Step | Agent | Tools | Decides |
|---|---|---|---|
| A1 ontology check | Triage | `ontology_check`, `ontology_lookup`, `check_coverage` | the tool: the findings adding the document would introduce; ONTOLOGY OK/FAIL |
| A2 document review | Researcher | `review_document`, `verified_by`, `search_documents`, `walk_graph` | the agent compares each test case's pass criteria with the requirement it verifies (MATCHES / STRICTER / WEAKER / UNCLEAR) |
| A2 approval request | SE Reviewer | `ontology_check`, `review_document` | RECOMMENDATION: INGEST / DO NOT INGEST |
| **STOP** | **the SE** | `approve` / `reject` | **a person; nothing is ingested before this** |
| A3 ingest | Researcher | `ingest_document` | the tool refuses without a recorded approval, and re-runs the ontology check first |
| A4 verify | SE Reviewer | `verify_ingestion`, `health_report` | the tool: chunks stored, attached in the graph, test cases + VERIFIES links, search finds it, integrity passes, health before vs after; VERIFIED / NOT VERIFIED |

What the document must state (SE manual §2.5): a title line `# <ID> — <name>` and the table
`| Document | Type | Revision | Subsystem |`. Missing fields are ontology findings; the SE can supply them
with `--set revision=A --set attached_to=SUB-…`.

**What "ingest" changes**: copies the file into `data/documents/`, appends one line to the baseline's
`artifacts:` (as text, so the YAML's comments survive), stores the chunks, and reloads the graph with its
traceability. A later revision replaces the earlier file and line (kept in the run's `replaced/`). Each ingest
writes `manifest.json`; `undo` reverses exactly that. These are real edits to the SE baseline: review them
with `git diff` and commit them, or `undo`.

**Approving against the recommendation** needs `--against-recommendation` and a `--note`; it is recorded.
Ontology errors can't be approved past: the document is fixed and reviewed again.

Run folder of an intake: `state.json` (state, entry, ontology result, review, approval, health before/after,
verification), `incoming/<doc>`, one `.md` per task, `approval_request.md`, `manifest.json`,
`verification_report.md`. Inbox originals move to `work/inbox/done/`.

**Interactive** (`ask`): Triage pins down the entities and the documents that would answer (present or
MISSING), the Researcher answers from tools only with citations, the SE Reviewer re-runs the coverage and
traceability checks and keeps the MISSING section. Read-only: `ask` never changes anything.

## Step 2: standards library and Pipeline B (an SRS from an SE's brief)

**Standards library** (`data/standards/`, `esamkit/standards.py`): only clauses ESAM holds can be cited.

| Standard | Held | From |
|---|---|---|
| MIL-STD-882E (2012) | 74 clauses: 3.2 definitions, section 4 with Tables I–VI, Appendix B | `pdftotext` of the public PDF; `python -m esamkit.standards extract-882e <pdf>` rebuilds it and refuses a PDF with a different SHA-256 |
| DI-IPSC-81433A (1999) | Full text, 39 clauses (the SRS outline + the DID's instructions as I.0–I.4) | Transcribed from the scanned PDF (no text layer) |
| ISO/IEC/IEEE 29148 (2018) | Name and its requirement characteristics only | Copyrighted: no text |

Tables I, IV, V and VI are also held as data (`library.yaml`), and `tests/test_standards.py` checks every Table V
cell against the extracted text. `resolve("<standard> <clause>")` never returns a near miss. Change 1 of 882E (2023)
is not held.

**Pipeline B** (`python -m crew.main author <brief.yaml>`): the SE's brief names the document, its sources and the
safety assessment (severity and control category per safety-significant function). ESAM computes the SwCI and never
assesses safety itself.

| Step | Agent | Tools | Decides |
|---|---|---|---|
| brief check | (none) | `esamkit.srs.check_brief` | SwCI per SSF (Table V); brief vs baseline (e.g. 882E 3.2.31) |
| B1 gather basis | Triage | `read_brief`, `read_source`, `check_coverage`, `walk_graph`, `search_standards`, `standard_clause`, `ontology_lookup` | |
| B2 draft | Researcher | `read_brief`, `read_source`, `standard_clause`, `search_standards`, `save_draft`, `check_draft` | every save is linted at once (`esamkit.srs.check_srs`); errors must reach 0 |
| B3 verify | SE Reviewer | `check_draft`, `read_source`, `standard_clause`, `save_draft` | the lint; the agent confirms values against sources and that each cited clause *supports* the requirement; VERDICT |
| B4 evidence pack | SE Reviewer | `build_evidence_pack` | the pack; the runner rebuilds it from the final draft regardless |

Output in `work/drafts/<doc>_<timestamp>/`: the candidate SRS, `compliance_matrix.md`, `traceability_matrix.csv`,
`lint.md`/`lint.json`, `evidence_pack.md` (provenance and open items) and `se_reviewer_memo.md`. The draft enters
the baseline only through Pipeline A (`review` → SE `approve`). The pack is supporting evidence; certification is
the certifying authority's decision.
