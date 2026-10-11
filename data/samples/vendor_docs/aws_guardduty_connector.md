> Synthetic test document written for this project. Not official Fortinet documentation.

# AWS GuardDuty connector

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

