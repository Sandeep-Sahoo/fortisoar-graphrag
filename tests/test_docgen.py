"""Playbook documentation (fortisoar-playbook-docs format), with and without AI. No real model calls."""
import json
from types import SimpleNamespace

from app import docgen
from app.scrub import Scrubber


def _playbook():
    def step(uuid, name, args=None, st="/api/3/workflow_step_types/x"):
        return {"uuid": uuid, "name": name, "arguments": args or {}, "stepType": st}

    def route(a, b, label=""):
        return {"sourceStep": f"/api/3/workflow_steps/{a}", "targetStep": f"/api/3/workflow_steps/{b}", "label": label}

    return {"uuid": "pb-1", "name": "Block malicious IP", "description": "Blocks a malicious IP after approval.", "isActive": True,
            "triggerStep": "/api/3/workflow_steps/t",
            "steps": [step("t", "Start", {"route": "x", "executeButtonText": "Run", "resources": ["alerts"]}),
                      step("c", "Configuration", {"step_variables": {"notify_to": "soc.dept@example.com", "internal_cidr": "10.1.0.0/16",
                                                                      "ipBlockPolicyName": "IP_BLOCK_POLICY"}},
                           "/api/3/workflow_step_types/04d0cf46-b6a8-42c4-8683-60a7eaa69e8f"),
                      step("v", "Get IP Reputation", {"connector": "virustotal", "operation": "query_ip", "operationTitle": "Get IP Reputation"}),
                      step("d", "Is Malicious", {"conditions": [{"option": "Yes", "step_iri": "/api/3/workflow_steps/a"},
                                                                {"option": "No", "default": True, "step_iri": "/api/3/workflow_steps/e"}]}),
                      step("a", "Approve Block", {"type": "InputBased", "is_approval": True, "response_mapping": {"options": [
                          {"option": "Approve", "step_iri": "/api/3/workflow_steps/b"}, {"option": "Reject", "step_iri": "/api/3/workflow_steps/e"}]},
                          "owner_detail": {"isAssigned": True, "assignedToTeam": [{"teamname": "SOC Team"}]}}),
                      step("b", "Block IP", {"connector": "fortigate-firewall", "operation": "block_ip", "operationTitle": "Block IP Address"}),
                      step("e", "No Action", {"step_variables": {"x": 1}}, "/api/3/workflow_step_types/04d0cf46-b6a8-42c4-8683-60a7eaa69e8f")],
            "routes": [route("t", "c"), route("c", "v"), route("v", "d"), route("d", "a"), route("d", "e"), route("a", "b"), route("a", "e")]}


def _spec():
    wf = _playbook()
    return {"kind": "source", "target": "src1", "title": "Test source", "source": {"type": "upload", "name": "Test source"},
            "meta": {}, "playbooks": [{"wf": wf, "collection": "Test collection"}], "names": {wf["uuid"]: wf["name"]}}


SVC = SimpleNamespace(scrubber=Scrubber(()), graph=SimpleNamespace(read=lambda *a, **k: []),
                      settings=SimpleNamespace(fetch_org="fortinet-fortisoar"))


def test_standard_document_has_the_skill_sections():
    md, stats = docgen.build_markdown(SVC, _spec(), False, [{"version": "1.0", "date": "2026-10-12", "by": "t", "summary": "Initial"}])
    for heading in ("— Playbook Documentation", "## 1. Overview", "### Why It Matters to the SOC Team", "### Connectors Used",
                    "Playbook Collection: `Test collection`", "### Configuration Variables", "### Flow at a Glance", "### Detailed Flow",
                    "### Step-by-step Description", "Operational Notes", "Version History"):
        assert heading in md, heading
    assert "### Dependencies" not in md and "Record Set" not in md          # user export: no info.json
    assert "user-supplied export" in md
    assert md.count("```mermaid") == 2
    assert '{"Is Malicious"}' in md and '|"Approve"|' in md                   # diamonds + branch labels
    assert "On **Reject** → continues to **No Action**." in md               # each branch outcome is its own point
    assert "placeholder — replace" in md and "soc.dept@example.com" in md and "network range" in md and "must match your environment" in md
    assert "assigned to SOC Team" in md
    assert "**Phase 1 —" in md and "\n1. **" in md                          # numbering restarts per phase
    assert stats == {"playbooks": 1, "ai_used": 0, "ai_errors": []}


def test_generate_appends_version_history(monkeypatch):
    monkeypatch.setattr(docgen, "resolve", lambda svc, kind, target: _spec())
    d1 = docgen.generate(SVC, "source", "src1", False, "ana", note="first")
    d2 = docgen.generate(SVC, "source", "src1", False, "bob")
    assert d1["id"] == d2["id"] and [h["version"] for h in d2["history"]] == ["1.0", "1.1"]
    assert d2["history"][0] == d1["history"][0]                              # earlier rows never change
    assert "| 1.0 |" in d2["markdown"] and "| 1.1 |" in d2["markdown"]
    assert docgen.get(d1["id"])["markdown"] == d2["markdown"]
    assert docgen.list_docs()[0]["id"] == d1["id"] and docgen.delete(d1["id"]) and docgen.get(d1["id"]) is None


def test_ai_writeup_is_validated_against_the_json():
    class FakeLLM:
        calls = 0

        def complete(self, system, prompt, json_mode=False):
            FakeLLM.calls += 1
            return json.dumps({
                "why_it_matters": "Analysts no longer chase IP reputations by hand.",
                "summary": "Runs from an alert and blocks a malicious IP once approved.",
                "glance": {"nodes": [{"id": "A", "label": "Alert raised"}, {"id": "B", "label": "Check the IP"},
                                     {"id": "C", "label": "Is it bad?", "shape": "decision"}, {"id": "D", "label": "Block it", "shape": "end"}],
                           "edges": [{"from": "A", "to": "B"}, {"from": "B", "to": "C"}, {"from": "C", "to": "D", "label": "Yes"}]},
                "phases": [{"title": "Check the IP", "intro": "First we find out whether the IP is known-bad.",
                            "steps": [{"step": "Get IP Reputation", "text": "Asks VirusTotal about the IP."},
                                      {"step": "Invented Step", "text": "should be dropped"}]}]})

    svc = SimpleNamespace(**vars(SVC), llm=FakeLLM())
    md, stats = docgen.build_markdown(svc, _spec(), True, [])
    assert FakeLLM.calls == 1 and stats["ai_used"] == 1
    assert "Analysts no longer chase IP reputations by hand." in md and "Asks VirusTotal about the IP." in md
    assert "Invented Step" not in md
    assert "**Phase 2 — Other steps**" in md and "**Block IP**" in md       # steps the model skipped are kept
    assert '"Alert raised"' in md and "On **Approve** → continues to **Block IP**." in md


def test_ai_failure_falls_back_to_standard_wording():
    class Broken:
        def complete(self, *a, **k):
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota")

    md, stats = docgen.build_markdown(SimpleNamespace(**vars(SVC), llm=Broken()), _spec(), True, [])
    assert stats["ai_used"] == 0 and stats["ai_errors"] and "AI write-up not applied" in md and "### Why It Matters" in md
