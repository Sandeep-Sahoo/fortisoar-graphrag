> Synthetic test document.

# AWS account inventory and contacts

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

