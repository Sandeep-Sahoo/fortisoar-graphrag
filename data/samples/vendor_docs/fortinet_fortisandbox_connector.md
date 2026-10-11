> Synthetic test document written for this project. Not official Fortinet documentation.

# Fortinet FortiSandbox connector

The FortiSandbox connector submits files and URLs for dynamic analysis and retrieves verdicts.

Operations include `submit_file`, `submit_url`, and `get_file_verdict`. Verdicts are returned as ratings such as Clean, Low Risk, Medium Risk, High Risk, or Malicious. Submission is asynchronous: poll `get_file_verdict` or add a wait step before reading the result.
