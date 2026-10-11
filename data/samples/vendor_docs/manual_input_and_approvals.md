> Synthetic test document written for this project. Not official Fortinet documentation.

# Manual Input and approvals

The Manual Input step pauses a playbook and asks a user or team for a decision. It is commonly used as an approval gate before disruptive actions such as blocking, deleting email, or containing a host.

Configure the owner (a user or team), the input fields, and an optional timeout. When the timeout expires the playbook follows the timeout path, so always define what happens if nobody responds.

Pending manual inputs appear in the Pending Decisions view and can be answered from email if email approvals are enabled.
