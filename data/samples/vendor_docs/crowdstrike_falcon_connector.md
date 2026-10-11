> Synthetic test document written for this project. Not official Fortinet documentation.

# CrowdStrike Falcon connector

The CrowdStrike Falcon connector manages endpoints. `contain_host` network-isolates a device so it can only talk to the Falcon cloud; `lift_containment` reverses it. `get_device_details` returns hostname, OS, and last-seen time.

Containment disrupts the user, so gate it behind an approval for production endpoints.
