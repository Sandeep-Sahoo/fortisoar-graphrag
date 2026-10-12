import base64
import io
import json
import zipfile

import pytest

from app.review import rules as rules_mod
from app.review.engine import Context, diff_signatures, review_workflow
from app.scrub import Scrubber

SC = Scrubber(())


def step(uuid, name, args=None, step_type="/api/3/workflow_step_types/x", description=""):
    return {"uuid": uuid, "name": name, "arguments": args or {}, "stepType": step_type, "description": description}


def route(a, b, label=""):
    return {"sourceStep": f"/api/3/workflow_steps/{a}", "targetStep": f"/api/3/workflow_steps/{b}", "label": label}


def risky_playbook():
    return {
        "uuid": "pb-1", "name": "Block", "description": "", "isActive": True, "debug": True, "recordTags": [],
        "triggerStep": "/api/3/workflow_steps/t",
        "steps": [
            step("t", "Start", {"resource": "alerts", "resources": ["alerts"], "fieldbasedtrigger": {"filters": []}}),
            step("c1", "Step Output", {"connector": "fortigate-firewall", "operation": "block_ip", "operationTitle": "Block IP",
                                       "config": "", "params": {"ip": "10.20.30.40", "api_key": "s3cr3t-literal-value"}}),
            step("d1", "Is bad", {"conditions": [{"option": "Yes", "step_iri": "/api/3/workflow_steps/c1", "condition": "{{x}}"}]}),
            step("orphan", "Lonely", {"connector": "virustotal", "operation": "get_file_report", "useMockOutput": True,
                                      "ignore_errors": True}),
        ],
        "routes": [route("t", "d1"), route("d1", "c1", "Yes")],
    }


def test_risky_playbook_findings_score_and_verdict():
    r = review_workflow(risky_playbook(), "Coll", SC)
    rules_hit = {f["rule"] for f in r["findings"]}
    for expected in ("SEC-01", "SEC-02", "SEC-03", "SEC-04", "SEC-07", "REL-01", "REL-02", "REL-03", "PERF-02",
                     "PERF-03", "MNT-01", "MNT-02", "MNT-03", "MNT-05"):
        assert expected in rules_hit, expected
    assert r["verdict"] == "Request changes" and r["grade"] in ("D", "F") and r["score"] < 60
    # evidence is masked: no full IP or secret in any finding
    blob = json.dumps(r["findings"])
    assert "10.20.30.40" not in blob and "s3cr3t" not in blob and "10.20.x.x" in blob
    assert r["provisional"] and r["manual_pending"] == 4
    assert "Must fix before production" in r["summary"]


def clean_playbook():
    return {
        "uuid": "pb-2", "name": "Phishing - Block sender after approval", "description": "Blocks the sender once approved.",
        "recordTags": ["phishing"], "isActive": True, "debug": False, "triggerStep": "/api/3/workflow_steps/t",
        "steps": [
            step("t", "Start", {"route": "x", "executeButtonText": "Run"}),
            step("v", "Configuration", {"step_variables": {"team": "soc"}}, "/api/3/workflow_step_types/04d0cf46-b6a8-42c4-8683-60a7eaa69e8f"),
            step("a", "Approve block", {"type": "InputBased", "inputVariables": [], "response_mapping":
                                       {"options": [{"option": "Approve"}, {"option": "Reject"}]}}),
            step("c", "Block sender", {"connector": "exchange", "operation": "block_sender", "config": "abc",
                                       "ignore_errors": True}),
        ],
        "routes": [route("t", "v"), route("v", "a"), route("a", "c", "Approve")],
    }


def test_clean_playbook_scores_high_and_approval_gate_satisfies_sec03():
    wf = clean_playbook()
    r = review_workflow(wf, "Coll", SC)
    assert not [f for f in r["findings"] if f["rule"] == "SEC-03"]
    assert r["score"] >= 90 and r["verdict"] == "Approve" and r["counts"]["critical"] == 0


