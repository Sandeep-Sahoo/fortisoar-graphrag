> Synthetic test document written for this project. Not official Fortinet documentation.

# AWS CloudTrail connector

## Overview

CloudTrail records API activity in an AWS account. The connector lets playbooks check trail health and search recent events.

## Operations

### get_trail_status
Returns whether a trail is logging, when it last delivered logs, and any delivery errors.

### start_logging
Restarts logging on a trail that was stopped.

### lookup_events
Searches the last 90 days of management events by event name, user name, resource or access key ID. Results include the source IP, user agent and the request parameters.

## Why disabled logging matters

Attackers often call `StopLogging`, `DeleteTrail` or `UpdateTrail` early in an intrusion to hide what they do next. Treat any of these calls as high severity: restart logging immediately, then find who made the call with `lookup_events` and contain that principal. An organisation trail managed from the management account cannot be stopped from member accounts, which is the recommended setup.

