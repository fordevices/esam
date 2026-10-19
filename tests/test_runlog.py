"""Run log keeps only the latest run per check."""

from esamkit.runlog import last_runs, save_run


def test_latest_run_replaces_previous(pg_dsn):
    save_run(pg_dsn, "pytest-check", "Pytest check", False, {"n": 1})
    save_run(pg_dsn, "pytest-check", "Pytest check", True, {"n": 2})
    rows = [r for r in last_runs(pg_dsn) if r["name"] == "pytest-check"]
    assert len(rows) == 1 and rows[0]["passed"] is True and rows[0]["result"] == {"n": 2}