def test_manual_answers_affect_score():
    wf = clean_playbook()
    base = review_workflow(wf, "C", SC)["score"]
    failed = review_workflow(wf, "C", SC, Context(answers={"GOV-01": {"answer": "fail"}}))
    assert failed["score"] < base and any(f["rule"] == "GOV-01" for f in failed["findings"])
    passed = review_workflow(wf, "C", SC, Context(answers={k: {"answer": "pass"} for k in ("GOV-01", "GOV-02", "GOV-03", "GOV-04")}))
    assert not passed["provisional"]


def test_custom_rules_and_overrides():
    rule = rules_mod.add_rule({"title": "No VirusTotal", "category": "Governance", "severity": "high",
                               "check": "connector_forbidden", "params": {"connectors": "virustotal"}}, "tester")
    r = review_workflow(risky_playbook(), "C", SC)
    assert any(f["rule"] == rule["id"] for f in r["findings"])
    rules_mod.update_rule("SEC-01", {"enabled": False}, "tester")
    assert not any(f["rule"] == "SEC-01" for f in review_workflow(risky_playbook(), "C", SC)["findings"])
    with pytest.raises(ValueError):
        rules_mod.delete_rule("SEC-01")
    with pytest.raises(ValueError):
        rules_mod.add_rule({"title": "bad", "check": "text_forbidden", "params": {"pattern": "("}}, "t")
    manual = rules_mod.add_rule({"title": "Peer reviewed", "check": "manual", "severity": "medium"}, "t")
    r = review_workflow(risky_playbook(), "C", SC)
    assert any(c["rule"] == manual["id"] for c in r["checklist"])
    assert rules_mod.delete_rule(rule["id"]) and rules_mod.version() >= 4


def test_diff_signatures():
    d = diff_signatures({"a": "x", "b": "y"}, {"b": "z", "c": "w"})
    assert d == {"added": ["c"], "removed": ["a"], "changed": ["b"]}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_unpack_accepts_json_and_zip_and_blocks_traversal():
    from app.review.sources import unpack

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("exports/a.json", "{}")
        zf.writestr("readme.txt", "ignored")
    files = unpack([{"name": "one.json", "content_base64": _b64(b"{}")}, {"name": "pack.zip", "content_base64": _b64(buf.getvalue())}])
    assert set(files) == {"one.json", "pack/exports/a.json"}
    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../etc/x.json", "{}")
    with pytest.raises(ValueError):
        unpack([{"name": "evil.zip", "content_base64": _b64(evil.getvalue())}])
    with pytest.raises(ValueError):
        unpack([{"name": "../x.json", "content_base64": _b64(b"{}")}])
    with pytest.raises(ValueError):
        unpack([{"name": "run.sh", "content_base64": _b64(b"echo")}])


def test_git_url_validation():
    from app.review.sources import clone

    for bad in ("http://github.com/a/b", "https://user:pw@github.com/a/b", "ssh://git@github.com/a/b", "file:///etc",
                "https://github.com/a/b; rm -rf /"):
        with pytest.raises(ValueError):
            clone(bad, "", "", None, "x")
    with pytest.raises(ValueError):
        clone("https://github.com/a/b", "--upload-pack=evil", "", None, "x")


def test_glance_diagram_collapses_stages():
    from app.exports import glance_diagram

    flow = {"trigger_type": "record_event", "modules": ["alerts"], "steps": [
        {"kind": "trigger", "connector": None}, {"kind": "set_variable", "connector": None},
        {"kind": "connector", "connector": "virustotal"}, {"kind": "connector", "connector": "abuseipdb"},
        {"kind": "decision", "connector": None}, {"kind": "manual_input", "connector": None}]}
    text = glance_diagram(flow)
    assert text.startswith("flowchart TD") and "virustotal, abuseipdb" in text and "S3{" in text
    assert text.count("-->") == 4
