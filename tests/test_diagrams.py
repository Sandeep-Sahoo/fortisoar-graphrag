from app.diagrams import flowchart, sequence

FLOW = {
    "name": "Block IP Address", "trigger_type": "manual", "modules": ["alerts"],
    "steps": [
        {"key": "a", "name": "Start", "kind": "trigger", "next": [{"to": "b", "label": ""}]},
        {"key": "b", "name": "Allowed?", "kind": "decision", "next": [{"to": "c", "label": "No"}]},
        {"key": "c", "name": "Approve \"block\"", "kind": "manual_input", "next": [{"to": "d", "label": ""}]},
        {"key": "d", "name": "Block", "kind": "connector", "connector": "fortinet-fortigate",
         "operation": "block_ip", "has_secret": True, "next": [{"to": "e", "label": ""}]},
        {"key": "e", "name": "Notify", "kind": "reference", "child": "Notify SOC On-Call", "next": []},
    ],
}


def test_flowchart_shapes_branches_and_escaping():
    out = flowchart(FLOW)
    assert out.startswith("flowchart TD")
    assert 's0((("Start<br/><i>manual on alerts</i>"))):::trigger' in out   # double circle
    assert 's1(("Allowed?<br/><i>decision</i>")):::decision' in out          # circle
    assert '#quot;block#quot;' in out  # quotes in names are escaped
    assert 's1 -->|"No"| s2' in out
    assert "class s3 secret" in out
    assert "calls: Notify SOC On-Call" in out


def test_sequence_participants_and_balance():
    out = sequence(FLOW)
    assert "participant C" in out and "as fortinet-fortigate" in out
    assert "actor A" in out and "as Analyst" in out
    assert "block_ip (Block)" in out and "hard-coded secret" in out
    # every activation (+) is matched by a deactivation (-)
    assert out.count("->>+") == out.count("-->>-")
