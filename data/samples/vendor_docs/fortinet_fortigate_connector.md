> Synthetic test document written for this project. Not official Fortinet documentation.

# Fortinet FortiGate connector

The FortiGate connector manages firewall objects. `block_ip` adds an address to a blocked address group, `unblock_ip` removes it, and `block_url` adds a URL or domain to the web filter.

Use an allow-list check before blocking so that critical infrastructure is never blocked. The connector needs an API user with write access to address objects.
