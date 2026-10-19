"""Environment profiles, probes that need no network, capabilities, and the usage ledger."""

import pytest

from esamkit import environment as env
from esamkit.usage import UsageLedger, summary


def test_profiles_load_and_secrets_are_named_not_stored():
    assert {"local", "pilot-il5", "full-janusgraph"} <= set(env.profile_names())
    for name in env.profile_names():
        text = (env.PROFILE_DIR / f"{name}.yaml").read_text()
        assert "sk-" not in text                                # no API keys in profiles
    assert "dsn_default" not in (env.PROFILE_DIR / "pilot-il5.yaml").read_text()   # no fallback password on the pilot


def test_mask_dsn():
    assert env.mask_dsn("postgresql://esam_user:s3cret@vm-1:5432/esam_pilot") == "postgresql://esam_user:***@vm-1:5432/esam_pilot"


def test_settings_follow_the_profile(monkeypatch):
    monkeypatch.setenv("ESAM_PG_DSN", "postgresql://u:p@vm-1:5432/db")
    monkeypatch.setenv("ESAM_GATEWAY_URL", "https://gateway.example/v1")
    s = env.settings_for(env.load_profile("pilot-il5"))
    assert s.pg_dsn == "postgresql://u:p@vm-1:5432/db" and s.chat_base_url == "https://gateway.example/v1"
    assert s.chat_model == "gpt-5.1" and s.graph_backend == "postgres"


def test_pilot_without_settings_reports_what_to_set(monkeypatch):
    for v in ("ESAM_PG_DSN", "ESAM_GATEWAY_URL", "ESAM_GATEWAY_KEY"):
        monkeypatch.delenv(v, raising=False)
    rep = env.inspect("pilot-il5", network=False)
    p = {x["name"]: x for x in rep["probes"]}
    assert p["database"]["status"] == "not_configured" and "ESAM_PG_DSN" in p["database"]["detail"]
    assert p["llm.reasoning"]["status"] == "not_configured" and "ESAM_GATEWAY_URL" in p["llm.reasoning"]["detail"]
    assert not rep["capabilities"]["agents_can_run"]
    assert rep["capabilities"]["tools"]["walk_graph"]["blocked_by"] == ["graph"]


def test_janusgraph_is_declared_but_not_implemented(monkeypatch):
    monkeypatch.delenv("ESAM_GREMLIN_URL", raising=False)
    p = env.probe_graph(env.load_profile("full-janusgraph"), {"graph_url": "", "dsn": ""}, env.Probe("database", "ok", ""))
    assert p.status == "not_implemented"


def test_air_gapped_profile_never_calls_a_public_host(monkeypatch):
    prof = {"network": {"internet": False}}
    r = {"roles": {"reasoning": {"model": "gpt-5.1", "base_url": "https://api.openai.com/v1", "base_url_env": None,
                                 "api_key_env": "X_KEY", "api_key_set": True}}}
    monkeypatch.setenv("X_KEY", "k")
    p = env.probe_llm(prof, r, "reasoning", network=True)
    assert p.status == "unavailable" and "air-gapped" in p.detail


def test_capabilities_follow_probe_results():
    probes = [env.Probe("files", "ok", ""), env.Probe("graph", "ok", ""), env.Probe("database", "ok", ""),
              env.Probe("vector", "unavailable", ""), env.Probe("embeddings", "ok", ""), env.Probe("llm.reasoning", "warning", "")]
    caps = env.capabilities(probes)
    assert caps["agents_can_run"] and caps["tools"]["health_report"]["enabled"]
    assert caps["tools"]["search_documents"] == {"enabled": False, "blocked_by": ["vector"]}


def test_usage_ledger_file_sink(tmp_path):
    led = UsageLedger("run-1", "local", dsn=None, path=tmp_path / "u.jsonl")
    led.record("llm", agent="researcher", model="gpt-5.1", prompt_tokens=1000, completion_tokens=200)
    led.record("tool", tool="walk_graph", duration_ms=12)
    led.record("tool", tool="walk_graph", duration_ms=8, ok=False)
    s = summary(None, path=tmp_path / "u.jsonl", pricing={"gpt-5.1": {"input_per_1k": 0.01, "output_per_1k": 0.03}})
    assert s["runs"] == 1 and s["models"]["gpt-5.1"]["prompt_tokens"] == 1000
    assert s["models"]["gpt-5.1"]["cost"] == pytest.approx(0.016)
    assert s["tools"]["walk_graph"] == {"calls": 2, "failures": 1, "total_ms": 20}


def test_usage_ledger_database_sink(pg_dsn, tmp_path):
    led = UsageLedger("run-db-test", "local", dsn=pg_dsn, path=tmp_path / "unused.jsonl")
    assert led.sink == "database"
    led.record("llm", model="pytest-model", prompt_tokens=5, completion_tokens=7)
    s = summary(pg_dsn, path=tmp_path / "unused.jsonl")
    assert s["models"]["pytest-model"]["completion_tokens"] >= 7
    assert not (tmp_path / "unused.jsonl").exists()
