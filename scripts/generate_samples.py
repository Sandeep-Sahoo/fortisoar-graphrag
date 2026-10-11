"""Generate a synthetic but realistic test corpus under data/samples/.

  python scripts/generate_samples.py

Everything here is made up for testing: playbooks, connector docs (written in the style of
vendor docs, NOT copied from Fortinet), and internal runbooks. Some content deliberately
contains fake secrets / IPs / emails / tenant names to exercise scrubbing, and some internal
docs deliberately don't name their playbook to exercise LLM doc linking.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "samples"
NS = uuid.UUID("6f1c2a52-7d0e-4c55-9a43-1b7d6c1e0f00")

# Keep the UUIDs of the original sample so its CALLS edges still resolve.
FIXED = {
    "Enrich Indicators": "11111111-1111-4111-8111-111111111111",
    "Alert Triage - Phishing": "22222222-2222-4222-8222-222222222222",
}


def uid(name: str) -> str:
    return FIXED.get(name) or str(uuid.uuid5(NS, name))


# ----------------------------------------------------------------- step helpers

def record_event(module: str, **filters) -> dict:
    return {"resource": module, "resources": [module],
            "fieldbasedtrigger": {"filters": [{"field": k, "value": v} for k, v in filters.items()]}}


def manual(*modules: str, button: str = "Execute") -> dict:
    return {"route": str(uuid.uuid4()), "resources": list(modules), "title": button, "executeButtonText": button}


def referenced() -> dict:
    return {"step_variables": {"input": {"records": "{{vars.input.records}}"}}}


def api_endpoint(route: str) -> dict:
    return {"route": route, "authentication_methods": ["api_key"], "resources": []}


def conn(name, connector, operation, version="1.0.0", **params):
    return (name, {"connector": connector, "operation": operation, "version": version, "params": params}, None)


def ref(name, playbook):
    return (name, {"workflowReference": f"/api/3/workflows/{uid(playbook)}",
                   "arguments": {"records": "{{vars.input.records}}"}}, None)


def step(name, step_type, **args):
    return (name, args, step_type)


def playbook(name, description, tags, trigger, steps, branch_labels=None):
    """steps run linearly after the trigger; branch_labels maps a decision step's index
    (Start = 0) -> label of the route leaving it."""
    pb_uuid = uid(name)
    all_steps = [("Start", trigger, None)] + steps
    step_ids = [str(uuid.uuid5(NS, f"{name}/{i}")) for i in range(len(all_steps))]
    out_steps = []
    for (step_name, args, step_type), sid in zip(all_steps, step_ids):
        s = {"@type": "WorkflowStep", "uuid": sid, "name": step_name, "arguments": args}
        if step_type:
            s["stepType"] = {"name": step_type}
        out_steps.append(s)
    routes = []
    for i in range(len(step_ids) - 1):
        r = {"sourceStep": f"/api/3/workflow_steps/{step_ids[i]}", "targetStep": f"/api/3/workflow_steps/{step_ids[i + 1]}"}
        if branch_labels and i in branch_labels:  # label the route leaving the decision step
            r["label"] = branch_labels[i]
        routes.append(r)
    return {"@type": "Workflow", "uuid": pb_uuid, "name": name, "description": description, "isActive": True,
            "recordTags": tags, "triggerStep": f"/api/3/workflow_steps/{step_ids[0]}",
            "steps": out_steps, "routes": routes}


def collection(name, description, workflows):
    return {"type": "workflow_collections",
            "data": [{"@type": "WorkflowCollection", "name": name, "description": description, "workflows": workflows}]}


# ----------------------------------------------------------------- playbooks

COLLECTIONS = {
    "phishing_extended.json": collection("SOC - Phishing Response", "Phishing handling", [
        playbook(
            "Phishing - Analyse Email Attachments",
            "Manually detonates attachments from a reported phishing email in FortiSandbox and enriches embedded URLs.",
            ["Phishing", "Sandbox"], manual("alerts", button="Analyse Attachments"),
            [conn("Submit Attachment to Sandbox", "fortinet-fortisandbox", "submit_file", "3.2.0",
                  file="{{vars.input.records[0].attachment}}"),
             conn("Get Sandbox Verdict", "fortinet-fortisandbox", "get_file_verdict", "3.2.0", sha256="{{vars.steps.Submit_Attachment_to_Sandbox.data.sha256}}"),
             step("Is Attachment Malicious", "Decision",
                  conditions=[{"option": "Malicious", "condition": "{{vars.steps.Get_Sandbox_Verdict.data.rating == 'Malicious'}}"}]),
             ref("Enrich Embedded URLs", "Enrich Indicators"),
             ref("Purge From Mailboxes", "Phishing - Purge Malicious Email")],
            branch_labels={3: "Malicious"},
        ),
        playbook(
            "Phishing - Purge Malicious Email",
            "Searches FortiMail for all copies of a malicious message and deletes them after analyst approval.",
            ["Phishing", "Remediation"], referenced(),
            [conn("Search Message Copies", "fortinet-fortimail", "search_message", "2.1.0",
                  subject="{{vars.input.records[0].emailSubject}}", sender="{{vars.input.records[0].sender}}"),
             step("Approve Purge", "Manual Input", title="Purge {{vars.steps.Search_Message_Copies.data.count}} messages?",
                  owner="SOC Tier 2", timeout_hours=4),
             conn("Delete Messages", "fortinet-fortimail", "delete_message", "2.1.0",
                  message_ids="{{vars.steps.Search_Message_Copies.data.ids}}",
                  api_key="fm-API-7f3a9c2e1b8d4f60a5e2c7b9d1f3a8e6c4b2d0f9"),
             step("Add Purge Note", "Create Record", resource="comments", content="Purged message copies")],
        ),
    ]),
    "malware_endpoint.json": collection("SOC - Malware and Endpoint", "Endpoint detections", [
        playbook(
            "Malware Alert Triage",
            "Triages EDR malware alerts: enriches the file hash and isolates the host when the verdict is malicious.",
            ["Malware", "EDR"], record_event("alerts", type="Malware"),
            [ref("Enrich Hash", "Enrich File Hash"),
             step("Is Hash Malicious", "Decision",
                  conditions=[{"option": "Malicious", "condition": "{{vars.steps.Enrich_Hash.verdict == 'malicious'}}"}]),
             ref("Isolate Affected Host", "Endpoint - Isolate Host"),
             ref("Open ServiceNow Ticket", "Create ServiceNow Incident")],
            branch_labels={2: "Malicious"},
        ),
        playbook(
            "Enrich File Hash",
            "Looks up a SHA256 in VirusTotal and FortiSandbox and returns a combined verdict.",
            ["Enrichment", "Subroutine"], referenced(),
            [conn("VirusTotal File Lookup", "virustotal", "get_file_reputation", "2.5.0", hash="{{vars.input.records[0].fileHash}}"),
             conn("FortiSandbox Hash Lookup", "fortinet-fortisandbox", "get_file_verdict", "3.2.0", sha256="{{vars.input.records[0].fileHash}}"),
             step("Compute Verdict", "Set Variable", verdict="{{'malicious' if vars.steps.VirusTotal_File_Lookup.data.positives > 5 else 'unknown'}}")],
        ),
        playbook(
            "Endpoint - Isolate Host",
            "Network-contains a host in CrowdStrike Falcon after Tier 2 approval and records the action on the alert.",
            ["EDR", "Containment"], manual("alerts", "assets", button="Isolate Host"),
            [step("Approve Isolation", "Manual Input", title="Isolate {{vars.input.records[0].hostname}}?", owner="SOC Tier 2"),
             conn("Contain Host", "crowdstrike-falcon", "contain_host", "2.3.0", device_id="{{vars.input.records[0].deviceId}}"),
             step("Update Alert Status", "Update Record", status="Contained"),
             ref("Notify On-Call", "Notify SOC On-Call")],
        ),
    ]),
    "identity.json": collection("SOC - Identity", "Identity and access threats", [
        playbook(
            "Identity - Impossible Travel Triage",
            "Checks Entra ID sign-in logs and IP reputation for impossible-travel alerts; disables the account on high risk.",
            ["Identity", "Entra ID"], record_event("alerts", type="Impossible Travel"),
            [conn("Get Sign-in Logs", "microsoft-entra-id", "get_sign_in_logs", "1.4.0", user="{{vars.input.records[0].userPrincipalName}}"),
             conn("Check Source IP", "abuseipdb", "check_ip", "1.2.0", ip="{{vars.input.records[0].sourceIp}}"),
             step("High Risk?", "Decision",
                  conditions=[{"option": "High", "condition": "{{vars.steps.Check_Source_IP.data.abuseConfidenceScore > 75}}"}]),
             ref("Disable Account", "Disable User Account")],
            branch_labels={3: "High"},
        ),
        playbook(
            "Disable User Account",
            "Disables an AD account and revokes Entra ID sessions after approval, then notifies the user's manager.",
            ["Identity", "Containment", "Subroutine"], referenced(),
            [step("Approve Disable", "Manual Input", title="Disable {{vars.input.records[0].userPrincipalName}}?", owner="SOC Tier 2"),
             conn("Disable AD User", "active-directory", "disable_user", "3.0.1", username="{{vars.input.records[0].samAccountName}}"),
             conn("Revoke Sessions", "microsoft-entra-id", "revoke_sessions", "1.4.0", user="{{vars.input.records[0].userPrincipalName}}"),
             conn("Email Manager", "smtp", "send_email", "2.0.0", to="{{vars.input.records[0].managerEmail}}",
                  cc="iam-team@acme-tenant.example")],
        ),
        playbook(
            "Brute Force Login Response",
            "Correlates failed logins in Splunk, scores the source IP and blocks it on the FortiGate when abusive.",
            ["Identity", "Brute Force"], record_event("alerts", type="Brute Force"),
            [conn("Count Failed Logins", "splunk", "run_search", "2.2.0",
                  query="index=auth action=failure src={{vars.input.records[0].sourceIp}} | stats count"),
             conn("Score Source IP", "abuseipdb", "check_ip", "1.2.0", ip="{{vars.input.records[0].sourceIp}}"),
             ref("Block Source IP", "Block IP Address")],
        ),
    ]),
    "network.json": collection("SOC - Network", "Network blocking", [
        playbook(
            "Block IP Address",
            "Adds an IP to the FortiGate blocked address group unless it is on the allow list (jump host 10.20.30.40 is never blocked).",
            ["Network", "Containment", "Subroutine"], referenced(),
            [step("Check Allow List", "Decision",
                  conditions=[{"option": "Allowed", "condition": "{{vars.input.records[0].ip in ['10.20.30.40', '192.168.10.5']}}"}]),
             conn("Block on FortiGate", "fortinet-fortigate", "block_ip", "4.1.0", ip="{{vars.input.records[0].ip}}",
                  address_group="SOAR-Blocked", password="Fg-Adm1n-2026!"),
             step("Tag Indicator", "Update Record", tags=["Blocked"])],
        ),
        playbook(
            "Block Domain",
            "Blocks a domain in Cisco Umbrella and adds it to the FortiGate web filter.",
            ["Network", "Containment", "Subroutine"], referenced(),
            [conn("Umbrella Block", "cisco-umbrella", "block_domain", "1.1.0", domain="{{vars.input.records[0].domain}}"),
             conn("FortiGate URL Filter", "fortinet-fortigate", "block_url", "4.1.0", url="{{vars.input.records[0].domain}}")],
        ),
        playbook(
            "Webhook - External IOC Submission",
            "API endpoint for partner teams to submit IOCs; routes IPs and domains to the blocking playbooks.",
            ["Threat Intel", "API"], api_endpoint("ioc-submit"),
            [step("Create Indicator Records", "Create Record", resource="indicators", body="{{vars.request.data}}"),
             ref("Block Submitted IPs", "Block IP Address"),
             ref("Block Submitted Domains", "Block Domain")],
        ),
        playbook(
            "Threat Intel Feed - IOC Ingestion",
            "Pulls the partner threat feed hourly and creates indicator records for new IOCs.",
            ["Threat Intel", "Ingestion"], referenced(),
            [conn("Fetch Feed", "http-request", "get", "1.0.0", url="https://feeds.acme-tenant.example/iocs",
                  headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.ZmVlZC10b2tlbg.c2lnbmF0dXJl"}),
             step("Create Indicators", "Create Record", resource="indicators")],
        ),
    ]),
    "utilities.json": collection("SOC - Utilities", "Shared helper playbooks", [
        playbook(
            "Create ServiceNow Incident",
            "Opens a ServiceNow incident linked to the FortiSOAR alert and stores the ticket number.",
            ["Ticketing", "Subroutine"], referenced(),
            [conn("Create Incident", "servicenow", "create_record", "2.4.0", table="incident",
                  short_description="{{vars.input.records[0].name}}"),
             step("Store Ticket Number", "Update Record", ticket="{{vars.steps.Create_Incident.data.number}}")],
        ),
        playbook(
            "Notify SOC On-Call",
            "Emails and opens a Jira ticket for the SOC on-call engineer.",
            ["Notification", "Subroutine"], referenced(),
            [conn("Email On-Call", "smtp", "send_email", "2.0.0", to="soc-oncall@acme-tenant.example"),
             conn("Create Jira Issue", "jira", "create_issue", "3.1.0", project="SOC", issue_type="Task")],
        ),
        playbook(
            "Escalate Critical Incident",
            "When an incident becomes Critical, opens a ServiceNow P1 and pages the on-call engineer.",
            ["Escalation"], record_event("incidents", severity="Critical"),
            [ref("Open P1 Ticket", "Create ServiceNow Incident"),
             ref("Page On-Call", "Notify SOC On-Call")],
        ),
        playbook(
            "Daily SLA Breach Report",
            "Finds alerts that breached SLA in the last 24h and emails a summary to SOC management.",
            ["Reporting", "SLA"], referenced(),
            [step("Find Breached Alerts", "Find Records", resource="alerts", query="slaStatus eq 'Breached'"),
             conn("Email Report", "smtp", "send_email", "2.0.0", to="soc-managers@acme-tenant.example")],
        ),
    ]),
}

# Ransomware and cloud (AWS) scenarios. They reuse the shared subroutines above, so the call
# graph crosses collections (e.g. ransomware containment -> Endpoint - Isolate Host).
COLLECTIONS.update({
    "ransomware.json": collection("SOC - Ransomware", "Ransomware detection, containment and recovery", [
        playbook(
            "Ransomware - Detection Triage",
            "Triages FortiEDR anti-ransomware events: confirms encryption activity, opens a P1 ticket, pages on-call and starts containment.",
            ["Ransomware", "EDR"], record_event("alerts", type="Ransomware"),
            [conn("Get FortiEDR Event", "fortinet-fortiedr", "get_event", "2.0.0", event_id="{{vars.input.records[0].sourceId}}"),
             step("Encryption Confirmed?", "Decision",
                  conditions=[{"option": "Confirmed", "condition": "{{vars.steps.Get_FortiEDR_Event.data.classification == 'Malicious'}}"}]),
             ref("Open P1 Ticket", "Create ServiceNow Incident"),
             ref("Page On-Call", "Notify SOC On-Call"),
             ref("Start Containment", "Ransomware - Contain Outbreak")],
            branch_labels={2: "Confirmed"},
        ),
        playbook(
            "Ransomware - Contain Outbreak",
            "After incident-commander approval, isolates the first host, disables the user, quarantines agentless servers via FortiNAC and maps the blast radius.",
            ["Ransomware", "Containment"], referenced(),
            [step("Incident Commander Approval", "Manual Input", title="Contain ransomware on {{vars.input.records[0].hostname}}?",
                  owner="Incident Commander", timeout_hours=1),
             ref("Isolate First Host", "Endpoint - Isolate Host"),
             ref("Disable Affected User", "Disable User Account"),
             step("Has EDR Agent?", "Decision",
                  conditions=[{"option": "No agent", "condition": "{{vars.input.records[0].edrAgent == false}}"}]),
             conn("Quarantine Server via NAC", "fortinet-fortinac", "quarantine_host", "1.3.0",
                  ip_address="{{vars.input.records[0].sourceIp}}"),
             ref("Map Blast Radius", "Ransomware - Identify Blast Radius")],
            branch_labels={4: "No agent"},
        ),
        playbook(
            "Ransomware - Identify Blast Radius",
            "Hunts the encryptor hash across all endpoints and searches Splunk for SMB lateral movement from the first host.",
            ["Ransomware", "Hunting"], referenced(),
            [conn("Hunt Encryptor Hash", "fortinet-fortiedr", "hunt_hash", "2.0.0", sha256="{{vars.input.records[0].fileHash}}", days=7),
             conn("Search SMB Lateral Movement", "splunk", "run_search", "2.2.0",
                  query="index=network dest_port=445 src={{vars.input.records[0].sourceIp}} earliest=-24h | stats count by dest"),
             step("Merge Affected Hosts", "Set Variable",
                  affected="{{vars.steps.Hunt_Encryptor_Hash.data.devices + vars.steps.Search_SMB_Lateral_Movement.data.results}}"),
             step("Link Affected Assets", "Update Record", assets="{{vars.affected}}")],
        ),
        playbook(
            "Ransomware - Verify Backups",
            "Lists restore points for affected systems, flags immutable points created before the infection, and raises a change request for the restore.",
            ["Ransomware", "Recovery"], manual("incidents", button="Verify Backups"),
            [conn("Get Restore Points", "veeam-backup", "get_restore_points", "1.2.0",
                  vm_name="{{vars.input.records[0].assetName}}", password="V33am-Svc-Pass!"),
             step("Clean Restore Point Available?", "Decision",
                  conditions=[{"option": "Available", "condition": "{{vars.steps.Get_Restore_Points.data | selectattr('immutable') | list | length > 0}}"}]),
             conn("Raise Restore Change Request", "servicenow", "create_record", "2.4.0", table="change_request"),
             ref("Notify Recovery Team", "Notify SOC On-Call")],
            branch_labels={2: "Available"},
        ),
        playbook(
            "Ransomware - Collect Ransom Note IOCs",
            "Records the ransom note hash and payment/leak-site domains as indicators and blocks the domains.",
            ["Ransomware", "Threat Intel"], referenced(),
            [conn("Check Note Hash", "virustotal", "get_file_reputation", "2.5.0", hash="{{vars.input.records[0].noteHash}}"),
             step("Create Note Indicators", "Create Record", resource="indicators"),
             ref("Block Leak-Site Domains", "Block Domain")],
        ),
    ]),
    "cloud_aws.json": collection("SOC - Cloud AWS", "AWS threat detection and response", [
        playbook(
            "AWS GuardDuty Finding Triage",
            "Enriches GuardDuty findings and routes credential compromise to key disablement and instance compromise to EC2 isolation.",
            ["AWS", "GuardDuty"], record_event("alerts", source="AWS GuardDuty"),
            [conn("Get Finding Details", "aws-guardduty", "get_findings", "1.1.0", finding_ids=["{{vars.input.records[0].sourceId}}"]),
             step("High Severity?", "Decision",
                  conditions=[{"option": "High", "condition": "{{vars.steps.Get_Finding_Details.data.severity >= 7}}"}]),
             step("Finding Type?", "Decision",
                  conditions=[{"option": "Credentials", "condition": "{{'IAMUser' in vars.steps.Get_Finding_Details.data.type}}"},
                              {"option": "Instance", "condition": "{{'EC2' in vars.steps.Get_Finding_Details.data.type}}"}]),
             ref("Disable Leaked Key", "AWS - Disable Access Key"),
             ref("Isolate Instance", "AWS - Isolate EC2 Instance")],
            branch_labels={2: "High", 3: "Credentials"},
        ),
        playbook(
            "AWS - Isolate EC2 Instance",
            "Snapshots all volumes for forensics, swaps the instance into the quarantine security group and tags it, after approval for production.",
            ["AWS", "EC2", "Containment", "Subroutine"], referenced(),
            [step("Approve Isolation", "Manual Input", title="Isolate {{vars.input.records[0].instanceId}}?", owner="Cloud On-Call"),
             conn("Describe Instance", "aws-ec2", "describe_instance", "2.0.0", instance_id="{{vars.input.records[0].instanceId}}"),
             conn("Snapshot Volumes", "aws-ec2", "create_snapshot", "2.0.0", volume_ids="{{vars.steps.Describe_Instance.data.volumes}}"),
             conn("Apply Quarantine Security Group", "aws-ec2", "modify_instance_security_groups", "2.0.0",
                  instance_id="{{vars.input.records[0].instanceId}}", groups=["sg-quarantine"]),
             conn("Tag Instance", "aws-ec2", "tag_instance", "2.0.0", tags={"SecurityStatus": "Quarantined"}),
             ref("Notify On-Call", "Notify SOC On-Call")],
        ),
        playbook(
            "AWS - Disable Access Key",
            "Deactivates a leaked IAM access key after approval, attaches a deny-all policy and emails the key owner.",
            ["AWS", "IAM", "Containment", "Subroutine"], referenced(),
            [conn("List Access Keys", "aws-iam", "list_access_keys", "1.5.0", user_name="{{vars.input.records[0].userName}}"),
             step("Approve Key Disable", "Manual Input", title="Deactivate key {{vars.input.records[0].accessKeyId}}?", owner="Cloud On-Call"),
             conn("Deactivate Key", "aws-iam", "update_access_key", "1.5.0", access_key_id="{{vars.input.records[0].accessKeyId}}", status="Inactive"),
             conn("Attach Deny-All Policy", "aws-iam", "attach_user_policy", "1.5.0",
                  policy_arn="arn:aws:iam::123456789012:policy/DenyAll"),
             conn("Email Key Owner", "smtp", "send_email", "2.0.0", to="{{vars.input.records[0].ownerEmail}}")],
        ),
        playbook(
            "AWS - Public S3 Bucket Remediation",
            "Re-enables S3 public access block on unexpectedly public buckets (except allow-listed website buckets) and tickets the owner.",
            ["AWS", "S3"], record_event("alerts", type="S3 Public Access"),
            [step("Allow-Listed Bucket?", "Decision",
                  conditions=[{"option": "Allowed", "condition": "{{vars.input.records[0].bucket in ['customer-portal-static']}}"}]),
             conn("Get Bucket Policy", "aws-s3", "get_bucket_policy", "1.3.0", bucket="{{vars.input.records[0].bucket}}"),
             conn("Enable Public Access Block", "aws-s3", "put_public_access_block", "1.3.0", bucket="{{vars.input.records[0].bucket}}"),
             step("Mark Remediated", "Update Record", status="Remediated"),
             ref("Ticket Bucket Owner", "Create ServiceNow Incident")],
        ),
        playbook(
            "AWS - CloudTrail Disabled Response",
            "Restarts CloudTrail logging immediately, finds who stopped it and contains that principal's keys.",
            ["AWS", "CloudTrail"], record_event("alerts", type="CloudTrail Disabled"),
            [conn("Check Trail Status", "aws-cloudtrail", "get_trail_status", "1.2.0", trail="org-trail"),
             conn("Restart Logging", "aws-cloudtrail", "start_logging", "1.2.0", trail="org-trail",
                  aws_access_key_id="AKIAIOSFODNN7EXAMPLE", aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
             conn("Find Who Stopped It", "aws-cloudtrail", "lookup_events", "1.2.0", event_name="StopLogging"),
             ref("Disable Their Key", "AWS - Disable Access Key"),
             ref("Escalate to On-Call", "Notify SOC On-Call")],
        ),
        playbook(
            "AWS - Root Account Usage Alert",
            "Looks up root sign-in events and pages on-call unless the account owner confirms the login within 15 minutes.",
            ["AWS", "IAM"], record_event("alerts", type="Root Login"),
            [conn("Lookup Root Sign-ins", "aws-cloudtrail", "lookup_events", "1.2.0", event_name="ConsoleLogin", user_name="root"),
             step("Owner Confirms Login", "Manual Input", title="Did you sign in as root to {{vars.input.records[0].accountName}}?",
                  owner="Account Owner", timeout_hours=0.25),
             step("Confirmed?", "Decision", conditions=[{"option": "Not confirmed", "condition": "{{vars.steps.Owner_Confirms_Login.input.answer != 'Yes'}}"}]),
             ref("Page On-Call", "Notify SOC On-Call")],
            branch_labels={3: "Not confirmed"},
        ),
    ]),
})


# ----------------------------------------------------------------- vendor-style docs (synthetic)

DISCLAIMER = "> Synthetic test document written for this project. Not official Fortinet documentation.\n\n"

VENDOR_DOCS = {
    "playbook_triggers.md": """# Playbook triggers

