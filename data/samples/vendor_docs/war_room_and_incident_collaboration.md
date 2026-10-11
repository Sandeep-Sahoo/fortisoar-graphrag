> Synthetic test document written for this project. Not official Fortinet documentation.

# War rooms and incident collaboration

## Overview

For major incidents FortiSOAR can open a war room: a dedicated workspace attached to the incident with a timeline, task list, team chat and shared evidence. War rooms are designed for incidents that need several teams working in parallel, such as ransomware outbreaks or cloud account compromise.

## Tasks

Tasks inside a war room can be created by playbooks or analysts and are assigned to users or teams with due dates. Playbooks can wait for tasks to be completed, which lets automation hand off to people and resume afterwards.

## Timeline

Every playbook action, record change and comment attached to the incident is shown on the timeline in order. Use the timeline to prepare the post-incident report.

## Good practice

- Open the war room from a playbook as soon as an incident is confirmed as Critical, so collaborators are invited consistently.
- Keep containment decisions in the war room chat rather than in private messages, so they are captured on the timeline.
- Close the war room only after lessons-learned tasks are created.

