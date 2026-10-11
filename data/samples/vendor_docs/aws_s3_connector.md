> Synthetic test document written for this project. Not official Fortinet documentation.

# AWS S3 connector

## Overview

The S3 connector inspects and changes bucket configuration. Playbooks use it to remediate public exposure and to gather evidence of data access.

## Operations

### get_bucket_acl and get_bucket_policy
Return the bucket ACL grants and the bucket policy. A grant to `AllUsers` or a policy with `"Principal": "*"` and no restricting condition makes the bucket public.

### put_public_access_block
Enables all four public access block settings on the bucket: BlockPublicAcls, IgnorePublicAcls, BlockPublicPolicy and RestrictPublicBuckets. This overrides public ACLs and policies without deleting them, so it is safe to apply first and investigate afterwards.

### get_public_access_block
Returns the current block settings at bucket level.

## Investigation tips

Public exposure does not mean data was read. Check S3 server access logs or CloudTrail data events for `GetObject` calls from unknown principals during the exposure window. Some buckets are public on purpose, for example website hosting; keep an allow list of such buckets and skip them in automation.

