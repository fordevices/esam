"""The task list and worker (esamkit.tasks) against the test database, with
fake handlers: no LLM, no crew."""

import sys

import pytest

from esamkit.tasks import TaskQueue, process_one


@pytest.fixture()
def q(pg_dsn):
    queue = TaskQueue(pg_dsn)
    with queue._conn() as c:
        c.execute("TRUNCATE esam_tasks RESTART IDENTITY")
    return queue


def test_claim_order_is_priority_then_age(q):
    a = q.add("ask", {"question": "first"})
    b = q.add("ask", {"question": "urgent"}, priority=5)
    c = q.add("ask", {"question": "second"})
    assert [q.claim("w")["id"] for _ in range(3)] == [b, a, c]
    assert q.claim("w") is None


def test_two_workers_never_take_the_same_task(q):
    ids = {q.add("ask", {"question": str(i)}) for i in range(4)}
    other = TaskQueue(q.dsn)                       # a second connection, as a second worker would have
    got = [q.claim("w1")["id"], other.claim("w2")["id"], q.claim("w1")["id"], other.claim("w2")["id"]]
    assert sorted(got) == sorted(ids)


def test_claim_only_the_kinds_a_worker_handles(q):
    q.add("author", {"brief": "b.yaml"})
    r = q.add("review", {"file": "x.md"})
    assert q.claim("w", ("review",))["id"] == r
    assert q.claim("w", ("review",)) is None


def test_cancel_and_requeue(q):
    t = q.add("ask", {"question": "x"})
    assert q.cancel(t) and q.get(t)["status"] == "cancelled"
    assert not q.cancel(t)                          # only a queued task can be cancelled
    u = q.add("ask", {"question": "y"})
    q.claim("w")
    q.finish(u, ok=False, error="boom")
    assert q.requeue(u) and q.get(u)["status"] == "queued" and q.get(u)["error"] == ""


def test_unknown_kind_is_refused(q):
    with pytest.raises(ValueError):
        q.add("deploy", {})


def test_process_one_records_success_failure_and_exit(q):
    handlers = {
        "ask": lambda t: {"ok": True, "run_id": "run-1", "outcome": f"answered {t['payload']['question']}",
                          "result": {"state": "x"}},
        "review": lambda t: (_ for _ in ()).throw(RuntimeError("database down")),
        "author": lambda t: sys.exit("NOT READY: the environment disables save_draft"),
    }
    ok = q.add("ask", {"question": "q1"}, priority=3)
    bad = q.add("review", {"file": "x.md"}, priority=2)
    stop = q.add("author", {"brief": "b.yaml"}, priority=1)
    done = [process_one(q, handlers, "w") for _ in range(3)]
    assert process_one(q, handlers, "w") is None
    by_id = {t["id"]: t for t in done}
    assert by_id[ok]["status"] == "done" and by_id[ok]["run_id"] == "run-1" and by_id[ok]["outcome"] == "answered q1"
    assert by_id[bad]["status"] == "failed" and "database down" in by_id[bad]["outcome"] and "Traceback" in by_id[bad]["error"]
    assert by_id[stop]["status"] == "failed" and "save_draft" in by_id[stop]["outcome"]
    assert all(t["attempts"] == 1 and t["worker"] == "w" and t["finished_at"] for t in done)
