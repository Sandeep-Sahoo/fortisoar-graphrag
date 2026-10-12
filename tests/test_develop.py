"""Develop: guided design conversation -> proposal -> change -> develop -> customer document (no model calls).

Uses the downloaded connector catalog under data/fortinet/github read-only; skipped when it isn't there."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scrub import Scrubber

pytestmark = pytest.mark.skipif(not Path("data/fortinet/github").exists(), reason="connector catalog not downloaded")
USECASE = ("When a new high or critical alert has a suspicious source IP, check it on VirusTotal, and if malicious block it "
           "on FortiGate after SOC approval, close benign alerts and email the SOC.")


def _run_intake():
    from app.develop import conversation as C, store

    svc = SimpleNamespace(scrubber=Scrubber(()))
    s = store.create("tester")
    C.start(s)
    C.handle(s, svc, USECASE)
    while s["stage"] == "intake":
        C.handle(s, svc, values=s["messages"][-1]["default"])
    return s, svc


def test_intake_builds_a_valid_hardened_design():
    s, _ = _run_intake()
    p = s["messages"][-1]
    assert p["type"] == "proposal" and p["version"] == 1 and not p["problems"]
    assert p["flowchart"].startswith("flowchart TD") and "sequenceDiagram" in p["sequence"]
    kinds = [st["kind"] for st in s["spec"]["steps"]]
    assert kinds.index("approval") < kinds.index("contain")          # approval before the response action
    assert any(st.get("connector") == "virustotal" for st in s["spec"]["steps"])
    assert any(st.get("operation") == "block_ip" for st in s["spec"]["steps"])
    assert p["review"]["counts"]["critical"] == 0 and p["review"]["counts"]["high"] == 0


def test_change_requests_and_develop():
    from app.develop import conversation as C, generate, store

    s, svc = _run_intake()
    C.handle(s, svc, "notify on teams")
    assert any(st.get("connector") == "microsoft-teams" for st in s["spec"]["steps"])
    C.handle(s, svc, "no approval")
    assert not any(st["kind"] == "approval" for st in s["spec"]["steps"])
    C.handle(s, svc, "add an approval")
    assert any(st["kind"] == "approval" for st in s["spec"]["steps"])
    out = C.handle(s, svc, "please make it nicer")                     # not understood -> help, no new version
    assert out[-1]["type"] == "text" and len(s["versions"]) == 4
    msg = C.develop(s, svc, "tester")
    assert msg["type"] == "developed" and msg["review"]["score"] >= 85 and msg["review"]["verdict"] != "Request changes"
    doc = json.loads((store.files_dir(s["id"], 1) / "playbook.json").read_text())
    assert doc["type"] == "workflow_collections" and not generate.validate(doc)
    wf = doc["data"][0]["workflows"][0]
    assert wf["isActive"] is False and wf["debug"] is False and wf["description"]
    assert all(st["arguments"].get("ignore_errors") for st in wf["steps"] if st["arguments"].get("connector"))
    text = (store.files_dir(s["id"], 1) / "document.md").read_text()
    for heading in ("Flow at a glance", "Detailed workflow", "Sequence", "Prerequisites", "Test plan", "Quality review", "Version history"):
        assert heading in text


def test_panel_edits():
    from app.develop import conversation as C

    s, svc = _run_intake()
    first = next(st for st in s["spec"]["steps"] if st["kind"] == "enrich")
    C.panel_edit(s, {"op": "rename", "id": first["id"], "name": "VT lookup!"}, svc.scrubber)
    assert any(st["name"] == "VT lookup" for st in s["spec"]["steps"])   # made Jinja-safe
    C.panel_edit(s, {"op": "trigger", "type": "manual"}, svc.scrubber)
    assert s["spec"]["trigger"]["type"] == "manual"
    with pytest.raises(ValueError):
        C.panel_edit({**s, "spec": None}, {"op": "meta", "name": "x"}, svc.scrubber)


def test_parse_usecase_assigns_connector_roles():
    from app.develop.conversation import parse_usecase

    pre = parse_usecase("On a malware incident, check the file hash with VirusTotal, isolate the host with FortiEDR after approval, "
                        "disable the user account in Active Directory and open a ServiceNow ticket")
    assert pre["module"] == "incidents" and "hash" in pre["indicators"]
    assert pre["enrich"] == ["virustotal.file_reputation"]
    assert "activedirectory.disable_user_account" in pre["contain"] and "fortinet-fortiedr.isolate_collector" in pre["contain"]
    assert pre["tickets"] == ["servicenow.create_incident"]


def test_develop_api_and_viewer_permissions(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api as api
    import app.develop.api as dapi
    from app import users
    from app.auth import SessionStore

    class S:
        app_username, app_password, session_hours = "admin", "admin", 1

    monkeypatch.setattr("app.config.get_settings", lambda: S())
    svc = SimpleNamespace(settings=S(), scrubber=Scrubber(()))
    monkeypatch.setattr(api, "get_services", lambda: svc)
    monkeypatch.setattr(dapi, "get_services", lambda: svc)
    monkeypatch.setattr(api, "_sessions", SessionStore(1))
    client = TestClient(api.app)
    client.post("/auth/login", json={"username": "admin", "password": "admin"})
    sid = client.post("/develop/sessions").json()["id"]
    r = client.post(f"/develop/sessions/{sid}/message", json={"text": USECASE}).json()
    assert r["new"][-1]["type"] == "question"
    r = client.post(f"/develop/sessions/{sid}/message", json={"skip": True}).json()
    assert r["session"]["stage"] == "design" and r["new"][-1]["type"] == "proposal"
    assert client.get(f"/develop/sessions/{sid}/view").status_code == 200          # draft customer view
    assert client.get(f"/develop/sessions/{sid}/playbook.json").status_code == 404  # nothing developed yet
    assert client.post(f"/develop/sessions/{sid}/develop").json()["new"][0]["review"]["score"] >= 85
    dl = client.get(f"/develop/sessions/{sid}/playbook.json")
    assert dl.status_code == 200 and json.loads(dl.content)["type"] == "workflow_collections"
    client.post("/auth/logout")
    users.create("vic", "viewerpass1", "viewer", "admin")
    client.post("/auth/login", json={"username": "vic", "password": "viewerpass1"})
    assert client.post("/develop/sessions").status_code == 403
    assert client.get(f"/develop/sessions/{sid}").status_code == 200


def test_ai_turn_validates_the_models_spec():
    """AI mode with a stand-in model: invented operations are dropped, known ones are rebuilt from the catalog."""
    import json as _json

    from app.develop import conversation as C

    s, svc = _run_intake()

    class FakeLLM:
        def complete(self, system, prompt, json_mode=False):
            spec = _json.loads(prompt.split("SPEC:\n", 1)[1].split("\n\nCATALOG:", 1)[0])
            spec["steps"].insert(2, {"id": "sNEW01", "kind": "enrich", "name": "Made up", "connector": "nope", "operation": "fake_op"})
            spec["steps"].append({"id": "sNEW02", "kind": "notify", "connector": "microsoft-teams", "operation": "send_message"})
            spec["name"] = "AI renamed"
            return _json.dumps({"reply": "Added Teams.", "spec": spec})

    svc.llm = FakeLLM()
    out = C.handle(s, svc, "also tell the SOC on Teams and use my magic tool", ai=True)
    assert out[-1]["type"] == "proposal"
    assert s["spec"]["name"] == "AI renamed"
    assert not any(st.get("connector") == "nope" for st in s["spec"]["steps"])
    teams = [st for st in s["spec"]["steps"] if st.get("connector") == "microsoft-teams"]
    assert teams and teams[0]["params"] is not None and "Ignored operations" in out[-2]["text"]


@pytest.mark.parametrize("usecase", [
    USECASE,
    "On a malware incident, check the file hash with VirusTotal, isolate the host with FortiEDR after approval, "
    "disable the user account in Active Directory and open a ServiceNow ticket",
    "For phishing emails reported by users, check the sender and URLs on urlscan and VirusTotal, purge the email "
    "from Exchange after approval and notify on Teams",
    "When an indicator is created, look it up on VirusTotal and add the result to the indicator",
])
def test_every_generated_design_is_clean(usecase):
    from app.develop import conversation as C, store

    svc = SimpleNamespace(scrubber=Scrubber(()))
    s = store.create("tester")
    C.start(s)
    C.handle(s, svc, usecase)
    C.handle(s, svc, skip=True)
    msg = C.develop(s, svc, "tester")
    bad = [f for f in msg["review"]["findings"] if f["severity"] in ("critical", "high", "medium")]
    assert not bad, bad
    assert msg["review"]["score"] >= 95
    assert all(o["score"] >= 95 for o in msg["review"]["others"])     # rollback playbook too
