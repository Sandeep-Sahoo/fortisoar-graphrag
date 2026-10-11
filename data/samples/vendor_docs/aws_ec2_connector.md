> Synthetic test document written for this project. Not official Fortinet documentation.

# AWS EC2 connector

## Overview

The EC2 connector manages Amazon EC2 instances, volumes and security groups. In incident response it is used to isolate compromised instances and preserve evidence.

## Isolation pattern

The recommended way to isolate an instance is to replace all of its security groups with a dedicated quarantine security group that has no inbound rules and no outbound rules. Existing tracked connections may persist briefly, so isolation is not instantaneous for long-lived sessions. Do not stop or terminate the instance before taking snapshots: memory and some volatile evidence are lost when the instance stops.

## Operations

### describe_instance
Returns instance state, attached volumes, security groups, IAM instance profile, tags and the VPC/subnet.

### create_snapshot
Creates an EBS snapshot of a volume. Snapshot every attached volume before changing anything else. Tag snapshots with the incident ID so they are not removed by lifecycle policies.

### modify_instance_security_groups
Replaces the instance's security groups. Pass the quarantine group ID only.

### tag_instance
Adds tags such as `SecurityStatus=Quarantined` and `IncidentId=<id>` so owners and automation can see the instance is under investigation.

### stop_instance
Stops the instance. Use only after evidence collection is complete, or when the instance is actively causing harm that isolation cannot stop.

## Permissions

`ec2:DescribeInstances`, `ec2:CreateSnapshot`, `ec2:CreateTags`, `ec2:ModifyInstanceAttribute`, and `ec2:StopInstances` if stopping is automated.

