> Synthetic test document written for this project. Not official Fortinet documentation.

# Fortinet FortiMail connector

The FortiMail connector searches and remediates email. `search_message` finds messages by subject, sender, or message ID across mailboxes. `delete_message` removes the found copies.

Because deletion cannot be undone, combine `delete_message` with a Manual Input approval and record what was deleted on the alert.
