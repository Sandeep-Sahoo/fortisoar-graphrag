> Synthetic test document written for this project. Not official Fortinet documentation.

# Decision step

The Decision step branches a playbook based on conditions. Each condition is a Jinja expression that evaluates to true or false. The first matching condition's route is followed; add a default route for when no condition matches.

Typical uses: route on a verdict (malicious or clean), on severity, or on whether an indicator is on an allow list.