A playbook starts from a trigger step. FortiSOAR supports several trigger types.

**On Create / On Update** triggers run when a record in a module (for example Alerts or Incidents) is created or updated. Field-based conditions restrict which records start the playbook, such as `type equals Phishing`.

**Manual** triggers add a button to the record view. An analyst selects one or more records and clicks the button. Manual triggers are used for actions that need human judgement, like isolating a host.

**Referenced** playbooks have no trigger of their own. They are called from other playbooks with the Reference a Playbook step and receive input through `vars.input.records`.

**Custom API Endpoint** triggers expose a playbook as an HTTP endpoint so external systems can start it. Choose an authentication method (API key or basic auth) for every endpoint.""",
    "reference_a_playbook.md": """# Reference a Playbook step

The Reference a Playbook step calls another playbook (a child) from the current one (the parent). Use it to build reusable subroutines such as enrichment, blocking, or ticket creation.

The parent passes records to the child as `vars.input.records`. The child's final step output is available to the parent as the step result.

Changing a child playbook affects every parent that references it. Before editing a shared child, list its callers and test them. A referenced playbook that is deactivated causes parent executions to fail at that step.""",
    "manual_input_and_approvals.md": """# Manual Input and approvals

The Manual Input step pauses a playbook and asks a user or team for a decision. It is commonly used as an approval gate before disruptive actions such as blocking, deleting email, or containing a host.

