import json
from pathlib import Path

from app.ingest.docs import chunk_text
from app.ingest.playbooks import load_playbooks
from app.retrieval import lucene_query, rrf
from app.scrub import Scrubber

SAMPLE = Path(__file__).parent.parent / "data" / "playbooks" / "sample_collection.json"


def _playbooks():
    return {p.name: p for p in load_playbooks(SAMPLE, Scrubber(("acme-tenant",)))}


def test_parses_structure():
    pbs = _playbooks()
    triage = pbs["Alert Triage - Phishing"]
    assert triage.collection == "SOC - Phishing Response"
    assert triage.trigger_type == "record_event"
    assert triage.modules == ["alerts"]
    assert [s.name for s in triage.steps] == ["On Alert Create", "Enrich", "Block Sender IP", "Notify SOC"]
    assert triage.calls == [{"uuid": "11111111-1111-4111-8111-111111111111",
                             "step": "22222222-2222-4222-8222-222222222222:bbbbbbbb-0000-4000-8000-000000000002"}]
    block = triage.steps[2]
    assert (block.kind, block.connector, block.operation) == ("connector", "fortinet-fortigate", "block_ip")


def test_referenced_playbook_and_decision():
    enrich = _playbooks()["Enrich Indicators"]
    assert enrich.trigger_type == "referenced"
    assert [s.kind for s in enrich.steps] == ["trigger", "connector", "decision", "update_record"]
    assert enrich.routes[-1]["label"] == "Malicious"


def test_secrets_scrubbed():
    blob = json.dumps([s.__dict__ for p in _playbooks().values() for s in p.steps])
    blob += " ".join(p.description for p in _playbooks().values())
    for secret in ("AIzaSyD-FAKE", "Sup3rS3cret", "10.20.30.40", "acme-tenant", "eyJhbGciOiJIUzI1NiJ9", "soc-lead@"):
        assert secret not in blob, secret
    assert "<IP>" in blob and "<REDACTED>" in blob


def test_summary_mentions_connectors_and_calls():
    pbs = _playbooks()
    names = {p.uuid: p.name for p in pbs.values()}
    summary = pbs["Alert Triage - Phishing"].summary(names)
    assert "fortinet-fortigate.block_ip" in summary and "calls playbook Enrich Indicators" in summary


def test_chunking_and_helpers():
    chunks = chunk_text("\n\n".join(f"para {i} " + "x" * 300 for i in range(10)), size=1000, overlap=100)
    assert len(chunks) > 1 and all(len(c) <= 1300 for c in chunks)
    assert lucene_query('block_ip: "fortigate" AND (x)') == "block_ip fortigate x"
    fused = rrf([{"id": "a"}, {"id": "b"}], [{"id": "b"}, {"id": "c"}])
    assert fused[0]["id"] == "b"


def test_hard_coded_secrets_flagged():
    pbs = _playbooks()
    names = {p.uuid: p.name for p in pbs.values()}
    triage = pbs["Alert Triage - Phishing"]
    flagged = [s.name for s in triage.steps if s.has_secret]
    assert flagged == ["Block Sender IP", "Notify SOC"]
    assert "Security finding" in triage.summary(names)


def test_sensitive_key_matching():
    s = Scrubber()
    out = s.obj({"api_key": "x", "access_token": "x", "clientSecret": "x", "password": "x",
                 "authentication_methods": ["api_key"], "token_type": "bearer", "hostname": "fw01"})
    assert out["api_key"] == out["access_token"] == out["clientSecret"] == out["password"] == "<REDACTED>"
    assert out["authentication_methods"] == ["api_key"] and out["hostname"] == "fw01"


def test_aws_identifiers_scrubbed():
    s = Scrubber()
    out = s.text("key AKIAIOSFODNN7EXAMPLE used on arn:aws:iam::123456789012:user/build-bot "
                 "and arn:aws:s3:::public-bucket; timestamp 202610120930 stays")
    assert "AKIAIOSFODNN7EXAMPLE" not in out and "<AWS_KEY_ID>" in out
    assert "arn:aws:iam::<AWS_ACCOUNT>:user/build-bot" in out
    assert "arn:aws:s3:::public-bucket" in out and "202610120930" in out
    assert s.obj({"aws_secret_access_key": "wJalrXUtnFEMI/K7MDENG"})["aws_secret_access_key"] == "<REDACTED>"


def test_secret_detection_ignores_references_and_settings():
    s = Scrubber()
    out = s.obj({"password": "{{vars.input.params.password}}", "pass_input_record": True, "pass_parent_env": "true",
                 "token_expiration_in_seconds": 3600, "api_key": "", "token": "abc123literal"})
    assert out["password"] == "{{vars.input.params.password}}"     # Jinja reference kept
    assert out["pass_input_record"] is True and out["pass_parent_env"] == "true"
    assert out["token_expiration_in_seconds"] == 3600 and out["api_key"] == ""
    assert out["token"] == "<REDACTED>"                              # real literal secret


def test_set_variable_step_type_by_iri():
    from app.ingest.playbooks import _classify
    step = {"stepType": "/api/3/workflow_step_types/04d0cf46-b6a8-42c4-8683-60a7eaa69e8f",
            "arguments": {"splunkHuntStart": "{{arrow.utcnow()}}"}}
    assert _classify(step, False) == "set_variable"
