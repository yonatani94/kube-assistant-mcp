# Synthetic postmortem: checkout dependency hostname

Reviewed: 2026-11-17
Owner: example-checkout-team
Synthetic: fictional training example; no real customer incident or benchmark.

## DNS lookup failed after a namespace migration

In this scenario, checkout used an unqualified inventory hostname after inventory moved to a different namespace. Logs reported no such host. Other workloads resolved names correctly. The supplied deployment diff and service inventory established the namespace mismatch; there was no evidence of a cluster-wide DNS outage.

## Resolution and learning

The fictional team updated the reviewed configuration to use the correct namespace-qualified service name. They added a configuration validation check to deployment review. Do not assume the same explanation for every DNS error: timeouts across many workloads call for different evidence and escalation.