Configure the owner (a user or team), the input fields, and an optional timeout. When the timeout expires the playbook follows the timeout path, so always define what happens if nobody responds.

Pending manual inputs appear in the Pending Decisions view and can be answered from email if email approvals are enabled.""",
    "decision_step.md": """# Decision step

The Decision step branches a playbook based on conditions. Each condition is a Jinja expression that evaluates to true or false. The first matching condition's route is followed; add a default route for when no condition matches.

Typical uses: route on a verdict (malicious or clean), on severity, or on whether an indicator is on an allow list.""",
    "jinja_in_playbooks.md": """# Using Jinja in playbooks

Step parameters accept Jinja expressions. `vars.input.records` holds the triggering records. `vars.steps.<Step_Name>` holds the output of earlier steps, with spaces in step names replaced by underscores.

Filters such as `| default`, `| length`, and `| json_query` help shape data. Keep complex logic in a Set Variable step so it can be inspected in the execution history.""",
    "connectors_overview.md": """# Connectors overview

Connectors integrate FortiSOAR with external products. Each connector exposes operations (actions) that playbooks call through connector steps, for example `block_ip` on a firewall connector or `get_file_reputation` on a threat intel connector.

A connector can have several configurations (for example one per firewall). Credentials are stored in the configuration, not in playbooks. Rotate credentials in the configuration and avoid hard-coding secrets in step parameters.

