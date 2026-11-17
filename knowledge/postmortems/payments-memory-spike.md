# Synthetic postmortem: payments memory spike

Reviewed: 2026-11-17
Owner: example-payments-team
Synthetic: this incident is fictional training data, not a reported production event.

## OOMKilled after a synthetic batch-size change

In this fictional scenario, payments-worker restarted with OOMKilled after a deployment increased batch size. The fixture's supplied metrics showed a sharp peak during batch processing rather than steady growth. A rollback of the batch setting stopped the terminations in the scenario. This is evidence for that fictional incident only; it does not establish the cause of another pod's OOM.

## Lessons and follow-up

The example team added a load test with the larger batch, reviewed peak memory against limits, and documented an owner-approved rollback. For a new incident, collect its own configuration diff and memory trend before proposing the same action. A runbook is reusable guidance; a historical resolution is not an automatic remediation instruction.
