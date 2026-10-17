"""
PostgreSQL + pgvector backend — what the ESAM pilot runs on VM-1.

Locally this talks to the docker-compose container (port 5433); on the
pilot, point ESAM_PG_DSN at VM-1:
    ESAM_PG_DSN=postgresql://esam_user:***@<vm-1>:5432/esam_pilot

Differences from the earlier embeddings.py, on purpose:
  * The vector column's dimension comes from the embedder (no hard-coded 768).
  * A small `esam_collections` table records model_id/dim per collection —
    the "model guard" from vectorstores/base.py.
  * HNSW index instead of IVFFlat. IVFFlat clusters the rows that exist WHEN
    THE INDEX IS BUILT; built on an empty table (as before) its clusters are
    meaningless, and with the default probes=1 a search can return fewer than
    k rows. HNSW needs no training data and is safe to create up front.
    (Requires pgvector >= 0.5; the pilot has 0.8.)
"""

import json
import re

from .base import Chunk, Hit, VectorStore

_SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,50}$")


class PgVectorStore(VectorStore):
    def __init__(self, dsn: str, collection: str, model_id: str, dim: int):
        if not dsn:
            raise ValueError("ESAM_PG_DSN must be set for ESAM_VECTOR_BACKEND=pgvector")
        if not _SAFE_NAME.match(collection):
            # The collection name becomes part of a table name, so keep it to
            # plain identifiers — never interpolate arbitrary text into SQL.
            raise ValueError(f"collection name must match {_SAFE_NAME.pattern}")

        import psycopg
        from pgvector.psycopg import register_vector

        self._name = collection
        self._table = f"{collection}_chunks"
        self._model_id = model_id
        self._dim = dim
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)  # lets us pass Python lists as vectors
        self._open()

    def _open(self):
        c = self._conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS esam_collections (
                name           TEXT PRIMARY KEY,
                embed_model_id TEXT NOT NULL,
                embed_dim      INTEGER NOT NULL,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        c.execute("""
            INSERT INTO esam_collections (name, embed_model_id, embed_dim)
            VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING""",
                  (self._name, self._model_id, self._dim))
        stored_id, stored_dim = c.execute(
            "SELECT embed_model_id, embed_dim FROM esam_collections WHERE name = %s",
            (self._name,)).fetchone()
        self.check_model(self._name, stored_id, stored_dim, self._model_id, self._dim)

        # Table/index names are validated identifiers (see _SAFE_NAME);
        # all VALUES still go through %s parameters.
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                source_file TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                artifact_id TEXT NOT NULL DEFAULT '',
                metadata    JSONB NOT NULL DEFAULT '{{}}',
                embedding   vector({int(self._dim)}) NOT NULL
            )""")
        c.execute(f"CREATE INDEX IF NOT EXISTS {self._table}_src ON {self._table} (source_file)")
        c.execute(f"""CREATE INDEX IF NOT EXISTS {self._table}_hnsw
                      ON {self._table} USING hnsw (embedding vector_cosine_ops)""")

    def upsert(self, chunks, vectors) -> None:
        if not chunks:
            return
        with self._conn.cursor() as cur:
            cur.executemany(f"""
                INSERT INTO {self._table}
                    (id, content, source_file, chunk_index, artifact_id, metadata, embedding)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content, source_file = EXCLUDED.source_file,
                    chunk_index = EXCLUDED.chunk_index, artifact_id = EXCLUDED.artifact_id,
                    metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding""",
                [(c.id, c.text, c.source_file, c.chunk_index, c.artifact_id,
                  json.dumps(c.metadata), v) for c, v in zip(chunks, vectors)])

    def delete_source(self, source_file: str) -> int:
        cur = self._conn.execute(
            f"DELETE FROM {self._table} WHERE source_file = %s", (source_file,))
        return cur.rowcount

    def search(self, query_vector, k=5, where=None) -> list[Hit]:
        # `<=>` is pgvector's cosine DISTANCE operator (1 - cosine similarity).
        # ORDER BY distance + LIMIT k is what lets the HNSW index kick in.
        sql = [f"""SELECT id, content, source_file, chunk_index, artifact_id, metadata,
                          1 - (embedding <=> %(q)s::vector) AS score
                   FROM {self._table}"""]
        params = {"q": query_vector, "k": k}
        if where:
            # Exact-match filter. Top-level columns
            # are matched directly; anything else is looked up in metadata.
            conds = []
            for i, (key, val) in enumerate(where.items()):
                if key in ("source_file", "artifact_id", "chunk_index"):
                    conds.append(f"{key} = %(w{i})s")
                    params[f"w{i}"] = val
                else:
                    conds.append(f"metadata @> %(w{i})s::jsonb")
                    params[f"w{i}"] = json.dumps({key: val})
            sql.append("WHERE " + " AND ".join(conds))
        sql.append("ORDER BY embedding <=> %(q)s::vector LIMIT %(k)s")
        rows = self._conn.execute("\n".join(sql), params).fetchall()
        return [Hit(chunk=Chunk(id=r[0], text=r[1], source_file=r[2], chunk_index=r[3],
                                artifact_id=r[4], metadata=r[5]), score=float(r[6]))
                for r in rows]

    def count(self) -> int:
        return self._conn.execute(f"SELECT count(*) FROM {self._table}").fetchone()[0]

    def reset(self) -> None:
        self._conn.execute(f"DROP TABLE IF EXISTS {self._table}")
        self._conn.execute("DELETE FROM esam_collections WHERE name = %s", (self._name,))
        self._open()