Connector versions matter: upgrading a connector can rename or change operation parameters, so test playbooks that use it after an upgrade.""",
    "fortinet_fortisandbox_connector.md": """# Fortinet FortiSandbox connector

The FortiSandbox connector submits files and URLs for dynamic analysis and retrieves verdicts.

Operations include `submit_file`, `submit_url`, and `get_file_verdict`. Verdicts are returned as ratings such as Clean, Low Risk, Medium Risk, High Risk, or Malicious. Submission is asynchronous: poll `get_file_verdict` or add a wait step before reading the result.""",
    "fortinet_fortimail_connector.md": """# Fortinet FortiMail connector

The FortiMail connector searches and remediates email. `search_message` finds messages by subject, sender, or message ID across mailboxes. `delete_message` removes the found copies.

Because deletion cannot be undone, combine `delete_message` with a Manual Input approval and record what was deleted on the alert.""",
    "fortinet_fortigate_connector.md": """# Fortinet FortiGate connector

The FortiGate connector manages firewall objects. `block_ip` adds an address to a blocked address group, `unblock_ip` removes it, and `block_url` adds a URL or domain to the web filter.

Use an allow-list check before blocking so that critical infrastructure is never blocked. The connector needs an API user with write access to address objects.""",
    "virustotal_connector.md": """# VirusTotal connector

