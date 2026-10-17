"""TEST-ONLY psycopg stand-in: executes via psql, returns dict rows via json_agg.
Lets esamkit's graph code run end-to-end on a machine with no psycopg wheel."""
import contextlib, json, re, subprocess

ARGS = ["psql", "-h", "127.0.0.1", "-p", "55433", "-U", "tester", "-d", "esamtest",
        "-v", "ON_ERROR_STOP=1", "-qtA"]

def _lit(v):
    if v is None: return "NULL"
    if isinstance(v, bool): return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)): return str(v)
    return "'" + str(v).replace("'", "''") + "'"

class _Result:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None

class _Conn:
    def execute(self, sql, params=None):
        if params:
            sql = re.sub(r"%\((\w+)\)s", lambda m: _lit(params[m.group(1)]), sql)
        s = sql.strip().rstrip(";")
        if re.match(r"(?is)^(select|with)\b", s):
            s = f"SELECT COALESCE(json_agg(t), '[]') FROM ({s}) t"
            out = subprocess.run(ARGS, input=s + ";", text=True, capture_output=True)
            if out.returncode: raise RuntimeError(out.stderr)
            return _Result(json.loads(out.stdout))
        out = subprocess.run(ARGS, input=s + ";", text=True, capture_output=True)
        if out.returncode: raise RuntimeError(out.stderr)
        return _Result([])
    @contextlib.contextmanager
    def transaction(self): yield

def connect(dsn, **kw): return _Conn()
