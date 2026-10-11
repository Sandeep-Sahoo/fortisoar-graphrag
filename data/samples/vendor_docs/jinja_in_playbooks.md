> Synthetic test document written for this project. Not official Fortinet documentation.

# Using Jinja in playbooks

Step parameters accept Jinja expressions. `vars.input.records` holds the triggering records. `vars.steps.<Step_Name>` holds the output of earlier steps, with spaces in step names replaced by underscores.

Filters such as `| default`, `| length`, and `| json_query` help shape data. Keep complex logic in a Set Variable step so it can be inspected in the execution history.
