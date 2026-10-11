> Synthetic test document written for this project. Not official Fortinet documentation.

# Veeam Backup connector

## Overview

The Veeam connector queries a Veeam Backup & Replication server so that incident response playbooks can check whether clean backups exist before a restore is planned.

## Operations

### get_restore_points
Returns the restore points for a VM or protected machine with creation time, job name, and whether the point is stored on immutable or hardened repository storage.

### get_job_status
Returns the last result of a backup job.

### start_instant_recovery
Starts an instant recovery of a VM from a chosen restore point into an isolated network. Use this for testing that a restore point is clean before a full restore.

## Ransomware guidance

Ransomware operators often delete or encrypt backups before detonating the encryptor. Restore points on immutable storage cannot be modified during their immutability period and are the most reliable recovery source. Before restoring, scan the restore point (Veeam's secure restore option) so you do not reintroduce the malware.