The VirusTotal connector returns reputation for files, URLs, domains, and IPs. `get_file_reputation` takes a hash and returns detection counts (`positives`) from antivirus engines. `get_reputation_of_url` does the same for URLs.

Public API keys are rate limited. Cache results on indicator records to avoid repeated lookups.""",
    "abuseipdb_connector.md": """# AbuseIPDB connector

The AbuseIPDB connector scores IP addresses. `check_ip` returns an `abuseConfidenceScore` from 0 to 100 and recent report categories. Scores above 75 usually indicate a known abusive source.""",
    "crowdstrike_falcon_connector.md": """# CrowdStrike Falcon connector

The CrowdStrike Falcon connector manages endpoints. `contain_host` network-isolates a device so it can only talk to the Falcon cloud; `lift_containment` reverses it. `get_device_details` returns hostname, OS, and last-seen time.

Containment disrupts the user, so gate it behind an approval for production endpoints.""",
    "microsoft_entra_id_connector.md": """# Microsoft Entra ID connector

The Microsoft Entra ID connector reads and changes identity data. `get_sign_in_logs` returns recent sign-ins with location and risk details. `revoke_sessions` invalidates refresh tokens so the user must sign in again. `disable_user` blocks sign-in.""",
    "servicenow_connector.md": """# ServiceNow connector

