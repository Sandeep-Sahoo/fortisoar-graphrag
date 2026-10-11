> Synthetic test document.

# Cloud security baseline (AWS)

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

