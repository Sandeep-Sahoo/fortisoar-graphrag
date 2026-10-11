"""Longer, multi-section synthetic documents for the ransomware and AWS test scenarios.

Each document is several chunks long, so retrieval has to find the right section rather than
the whole file. All content is invented for testing: connector docs are written in the style of
vendor documentation (not copied from any vendor), and internal docs describe a fictional SOC.
"""

VENDOR_DOCS_EXT = {
    "fortinet_fortiedr_connector.md": """# Fortinet FortiEDR connector

## Overview

The FortiEDR connector lets playbooks investigate and respond to endpoint threats detected by FortiEDR. It covers event retrieval, threat hunting across the fleet, and device-level response actions. It is commonly used in ransomware workflows because FortiEDR can block encryption behaviour in real time and report which processes attempted it.

## Configuration

Create one configuration per FortiEDR management console. You need the console hostname, an organisation name (for multi-tenant consoles), and an API user with the "Rest API" role. Store the API user's credentials in the connector configuration; do not pass them as step parameters.

The connector verifies the console certificate by default. Disable verification only in lab environments.

## Operations

### get_event
Returns full details of a security event: the triggering process, its hash, the device, the user, the rule that fired, and the action FortiEDR took (Block, Log, or Simulation). Use it as the first enrichment step on any FortiEDR alert.

Parameters: `event_id` (required).

### hunt_hash
Searches every managed endpoint for a file hash and returns the devices where it was seen, with first-seen and last-seen times. This is the main operation for answering "how far has it spread?" during a ransomware outbreak.

Parameters: `sha256` (required), `days` (optional, default 30).

### isolate_device
Network-isolates a device so it can only talk to the FortiEDR console. Isolation survives reboots. Use `remove_isolation` to undo it.

Parameters: `device_id` (required).

### get_device
Returns operating system, last-seen time, logged-in users and isolation state for a device.

## Ransomware considerations

FortiEDR's anti-ransomware rules raise events with the classification "Malicious" and rule names containing "Ransomware" or "File Encryptor". When the policy is in Prevention mode, the encrypting process has usually been terminated already. In Simulation mode only a log entry is created, so the playbook must contain the device itself.

Hunting the hash of the encryptor is fast, but attackers often recompile per victim. Combine `hunt_hash` with a search for the ransom note file name across endpoints for better coverage.

## Troubleshooting

- `401 Unauthorized`: the API user is missing the Rest API role or the organisation name is wrong.
- `hunt_hash` returns nothing for a hash you know is present: the endpoint may have been offline for longer than the `days` window.
- Isolation succeeds but the device still communicates: the collector may be outdated; check `get_device` for the collector version.
""",
    "fortinet_fortinac_connector.md": """# Fortinet FortiNAC connector

## Overview

The FortiNAC connector controls network access for hosts. Playbooks use it to move a suspicious host into a quarantine VLAN without needing an EDR agent on the host, which makes it useful for servers, OT devices, and machines whose agent has been tampered with.

## Operations

### quarantine_host
Marks a host as "At Risk" and moves it to the quarantine VLAN defined in the FortiNAC isolation policy. Parameters: `mac_address` or `ip_address`.

### release_host
Clears the At Risk state and returns the host to its normal VLAN.

### get_host
Returns the host's switch port, VLAN, last-seen time and registration owner.

## Notes

Quarantine depends on the switch being managed by FortiNAC. For hosts on unmanaged switches the operation reports success but the VLAN does not change; confirm with `get_host` after quarantining. Quarantining a hypervisor quarantines every virtual machine behind its uplink, so exclude hypervisors in the playbook.
""",
    "aws_guardduty_connector.md": """# AWS GuardDuty connector

## Overview

Amazon GuardDuty continuously analyses CloudTrail management events, VPC flow logs, DNS logs and S3 data events to detect threats in an AWS account. The GuardDuty connector lets FortiSOAR ingest findings as alerts and enrich them during triage.

## Configuration

The connector authenticates with an IAM role that FortiSOAR assumes (recommended) or with an access key pair. For organisations with many accounts, configure the connector against the GuardDuty delegated administrator account; it can read findings for every member account.

The minimum permissions are `guardduty:ListFindings`, `guardduty:GetFindings`, and `guardduty:ArchiveFindings` if the playbook archives findings after triage.

## Operations

### get_findings
Returns full finding details for one or more finding IDs: type, severity (0.1 to 8.9), affected resource (instance, access key, or S3 bucket), the actor's IP and geolocation, and the first and last seen times.

### list_findings
Lists finding IDs filtered by severity, type, or update time. Data ingestion uses this on a schedule.

### archive_findings
Archives findings after they are resolved so they do not reappear in the GuardDuty console.

## Finding types worth automating

| Finding type prefix | What it means | Typical automation |
|---|---|---|
| `UnauthorizedAccess:IAMUser/InstanceCredentialExfiltration` | Instance role credentials used from outside AWS | Disable the session, rotate the role |
| `Recon:IAMUser/*` and `Discovery:*` | Unusual API enumeration by a principal | Investigate the principal, consider disabling its keys |
| `CryptoCurrency:EC2/BitcoinTool.B` | Instance talking to mining pools | Isolate the instance |
| `Backdoor:EC2/*` | Instance acting as a C2 client or bot | Isolate the instance, snapshot for forensics |
| `Policy:S3/BucketBlockPublicAccessDisabled` | Public access block removed from a bucket | Re-enable the block |

Severity 7.0 and above is High. Most teams auto-contain only High findings and route Medium findings to an analyst.
""",
    "aws_ec2_connector.md": """# AWS EC2 connector

## Overview

The EC2 connector manages Amazon EC2 instances, volumes and security groups. In incident response it is used to isolate compromised instances and preserve evidence.

## Isolation pattern

The recommended way to isolate an instance is to replace all of its security groups with a dedicated quarantine security group that has no inbound rules and no outbound rules. Existing tracked connections may persist briefly, so isolation is not instantaneous for long-lived sessions. Do not stop or terminate the instance before taking snapshots: memory and some volatile evidence are lost when the instance stops.

## Operations

### describe_instance
Returns instance state, attached volumes, security groups, IAM instance profile, tags and the VPC/subnet.

### create_snapshot
Creates an EBS snapshot of a volume. Snapshot every attached volume before changing anything else. Tag snapshots with the incident ID so they are not removed by lifecycle policies.

### modify_instance_security_groups
Replaces the instance's security groups. Pass the quarantine group ID only.

### tag_instance
Adds tags such as `SecurityStatus=Quarantined` and `IncidentId=<id>` so owners and automation can see the instance is under investigation.

### stop_instance
Stops the instance. Use only after evidence collection is complete, or when the instance is actively causing harm that isolation cannot stop.

## Permissions

`ec2:DescribeInstances`, `ec2:CreateSnapshot`, `ec2:CreateTags`, `ec2:ModifyInstanceAttribute`, and `ec2:StopInstances` if stopping is automated.
""",
    "aws_iam_connector.md": """# AWS IAM connector

## Overview

The IAM connector manages identities in an AWS account: users, access keys, roles and policies. It is the main containment tool for compromised credentials.

## Operations

### list_access_keys
Lists a user's access key IDs, their status (Active or Inactive) and creation dates.

### update_access_key
Sets an access key to Inactive (or back to Active). Deactivating is reversible and should be preferred over deleting during an investigation.

### attach_user_policy
Attaches a managed policy to a user. Attaching an explicit deny-all policy blocks every action, including those allowed by other policies, and is a fast way to contain a user whose keys you cannot enumerate.

### revoke_role_sessions
For roles, adds an inline policy that denies all actions for sessions issued before the current time. This invalidates stolen temporary credentials without affecting new, legitimate sessions.

### get_user
Returns the user's ARN, creation date, groups, attached policies and the last time each access key was used.

## Containment order for a leaked access key

1. Identify the key's owner with `get_user` and check when the key was last used and from where.
2. Deactivate the key with `update_access_key`.
3. If the attacker may have created additional keys or users, attach the deny-all policy to the affected user and review CloudTrail for `CreateAccessKey`, `CreateUser` and `AttachUserPolicy` calls.
4. Rotate any secrets the user could read, such as Secrets Manager entries and parameters.

## Common mistakes

Deleting the key destroys the evidence of which key was used. Deactivating the key does not end sessions that were already created with `AssumeRole`; revoke the role sessions as well.
""",
    "aws_s3_connector.md": """# AWS S3 connector

## Overview

The S3 connector inspects and changes bucket configuration. Playbooks use it to remediate public exposure and to gather evidence of data access.

## Operations

### get_bucket_acl and get_bucket_policy
Return the bucket ACL grants and the bucket policy. A grant to `AllUsers` or a policy with `"Principal": "*"` and no restricting condition makes the bucket public.

### put_public_access_block
Enables all four public access block settings on the bucket: BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy and RestrictPublicBuckets. This overrides public ACLs and policies without deleting them, so it is safe to apply first and investigate afterwards.

### get_public_access_block
Returns the current block settings at bucket level.

## Investigation tips

Public exposure does not mean data was read. Check S3 server access logs or CloudTrail data events for `GetObject` calls from unknown principals during the exposure window. Some buckets are public on purpose, for example website hosting; keep an allow list of such buckets and skip them in automation.
""",
    "aws_cloudtrail_connector.md": """# AWS CloudTrail connector

## Overview

CloudTrail records API activity in an AWS account. The connector lets playbooks check trail health and search recent events.

## Operations

### get_trail_status
Returns whether a trail is logging, when it last delivered logs, and any delivery errors.

### start_logging
Restarts logging on a trail that was stopped.

### lookup_events
Searches the last 90 days of management events by event name, user name, resource or access key ID. Results include the source IP, user agent and the request parameters.

## Why disabled logging matters

Attackers often call `StopLogging`, `DeleteTrail` or `UpdateTrail` early in an intrusion to hide what they do next. Treat any of these calls as high severity: restart logging immediately, then find who made the call with `lookup_events` and contain that principal. An organisation trail managed from the management account cannot be stopped from member accounts, which is the recommended setup.
""",
    "veeam_backup_connector.md": """# Veeam Backup connector

## Overview

The Veeam connector queries a Veeam Backup & Replication server so that incident response playbooks can check whether clean backups exist before a restore is planned.

## Operations

### get_restore_points
Returns the restore points for a VM or protected machine with creation time, job name, and whether the point is stored on immutable or hardened repository storage.

### get_job_status
Returns the last result of a backup job.

### start_instant_recovery
Starts an instant recovery of a VM from a chosen restore point into an isolated network. Use this for testing that a restore point is clean before a full restore.

## Ransomware guidance

Ransomware operators often delete or encrypt backups before detonating the encryptor. Restore points on immutable storage cannot be modified during their immutability period and are the most reliable recovery source. Before restoring, scan the restore point (Veeam's secure restore option) so you do not reintroduce the malware.
""",
    "war_room_and_incident_collaboration.md": """# War rooms and incident collaboration

## Overview

For major incidents FortiSOAR can open a war room: a dedicated workspace attached to the incident with a timeline, task list, team chat and shared evidence. War rooms are designed for incidents that need several teams working in parallel, such as ransomware outbreaks or cloud account compromise.

## Tasks

Tasks inside a war room can be created by playbooks or analysts and are assigned to users or teams with due dates. Playbooks can wait for tasks to be completed, which lets automation hand off to people and resume afterwards.

## Timeline

Every playbook action, record change and comment attached to the incident is shown on the timeline in order. Use the timeline to prepare the post-incident report.

## Good practice

- Open the war room from a playbook as soon as an incident is confirmed as Critical, so collaborators are invited consistently.
- Keep containment decisions in the war room chat rather than in private messages, so they are captured on the timeline.
- Close the war room only after lessons-learned tasks are created.
""",
}

