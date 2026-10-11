> Synthetic test document written for this project. Not official Fortinet documentation.

# Fortinet FortiEDR connector

## Overview

The FortiEDR connector lets playbooks investigate and respond to endpoint threats detected by FortiEDR. It covers event retrieval, threat hunting across the fleet, and device-level response actions. It is commonly used in ransomware workflows because FortiEDR can block encryption behaviour in real time and report which processes attempted it.

## Configuration

Create one configuration per FortiEDR management console. You need the console hostname, an organisation name (for multi-tenant consoles), and an API user with the "Rest API" role. Store the API user's credentials in the connector configuration; do not pass them as step parameters.

The connector verifies the console certificate by default. Disable verification only in lab environments.

## Operations

### get_event
Returns full details of a security event: the triggering process, its hash, the device, the user, the rule that fired, and the action FortiEDR took (Block, Log, or Simulation). Use it as the first enrichment step on any FortiEDR alert.

Parameters: `event_id` (required).

### hunt_hash
Searches every managed endpoint for a file hash and returns the devices where it was seen, with first-seen and last-seen times. This is the main operation for answering "how far has it spread?" during a ransomware outbreak.

Parameters: `sha256` (required), `days` (optional, default 30).

### isolate_device
Network-isolates a device so it can only talk to the FortiEDR console. Isolation survives reboots. Use `remove_isolation` to undo it.

Parameters: `device_id` (required).

### get_device
Returns operating system, last-seen time, logged-in users and isolation state for a device.

## Ransomware considerations

FortiEDR's anti-ransomware rules raise events with the classification "Malicious" and rule names containing "Ransomware" or "File Encryptor". When the policy is in Prevention mode, the encrypting process has usually been terminated already. In Simulation mode only a log entry is created, so the playbook must contain the device itself.

Hunting the hash of the encryptor is fast, but attackers often recompile per victim. Combine `hunt_hash` with a search for the ransom note file name across endpoints for better coverage.

## Troubleshooting

- `401 Unauthorized`: the API user is missing the Rest API role or the organisation name is wrong.
- `hunt_hash` returns nothing for a hash you know is present: the endpoint may have been offline for longer than the `days` window.
- Isolation succeeds but the device still communicates: the collector may be outdated; check `get_device` for the collector version.

