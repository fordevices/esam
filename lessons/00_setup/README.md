# Lesson 00 — Setup, and carrying it across the air gap

The ESAM pilot network has no internet access. Anything that downloads at runtime will fail
there. That includes `pip install`, Hugging Face model downloads and `docker pull`. So setup
happens in two places:

```
 CONNECTED MACHINE (this laptop)            AIR-GAPPED MACHINE (pilot VM)
 ───────────────────────────────            ─────────────────────────────
 1. pip download  -> wheels/        ──┐
 2. stage_model.py -> models/       ──┼──  approved transfer media  ──►  verify hashes
 3. docker save   -> images/*.tar   ──┘                                   pip install --no-index
                                                                          docker load
```

## On this laptop (to learn)

```bash
cd esam
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # CPU-only, ~200 MB not ~2 GB
pip install -r requirements.txt
python lessons/00_setup/stage_model.py        # downloads ~440 MB once, then tests an offline load
docker compose up -d --wait                   # local PostgreSQL 16 + pgvector on 127.0.0.1:5433
```

Expected last lines of `stage_model.py`:

```
Manifest OK — every file matches.
Offline load OK — 768-dimensional vectors.
```

### Why Postgres in Docker, not installed on the host

Postgres installed on the host means system packages, a `postgres` OS user, `pg_hba.conf`
authentication rules, and building or packaging the pgvector extension to match your exact
Postgres version. Each of those is a place for setup to go wrong. The `pgvector/pgvector:pg16`
image ships Postgres 16 with pgvector already built in, the same major version as the pilot.
The whole database is one container plus one named volume:

| Want to… | Run |
|---|---|
| start / stop | `docker compose up -d --wait` / `docker compose down` |
| poke at it with SQL | `docker exec -it esam-postgres psql -U esam_user -d esam_pilot` |
| throw everything away and start clean | `docker compose down -v` |

It listens on **127.0.0.1:5433**, so it won't collide with a host Postgres on 5432 and isn't
reachable from the network.

## Preparing the transfer bundle

```bash
# Python packages as wheel files, for Linux x86-64 / Python 3.10 (match the pilot VM)
pip download -r requirements.txt -d bundle/wheels
pip download torch --index-url https://download.pytorch.org/whl/cpu -d bundle/wheels

# The model folder (already has MANIFEST.sha256)
cp -r models bundle/

# Only for the pgvector backend, and only if the VM runs Docker:
docker pull pgvector/pgvector:pg16
docker save pgvector/pgvector:pg16 -o bundle/pgvector-pg16.tar
```

## On the air-gapped side

```bash
pip install --no-index --find-links bundle/wheels -r requirements.txt
python lessons/00_setup/stage_model.py --verify    # hashes + offline load, no network
```

## Two things to raise with your programme before the pilot

1. **Model weights on premises.** `ESAM_Pilot_Implementation_v1.0.md` §3.1 says *"No on-premises
   model weights."* A local embedding model is a set of model weights (~440 MB, MIT license).
   It's small and it can't generate text, but the statement as written forbids it. Either get
   an exception approved, or ask for an embeddings route on the IL5 API Gateway. With the
   gateway option you set `ESAM_EMBED_BACKEND=openai` and no code changes.
2. **License record.** The model card (`README.md` in the model folder) states the license.
   It travels with the files so whoever approves the transfer can read it.
