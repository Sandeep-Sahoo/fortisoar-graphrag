> Synthetic test document written for this project. Not official Fortinet documentation.

# Playbook triggers

A playbook starts from a trigger step. FortiSOAR supports several trigger types.

**On Create / On Update** triggers run when a record in a module (for example Alerts or Incidents) is created or updated. Field-based conditions restrict which records start the playbook, such as `type equals Phishing`.

**Manual** triggers add a button to the record view. An analyst selects one or more records and clicks the button. Manual triggers are used for actions that need human judgement, like isolating a host.

**Referenced** playbooks have no trigger of their own. They are called from other playbooks with the Reference a Playbook step and receive input through `vars.input.records`.

**Custom API Endpoint** triggers expose a playbook as an HTTP endpoint so external systems can start it. Choose an authentication method (API key or basic auth) for every endpoint.
