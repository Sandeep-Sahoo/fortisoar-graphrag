# Evaluation questions

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
