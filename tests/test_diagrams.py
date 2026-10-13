from app.diagrams import flowchart, linear, scenarios, sequence

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


def test_linear_numbers_steps_in_order_and_keeps_branch_labels():
    out = linear(FLOW)
    assert out.startswith("flowchart TB")
    assert 's0("<b>1 · Trigger</b><br/>Start<br/><i>manual on alerts</i>"):::trigger' in out
    assert '<b>4 · Connector</b><br/>Block<br/><i>fortinet-fortigate.block_ip</i>' in out
    assert 's1 -->|"No"| s2' in out and "s0 --> s1" in out and "s3 --> s4" in out
    assert out.count("-->") == len(FLOW["steps"]) - 1  # a single chain, no extra links
    assert "class s3 secret" in out


BRANCHY = {
    "name": "Triage", "trigger_type": "record_event", "modules": ["alerts"],
    "steps": [
        {"key": "t", "name": "Start", "kind": "trigger", "next": [{"to": "d", "label": ""}]},
        {"key": "d", "name": "Malicious?", "kind": "decision",
         "next": [{"to": "a", "label": "Yes"}, {"to": "c", "label": "No"}]},
        {"key": "a", "name": "Approve block", "kind": "manual_input",
         "next": [{"to": "b", "label": "Approve"}, {"to": "c", "label": "Reject"}]},
        {"key": "b", "name": "Block", "kind": "connector", "connector": "fortigate", "operation": "block_ip",
         "next": [{"to": "r", "label": ""}]},
        {"key": "r", "name": "Retry?", "kind": "decision",
         "next": [{"to": "c", "label": "Done"}, {"to": "b", "label": "Again"}]},
        {"key": "c", "name": "Close", "kind": "update_record", "next": []},
    ],
}


def test_scenarios_enumerate_every_branch_combination():
    out = scenarios(BRANCHY)
    names = [s["name"] for s in out["scenarios"]]
    assert names == ["Yes → Approve → Done", "Yes → Approve → Again", "Yes → Reject", "No"]
    first = out["scenarios"][0]
    assert first["path"] == [0, 1, 2, 3, 4, 5] and first["end"] == "end" and first["last"] == "Close"
    loop = out["scenarios"][1]
    assert loop["end"] == "loop" and loop["loops_to"] == 3   # "Again" goes back to Block
    assert out["scenarios"][3]["path"] == [0, 1, 5]
    assert "Malicious?: No" in out["scenarios"][3]["summary"]
    assert not out["truncated"]


def test_scenarios_without_branches_is_one_main_path():
    out = scenarios(FLOW)
    assert len(out["scenarios"]) == 1
    assert out["scenarios"][0]["path"] == [0, 1, 2, 3, 4]
    assert out["scenarios"][0]["name"] == "No"  # the single labelled link is still shown as the choice made


def test_direction_and_render():
    from app.diagrams import render
    assert flowchart(FLOW, "LR").startswith("flowchart LR")
    assert linear(FLOW, "horizontal").startswith("flowchart LR")
    assert linear(FLOW, "TB").startswith("flowchart TB")
    out = render(BRANCHY, "scenarios", "LR")
    assert out["mermaid"].startswith("flowchart LR") and len(out["scenarios"]) == 4
    assert render(FLOW, "sequence", "LR")["mermaid"].startswith("sequenceDiagram")


def test_graph_for_3d_matches_drawing_ids():
    from app.diagrams import graph, render
    g = graph(BRANCHY)
    assert [n["id"] for n in g["nodes"]] == [f"s{i}" for i in range(6)]
    assert {"source": "s1", "target": "s2", "label": "Yes"} in g["links"]
    assert {"source": "s4", "target": "s3", "label": "Again"} in g["links"]   # the loop is kept
    chain = graph(BRANCHY, linear_chain=True)["links"]
    assert [(l["source"], l["target"]) for l in chain] == [(f"s{i}", f"s{i + 1}") for i in range(5)]
    assert render(BRANCHY, "scenarios")["graph"]["nodes"][3]["detail"] == "fortigate.block_ip"
    assert "graph" not in render(BRANCHY, "sequence")


CHILD = {
    "name": "Notify SOC", "trigger_type": None, "modules": [],
    "steps": [
        {"key": "a", "name": "Start", "kind": "trigger", "next": [{"to": "b", "label": ""}]},
        {"key": "b", "name": "On call?", "kind": "decision", "next": [{"to": "c", "label": "Yes"}, {"to": "d", "label": "No"}]},
        {"key": "c", "name": "Page", "kind": "connector", "connector": "pagerduty", "operation": "page", "next": []},
        {"key": "d", "name": "Email", "kind": "connector", "connector": "smtp", "operation": "send", "next": []},
    ],
}


def _with_child():
    flow = {**FLOW, "steps": [dict(s) for s in FLOW["steps"]]}
    flow["steps"][4] = {**flow["steps"][4], "child_uuid": "child-1", "next": [{"to": "z", "label": ""}]}
    flow["steps"].append({"key": "z", "name": "Close", "kind": "update_record", "next": []})
    return flow


def test_expand_children_inlines_steps_and_rewires():
    from app.diagrams import expand_children
    out = expand_children(_with_child(), lambda st: CHILD if st.get("child_uuid") == "child-1" else None)
    keys = [s["key"] for s in out["steps"]]
    assert keys == ["a", "b", "c", "d", "e", "e::a", "e::b", "e::c", "e::d", "z"]
    caller = out["steps"][4]
    assert caller["expanded"] and caller["next"] == [{"to": "e::a", "label": ""}]
    assert out["steps"][5]["kind"] == "child_start" and out["steps"][5]["group"] == "Notify SOC"
    assert out["steps"][7]["next"] == [{"to": "z", "label": ""}] and out["steps"][8]["next"] == [{"to": "z", "label": ""}]
    # the child's branch doubles the scenarios: every path now runs through it
    names = [sc["name"] for sc in scenarios(out)["scenarios"]]
    assert len(names) == 2 and all(n.startswith("No") for n in names)
    chart = flowchart(out)
    assert 'subgraph child0["▸ Child playbook: Notify SOC"]' in chart and "s4 --> s5" in chart
    assert "in child: Notify SOC" in linear(out)
    assert "as Playbook: Notify SOC" in sequence(out)


def test_expand_children_stops_on_cycles_and_depth():
    from app.diagrams import expand_children
    loop = {"name": "Loop", "steps": [{"key": "x", "name": "Call me", "kind": "reference", "child_uuid": "loop", "child": "Loop", "next": []}]}
    out = expand_children({**loop, "uuid": "loop"}, lambda st: loop)
    assert [s["key"] for s in out["steps"]] == ["x"]  # calling itself: left as a plain call
    assert expand_children(_with_child(), lambda st: CHILD, depth=0)["steps"] == _with_child()["steps"]
