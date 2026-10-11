> Synthetic test document written for this project. Not official Fortinet documentation.

# Execution history and debugging

Every playbook run is recorded in the execution history with each step's input, output, and duration. Failed steps show the error returned by the connector.

To debug, open the failed run, inspect the step's rendered parameters, and re-run the playbook from the failed step after fixing the cause. Jinja errors usually mean a referenced step name or field does not exist.
