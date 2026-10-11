> Synthetic test document written for this project. Not official Fortinet documentation.

# Reference a Playbook step

The Reference a Playbook step calls another playbook (a child) from the current one (the parent). Use it to build reusable subroutines such as enrichment, blocking, or ticket creation.

The parent passes records to the child as `vars.input.records`. The child's final step output is available to the parent as the step result.

Changing a child playbook affects every parent that references it. Before editing a shared child, list its callers and test them. A referenced playbook that is deactivated causes parent executions to fail at that step.
