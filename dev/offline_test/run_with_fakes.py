"""TEST-ONLY: run an esam lesson script with a toy embedder + in-memory vector store
(the real ones need torch/model/pgvector, not downloadable here). Graph uses the
psql-backed psycopg shim on PYTHONPATH.   usage: run_with_fakes.py <script> [args...]"""
import hashlib, math, re, runpy, sys
from pathlib import Path
ESAM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ESAM))
import esamkit.factory as factory
from esamkit.embedders.base import Embedder
from esamkit.vectorstores.base import Hit, VectorStore

class ToyEmbedder(Embedder):
    """Bag-of-words hashed into 256 dims. NOT semantic: just exercises the plumbing."""
    model_id, dim = "toy-bow-256", 256
    def _v(self, t):
        v = [0.0] * self.dim
        for w in re.findall(r"[a-z0-9]+", t.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        return [x / n for x in v]
    def embed_documents(self, texts): return [self._v(t) for t in texts]
    def embed_query(self, text): return self._v(text)

class MemStore(VectorStore):
    rows = {}
    def upsert(self, chunks, vectors):
        for c, v in zip(chunks, vectors): self.rows[c.id] = (c, v)
    def delete_source(self, s):
        ids = [k for k, (c, _) in self.rows.items() if c.source_file == s]
        for k in ids: del self.rows[k]
        return len(ids)
    def search(self, q, k=5, where=None):
        def ok(c):
            return all((getattr(c, key, None) if key in ("artifact_id", "source_file") else c.metadata.get(key)) == val
                       for key, val in (where or {}).items())
        hits = [Hit(c, sum(a * b for a, b in zip(q, v))) for c, v in self.rows.values() if ok(c)]
        return sorted(hits, key=lambda h: -h.score)[:k]
    def count(self): return len(self.rows)
    def reset(self): self.rows.clear()

_store = MemStore()
factory.get_embedder = lambda *a, **k: ToyEmbedder()
factory.get_vector_store = lambda *a, **k: _store

for script_and_args in [s.split("|") for s in sys.argv[1:]]:
    sys.argv = script_and_args
    print(f"\n>>>>>>>> {' '.join(script_and_args)}")
    runpy.run_path(script_and_args[0], run_name="__main__")