The ServiceNow connector creates and updates ITSM records. `create_record` creates a record in a table such as `incident`, and returns its number and sys_id. Store the number on the FortiSOAR record so the two stay linked.""",
    "execution_history_and_debugging.md": """# Execution history and debugging

Every playbook run is recorded in the execution history with each step's input, output, and duration. Failed steps show the error returned by the connector.

To debug, open the failed run, inspect the step's rendered parameters, and re-run the playbook from the failed step after fixing the cause. Jinja errors usually mean a referenced step name or field does not exist.""",
}

# ----------------------------------------------------------------- internal docs (synthetic)

INTERNAL_DOCS = {
    "malware_response_runbook.md": """# Malware response runbook

Scope: EDR malware alerts.

The Malware Alert Triage playbook runs automatically when an alert of type Malware is created. It enriches the hash, and when the verdict is malicious it calls Endpoint - Isolate Host, which waits for Tier 2 approval before containing the device in CrowdStrike.

If the playbook fails at the enrichment step, check the VirusTotal API quota first. Escalate to the on-call engineer if a server (not a workstation) needs isolation.""",
    "host_isolation_policy.md": """# Host isolation approval policy

Only Tier 2 analysts or above may approve isolating an endpoint. Domain controllers, payment servers, and vessel communication gateways must never be isolated without the SOC manager's approval.

The Endpoint - Isolate Host playbook enforces the Tier 2 approval with a Manual Input step. Record the business justification in the approval comment.""",
    "identity_compromise_runbook.md": """# Identity compromise runbook

Impossible-travel alerts are handled by Identity - Impossible Travel Triage. High-risk sign-ins (AbuseIPDB score above 75) lead to the Disable User Account playbook, which disables the AD account and revokes Entra ID sessions after approval.

After containment, the IAM team (iam-team@acme-tenant.example) resets the password and reviews MFA methods with the user.""",
    "brute_force_procedure.md": """# Brute force procedure

Repeated failed logins from one source are handled automatically. Splunk is queried for the failure count, the source IP is scored, and abusive sources are blocked on the perimeter firewall by the shared blocking playbook.

Never block the corporate jump host 10.20.30.40. It is on the allow list for this reason.""",
    "email_purge_procedure.md": """# Removing a malicious email from all mailboxes

When an analyst confirms a phishing email is malicious, every copy must be removed from user mailboxes. The automation searches the mail gateway for copies by subject and sender, asks for approval, then deletes them and adds a note to the alert.

Deletion cannot be undone. Double-check the subject line before approving.""",
    "attachment_analysis_guide.md": """# Attachment analysis guide

Reported phishing emails with attachments should be detonated before any decision. Analysts start the analysis from the alert using the Analyse Attachments button. The sandbox verdict decides whether embedded URLs get enriched and whether the purge is started.""",
    "escalation_matrix.md": """# SOC escalation matrix

| Severity | Response | Automation |
|---|---|---|
| Critical | Page on-call within 15 minutes, ServiceNow P1 | Escalate Critical Incident |
| High | Tier 2 within 1 hour | Notify SOC On-Call |
| Medium | Tier 1 within 4 hours | none |

The on-call mailbox is soc-oncall@acme-tenant.example.""",
    "ioc_submission_guide.md": """# How partner teams submit IOCs

Partner teams can submit IPs and domains for blocking through the SOAR webhook. Send a POST to the ioc-submit endpoint with an API key from the SOC. Submitted IPs and domains are blocked automatically, so only submit confirmed malicious indicators.

Test key for the staging tenant: api_key=stg-9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f2e1d0c""",
    "threat_feed_operations.md": """# Threat feed operations

The hourly partner feed is ingested by Threat Intel Feed - IOC Ingestion. If indicators stop appearing, check that the feed token has not expired and that the feed host is reachable from the FortiSOAR appliance at 172.16.40.12.""",
    "sla_reporting.md": """# SLA reporting

SOC managers receive a daily email listing alerts that breached SLA in the previous 24 hours. The report is produced by Daily SLA Breach Report and sent to soc-managers@acme-tenant.example.""",
    "playbook_change_management.md": """# Playbook change management

Shared subroutine playbooks (Block IP Address, Enrich Indicators, Create ServiceNow Incident, Notify SOC On-Call) are used by many other playbooks. Before changing one, list every playbook that calls it and test each one in staging.

All playbook changes need a peer review and a change ticket. Export the collection before and after the change.""",
    "known_issues.md": """# Known issues and troubleshooting

- The FortiSandbox verdict is sometimes empty because the submission has not finished. The attachment analysis automation should wait before reading the verdict.
- ServiceNow ticket creation fails when the alert name is longer than 160 characters.
- Disabling accounts fails for cloud-only users because they have no on-premises AD object; revoke sessions still works.""",
}

