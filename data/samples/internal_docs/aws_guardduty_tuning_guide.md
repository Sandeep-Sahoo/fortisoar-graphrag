> Synthetic test document.

# GuardDuty tuning guide

## Why tune

Untuned GuardDuty produces many low-value findings, which leads to alert fatigue and slower response to real compromises. Tuning reduces noise without disabling detection.

## Suppression rules

Use suppression rules for well-understood benign activity, for example vulnerability scanners that trigger port-probe findings, or CI runners that call APIs from changing IP addresses. Suppression rules archive matching findings automatically; they still exist and can be reviewed.

Never suppress credential-related finding types such as InstanceCredentialExfiltration. Those must always reach the SOC.

## Trusted IP lists

Add the corporate egress IP ranges and the vessel satellite gateways to the trusted IP list so that console logins from them do not generate findings.

## Severity thresholds

FortiSOAR ingests findings of severity 4.0 and above. Findings of 7.0 and above are contained automatically by the triage automation; everything between 4.0 and 7.0 goes to an analyst.

## Review

Review suppression rules quarterly. Remove any rule that has not matched in 90 days.

