> Synthetic test document.

# Playbook change management

Shared subroutine playbooks (Block IP Address, Enrich Indicators, Create ServiceNow Incident, Notify SOC On-Call) are used by many other playbooks. Before changing one, list every playbook that calls it and test each one in staging.

All playbook changes need a peer review and a change ticket. Export the collection before and after the change.
