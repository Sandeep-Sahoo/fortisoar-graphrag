> Synthetic test document.

# AWS incident response runbook

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

