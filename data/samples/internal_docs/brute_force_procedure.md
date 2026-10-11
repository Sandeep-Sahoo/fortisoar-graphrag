> Synthetic test document.

# Brute force procedure

Repeated failed logins from one source are handled automatically. Splunk is queried for the failure count, the source IP is scored, and abusive sources are blocked on the perimeter firewall by the shared blocking playbook.

Never block the corporate jump host 10.20.30.40. It is on the allow list for this reason.
