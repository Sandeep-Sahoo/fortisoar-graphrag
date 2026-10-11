> Synthetic test document written for this project. Not official Fortinet documentation.

# Fortinet FortiNAC connector

## Overview

The FortiNAC connector controls network access for hosts. Playbooks use it to move a suspicious host into a quarantine VLAN without needing an EDR agent on the host, which makes it useful for servers, OT devices, and machines whose agent has been tampered with.

## Operations

### quarantine_host
Marks a host as "At Risk" and moves it to the quarantine VLAN defined in the FortiNAC isolation policy. Parameters: `mac_address` or `ip_address`.

### release_host
Clears the At Risk state and returns the host to its normal VLAN.

### get_host
Returns the host's switch port, VLAN, last-seen time and registration owner.

## Notes

Quarantine depends on the switch being managed by FortiNAC. For hosts on unmanaged switches the operation reports success but the VLAN does not change; confirm with `get_host` after quarantining. Quarantining a hypervisor quarantines every virtual machine behind its uplink, so exclude hypervisors in the playbook.

