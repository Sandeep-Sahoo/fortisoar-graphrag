> Synthetic test document written for this project. Not official Fortinet documentation.

# AWS IAM connector

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

