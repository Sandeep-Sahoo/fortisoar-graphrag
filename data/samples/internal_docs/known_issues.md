> Synthetic test document.

# Known issues and troubleshooting

- The FortiSandbox verdict is sometimes empty because the submission has not finished. The attachment analysis automation should wait before reading the verdict.
- ServiceNow ticket creation fails when the alert name is longer than 160 characters.
- Disabling accounts fails for cloud-only users because they have no on-premises AD object; revoke sessions still works.