# ----------------------------------------------------------------- evaluation questions

EVAL_QUESTIONS = """# Evaluation questions

Use these to check retrieval and answers. "Expect" lists the sources a good answer should cite.

## Company-specific
1. What happens automatically when a malware alert is created? Expect: Malware Alert Triage, Enrich File Hash, Endpoint - Isolate Host, malware runbook.
2. Who can approve isolating a host, and which playbook enforces it? Expect: Endpoint - Isolate Host, host isolation policy.
3. Which playbooks call Block IP Address? Expect: Brute Force Login Response, Webhook - External IOC Submission.
4. If I change Create ServiceNow Incident, what else could break? Expect: Malware Alert Triage, Escalate Critical Incident, change management doc.
5. How do we remove a phishing email from every mailbox? Expect: Phishing - Purge Malicious Email, email purge procedure.
6. Which connectors does the identity response use? Expect: microsoft-entra-id, abuseipdb, active-directory, smtp.
7. Why is 10.20.30.40 never blocked? Expect: Block IP Address, brute force procedure (scrubbed to <IP> in stored text).
8. How do partner teams submit IOCs? Expect: Webhook - External IOC Submission, IOC submission guide.
9. What runs when an incident becomes Critical? Expect: Escalate Critical Incident, escalation matrix.
10. Why might disabling an account fail? Expect: Disable User Account, known issues.

## Vendor-focused
11. What trigger types can a FortiSOAR playbook have? Expect: playbook triggers doc.
12. How does a child playbook receive input from a parent? Expect: reference a playbook doc.
13. What happens when a Manual Input step times out? Expect: manual input doc.
14. How do I reference the output of an earlier step in Jinja? Expect: Jinja doc.
15. What does the FortiSandbox verdict rating mean and why can it be empty? Expect: FortiSandbox doc, known issues.

## Mixed
16. Do we use FortiSandbox anywhere, and how? Expect: Enrich File Hash, Phishing - Analyse Email Attachments, FortiSandbox doc.
17. Which of our playbooks have approval gates? Expect: Endpoint - Isolate Host, Disable User Account, Phishing - Purge Malicious Email.
18. Where are connector credentials supposed to live, and do any playbooks break that rule? Expect: connectors overview; Block IP Address and Purge have hard-coded secrets (shown as <REDACTED>).

## Ransomware
19. What happens automatically when FortiEDR detects ransomware? Expect: Ransomware - Detection Triage, ransomware response plan.
20. Who approves ransomware containment and what does containment do? Expect: Ransomware - Contain Outbreak, response plan section 4.
21. How do we contain a file server that has no EDR agent? Expect: Contain Outbreak (FortiNAC quarantine branch), FortiNAC connector doc, tabletop lessons learned.
22. How do we know which backups are safe to restore from? Expect: Ransomware - Verify Backups, backup recovery procedure, Veeam doc.
23. What should we never do to an encrypted machine? Expect: response plan ("do not power off").

## Cloud (AWS)
24. What happens when GuardDuty reports leaked credentials? Expect: AWS GuardDuty Finding Triage, AWS - Disable Access Key, AWS IR runbook.
25. Why should we snapshot before isolating an EC2 instance, and what about Auto Scaling groups? Expect: AWS - Isolate EC2 Instance, EC2 connector doc, AWS IR runbook.
26. Someone stopped CloudTrail. What does our automation do? Expect: AWS - CloudTrail Disabled Response, CloudTrail doc, cloud security baseline.
27. Which S3 bucket is allowed to be public? Expect: AWS - Public S3 Bucket Remediation, account inventory, baseline.
28. Which playbooks are shared between ransomware and AWS response? Expect: Notify SOC On-Call, Create ServiceNow Incident (use find_dependents / run_cypher).
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("playbooks", "vendor_docs", "internal_docs"):
        (OUT / sub).mkdir(parents=True)

    shutil.copy(ROOT / "data" / "playbooks" / "sample_collection.json", OUT / "playbooks" / "phishing_core.json")
    for filename, coll in COLLECTIONS.items():
        (OUT / "playbooks" / filename).write_text(json.dumps(coll, indent=2))
    from sample_docs_extended import INTERNAL_DOCS_EXT, VENDOR_DOCS_EXT

    VENDOR_DOCS.update(VENDOR_DOCS_EXT)
    INTERNAL_DOCS.update(INTERNAL_DOCS_EXT)
    for filename, body in VENDOR_DOCS.items():
        (OUT / "vendor_docs" / filename).write_text(DISCLAIMER + body + "\n")
    for filename, body in INTERNAL_DOCS.items():
        (OUT / "internal_docs" / filename).write_text("> Synthetic test document.\n\n" + body + "\n")
    (OUT / "eval_questions.md").write_text(EVAL_QUESTIONS)

    n_pb = 2 + sum(len(c["data"][0]["workflows"]) for c in COLLECTIONS.values())
    print(f"wrote {n_pb} playbooks in {len(COLLECTIONS) + 1} collections, "
          f"{len(VENDOR_DOCS)} vendor docs, {len(INTERNAL_DOCS)} internal docs to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
