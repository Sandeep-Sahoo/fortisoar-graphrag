> Synthetic test document written for this project. Not official Fortinet documentation.

# Connectors overview

Connectors integrate FortiSOAR with external products. Each connector exposes operations (actions) that playbooks call through connector steps, for example `block_ip` on a firewall connector or `get_file_reputation` on a threat intel connector.

A connector can have several configurations (for example one per firewall). Credentials are stored in the configuration, not in playbooks. Rotate credentials in the configuration and avoid hard-coding secrets in step parameters.

Connector versions matter: upgrading a connector can rename or change operation parameters, so test playbooks that use it after an upgrade.
