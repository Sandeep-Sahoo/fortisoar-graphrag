> Synthetic test document.

# Tabletop exercise: ransomware on the finance file server (lessons learned)

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

