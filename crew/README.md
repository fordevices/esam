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
```

| Step | Status |
|---|---|
| 0. Environment: profiles, probes, capabilities, usage ledger, Environment Engineer | ✅ |
| 1. Pipeline A (ontology → document review → **stop for SE approval** → ingest → verify), interactive + inbox modes | planned |
| 2. Standards library (MIL-STD-882E) + Pipeline B (SRS authoring, verified, certification evidence pack) | planned |
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
