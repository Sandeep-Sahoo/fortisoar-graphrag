> Synthetic test document written for this project. Not official Fortinet documentation.

# VirusTotal connector

The VirusTotal connector returns reputation for files, URLs, domains, and IPs. `get_file_reputation` takes a hash and returns detection counts (`positives`) from antivirus engines. `get_reputation_of_url` does the same for URLs.

Public API keys are rate limited. Cache results on indicator records to avoid repeated lookups.
