> Synthetic test document.

# Backup verification and recovery procedure (ransomware)

## When to use

Use this procedure after ransomware has been contained and the incident commander has authorised recovery. It is carried out by the infrastructure team with SOC support.

## Step 1 - Establish the infection time

Ask the SOC for the earliest malicious activity time from the incident timeline. Every restore point must be older than this time. Add a safety margin of 24 hours for systems where the attacker had interactive access, because they may have planted persistence before running the encryptor.

## Step 2 - List candidate restore points

The backup verification automation (started from the incident with the Verify Backups button) queries the backup server for each affected system and lists the restore points with their creation times and whether they are on immutable storage. Restore points created after the infection time are marked as unsafe.

## Step 3 - Test the restore point

Start an instant recovery of the chosen restore point into the isolated recovery network. The secure restore scan must complete without detections. If it finds malware, pick the next older restore point and repeat.

## Step 4 - Restore and validate

Restore to production only after the test succeeds. The application owner validates the service. The SOC monitors the restored system closely for 72 hours, with heightened FortiEDR policy.

## Step 5 - Record

Record which restore point was used, how much data was lost (time between restore point and infection), and any failed restore attempts. This data feeds the recovery time metrics in the post-incident report.

## Known issues

- Backup jobs for vessel systems run only when the satellite link has enough bandwidth, so restore points for vessels can be several days old.
- The backup service account password must never be stored in playbooks. One legacy automation still had it hard-coded; it was flagged for change in the last audit.