INTERNAL_DOCS_EXT = {
    "ransomware_response_plan.md": """# Ransomware response plan

Owner: SOC manager. Review cycle: every six months and after any ransomware incident.

## 1. Purpose and scope

This plan describes how the SOC detects, contains, eradicates and recovers from ransomware affecting company endpoints, servers and vessel IT systems. It applies to all business units. Operational technology on vessels is covered only for detection and network containment; recovery of OT systems follows the separate OT recovery procedure.

## 2. Roles

| Role | Responsibility |
|---|---|
| Incident commander (SOC manager or deputy) | Declares the incident, approves containment of servers, owns external communication requests |
| SOC Tier 2 | Runs the automation, validates scope, approves workstation isolation |
| Infrastructure team | Backup verification and restore, rebuild of servers |
| IAM team | Account resets and privileged access review |
| Legal and communications | Regulator and customer notification decisions |

## 3. Detection

Ransomware is usually detected by FortiEDR anti-ransomware rules. Each such alert starts the Ransomware - Detection Triage playbook automatically. The playbook pulls the full FortiEDR event, checks whether encryption activity was confirmed (blocked or simulated), opens a ServiceNow ticket and pages the on-call engineer.

Other signals that should be treated as possible ransomware: mass file renames on file shares, a ransom note found by a user, sudden deletion of volume shadow copies, and backup jobs failing across many machines at once.

## 4. Containment

Speed matters more than precision in the first hour. When encryption is confirmed:

1. The Ransomware - Contain Outbreak playbook asks the incident commander for approval. After approval it isolates the first affected endpoint, disables the user account seen in the event, and quarantines servers through FortiNAC when they have no EDR agent.
2. Ransomware - Identify Blast Radius hunts the encryptor hash across all endpoints and searches Splunk for SMB lateral movement from the first host during the previous 24 hours.
3. Every newly affected host found by the blast radius search is isolated in turn. The incident commander can decide to isolate whole network segments instead when more than ten hosts are affected.

Do not power off encrypted machines. Memory may contain encryption keys or evidence; isolate them instead.

## 5. Eradication

After containment, the infrastructure team rebuilds affected workstations from the golden image. Servers are restored only from verified clean backups (section 6). The IAM team resets passwords for every account used on affected hosts in the last seven days and reviews privileged group membership for unexpected changes.

Collect ransom note indicators with Ransomware - Collect Ransom Note IOCs. It records the note's hash, the payment and leak-site domains, and blocks those domains on the proxy and firewall.

## 6. Recovery

The Ransomware - Verify Backups playbook is started manually from the incident. It lists restore points for each affected system and highlights those on immutable storage created before the first malicious activity. A restore point is considered clean only if the Veeam secure restore scan completes without detections.

Restore order: identity infrastructure first (domain controllers), then core business services, then file shares, then individual workstations.

## 7. Communication

All internal updates go through the war room. The incident commander provides a status summary every two hours during containment. Legal decides within 24 hours whether regulators or customers must be notified. Never contact the attacker or pay a ransom without executive approval and legal advice.

## 8. Post-incident

Within ten working days, run a lessons-learned session, update this plan, and create tasks for any detection gaps. Record the time from first detection to containment; the target is under 60 minutes.
""",
    "ransomware_backup_recovery_procedure.md": """# Backup verification and recovery procedure (ransomware)

## When to use

Use this procedure after ransomware has been contained and the incident commander has authorised recovery. It is carried out by the infrastructure team with SOC support.

## Step 1 - Establish the infection time

Ask the SOC for the earliest malicious activity time from the incident timeline. Every restore point must be older than this time. Add a safety margin of 24 hours for systems where the attacker had interactive access, because they may have planted persistence before running the encryptor.

## Step 2 - List candidate restore points

The backup verification automation (started from the incident with the Verify Backups button) queries the backup server for each affected system and lists the restore points with their creation times and whether they are on immutable storage. Restore points created after the infection time are marked as unsafe.

## Step 3 - Test the restore point

Start an instant recovery of the chosen restore point into the isolated recovery network. The secure restore scan must complete without detections. If it finds malware, pick the next older restore point and repeat.

## Step 4 - Restore and validate

Restore to production only after the test succeeds. The application owner validates the service. The SOC monitors the restored system closely for 72 hours, with heightened FortiEDR policy.

## Step 5 - Record

Record which restore point was used, how much data was lost (time between restore point and infection), and any failed restore attempts. This data feeds the recovery time metrics in the post-incident report.

## Known issues

- Backup jobs for vessel systems run only when the satellite link has enough bandwidth, so restore points for vessels can be several days old.
- The backup service account password must never be stored in playbooks. One legacy automation still had it hard-coded; it was flagged for change in the last audit.
""",
    "ransomware_tabletop_lessons_learned.md": """# Tabletop exercise: ransomware on the finance file server (lessons learned)

## Scenario

The exercise simulated encryption starting on a finance workstation and spreading to the finance file server over SMB within 40 minutes. Participants were the SOC, infrastructure, IAM, legal and communications.

## What went well

- The FortiEDR alert was triaged automatically within two minutes and the on-call engineer was paged immediately.
- The approval step before containment worked as intended; the incident commander approved within six minutes.
- Hunting the encryptor hash found two additional infected machines that the initial alert did not mention.

## What did not go well

1. The file server had no EDR agent, so endpoint isolation could not be used. The team lost 15 minutes finding a way to cut it off. Action: the containment automation must fall back to network access control quarantine for hosts without an agent.
2. Nobody knew which backups were immutable. Action: the backup check must show immutability for each restore point.
3. The ransom note domains were blocked manually on the firewall but not on the DNS security service. Action: block indicators in both places automatically.
4. Communication happened partly in private chats, so the timeline was incomplete. Action: always open a war room for critical incidents.

## Follow-up status

Actions 1 to 3 have been implemented in the ransomware automation. Action 4 is covered by the escalation process for Critical incidents.
""",
    "aws_incident_response_runbook.md": """# AWS incident response runbook

Scope: all production and non-production AWS accounts in the company organisation. Owner: Cloud security lead.

## 1. Sources of alerts

GuardDuty is enabled in every account and region, with the security account as delegated administrator. Findings of severity 4.0 and above are ingested into FortiSOAR as alerts with source "AWS GuardDuty". Security Hub controls and custom CloudTrail detections (root account usage, CloudTrail disabled, public S3 buckets) are also ingested.

## 2. Triage

The AWS GuardDuty Finding Triage playbook runs on every GuardDuty alert. It retrieves the full finding and decides the path based on the finding type:

- Compromised credentials (for example InstanceCredentialExfiltration or anomalous API calls by an IAM user) lead to the AWS - Disable Access Key playbook.
- Compromised instances (backdoor, crypto mining, C2 traffic) lead to the AWS - Isolate EC2 Instance playbook.

Medium findings are not contained automatically; an analyst reviews them within four hours.

## 3. Containing a compromised instance

1. Approval: the cloud on-call engineer approves isolation for production instances. Non-production instances may be isolated without approval.
2. Evidence: snapshot every attached EBS volume before touching the instance. Do not stop the instance.
3. Isolation: replace all security groups with the quarantine security group.
4. Tagging: tag the instance with SecurityStatus=Quarantined and the incident ID.
5. Notify the application owner listed in the account inventory.

Instances in an Auto Scaling group may be replaced automatically after isolation. Detach the instance from the group before isolating it, or the evidence may be terminated.

## 4. Containing compromised credentials

1. Identify the principal and when the key was last used.
2. Deactivate (do not delete) the access key.
3. Attach the deny-all policy if there is any sign the attacker created new keys, users or roles.
4. Search CloudTrail for CreateAccessKey, CreateUser, CreateLoginProfile, AttachUserPolicy and PutRolePolicy calls by the principal in the last 90 days.
5. Email the key owner and their manager.

For roles, revoke active sessions instead of deactivating keys.

## 5. Special cases

- **CloudTrail stopped or deleted:** restart logging immediately (the CloudTrail Disabled Response automation does this), then treat the principal that stopped it as compromised.
- **Root account used:** root logins are never expected. Confirm with the account owner within 15 minutes; if unconfirmed, rotate the root password and MFA through the break-glass process.
- **Public S3 bucket:** re-enable public access block first, then review access logs for downloads during the exposure window.

## 6. Recovery

Rebuild compromised instances from the latest approved AMI rather than cleaning them. Rotate every secret the compromised principal or instance role could read. Close the GuardDuty finding by archiving it once the incident is resolved.
""",
    "aws_account_inventory_and_contacts.md": """# AWS account inventory and contacts

This list is used by the SOC to find the owner of an affected account. Keep it in sync with the organisation's account vending process.

| Account | Purpose | Owner team | Escalation mailbox |
|---|---|---|---|
| security-prod | GuardDuty and Security Hub administrator, log archive | Cloud security | cloudsec@acme-tenant.example |
| shared-services | Directory, CI/CD runners, artefact storage | Platform engineering | platform-oncall@acme-tenant.example |
| fleet-telemetry-prod | Vessel telemetry ingestion | Fleet data | fleet-data@acme-tenant.example |
| customer-portal-prod | Customer self-service portal | Digital channels | digital-oncall@acme-tenant.example |
| sandbox | Developer experiments, no production data | Platform engineering | platform-oncall@acme-tenant.example |

## Break-glass roles

The break-glass role is arn:aws:iam::210987654321:role/BreakGlassAdmin in the security-prod account. It may only be assumed during a declared Critical incident and every use is reviewed.

## Known automation identities

- arn:aws:iam::123456789012:user/ci-deploy-bot (shared-services): legacy CI user with long-lived keys. The key AKIAIOSFODNN7EXAMPLE is scheduled for replacement by an assumed role.
- arn:aws:iam::123456789012:role/FortiSOAR-Responder: assumed by FortiSOAR connectors for containment actions.

## Public buckets that are allowed

Only the customer portal's static website bucket is allowed to be public. Any other public bucket is an incident.
""",
    "aws_guardduty_tuning_guide.md": """# GuardDuty tuning guide

## Why tune

Untuned GuardDuty produces many low-value findings, which leads to alert fatigue and slower response to real compromises. Tuning reduces noise without disabling detection.

## Suppression rules

Use suppression rules for well-understood benign activity, for example vulnerability scanners that trigger port-probe findings, or CI runners that call APIs from changing IP addresses. Suppression rules archive matching findings automatically; they still exist and can be reviewed.

Never suppress credential-related finding types such as InstanceCredentialExfiltration. Those must always reach the SOC.

## Trusted IP lists

Add the corporate egress IP ranges and the vessel satellite gateways to the trusted IP list so that console logins from them do not generate findings.

## Severity thresholds

FortiSOAR ingests findings of severity 4.0 and above. Findings of 7.0 and above are contained automatically by the triage automation; everything between 4.0 and 7.0 goes to an analyst.

## Review

Review suppression rules quarterly. Remove any rule that has not matched in 90 days.
""",
    "cloud_security_baseline.md": """# Cloud security baseline (AWS)

These controls apply to every account. Deviations need a documented exception approved by the cloud security lead.

## Logging

An organisation-wide trail logs management events from all accounts into the log archive account. Member accounts must not create trails that replace it. Any attempt to stop or delete logging is treated as a security incident and is reversed automatically.

## Storage

Account-level S3 Block Public Access is enabled in every account except where a documented website bucket requires otherwise. If a bucket becomes public unexpectedly, automation re-enables the block immediately, raises a ticket for the bucket owner, and the SOC reviews access logs.

## Identity

- No long-lived access keys for humans. Engineers sign in through single sign-on and assume roles.
- Root accounts have hardware MFA and are used only through the break-glass process. Any root login raises a high-severity alert and the account owner must confirm it.
- Access keys for automation are rotated every 90 days and should be replaced by roles where possible.

## Compute

Instances must use IMDSv2. Security groups must not allow inbound access from 0.0.0.0/0 except on load balancers. A quarantine security group with no rules exists in every VPC for incident response.

## Detection

GuardDuty, Security Hub and IAM Access Analyzer are enabled in all accounts and regions, aggregated in the security account.
""",
}
