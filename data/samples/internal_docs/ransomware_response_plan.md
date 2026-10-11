> Synthetic test document.

# Ransomware response plan

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

