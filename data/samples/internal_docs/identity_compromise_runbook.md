> Synthetic test document.

# Identity compromise runbook

Impossible-travel alerts are handled by Identity - Impossible Travel Triage. High-risk sign-ins (AbuseIPDB score above 75) lead to the Disable User Account playbook, which disables the AD account and revokes Entra ID sessions after approval.

After containment, the IAM team (iam-team@acme-tenant.example) resets the password and reviews MFA methods with the user.
