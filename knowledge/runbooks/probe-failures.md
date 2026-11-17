# Readiness and liveness probe failures

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## Readiness and liveness probe failures — evidence

Readiness controls whether a pod receives normal Service traffic; liveness failures can restart a container. Collect probe configuration, relevant Unhealthy events and application health logs. Distinguish timeout, refused connection, wrong path and unexpected status code.

## Readiness and liveness probe failures — investigation

Compare probe startup timing with measured initialization duration. Check whether the health endpoint depends on a remote service. A readiness failure may correctly remove a degraded pod; making it always succeed can route requests to a broken application.

```bash
kubectl -n <namespace> describe pod <pod>
```

## Readiness and liveness probe failures — recommendations and uncertainty

Recommend correcting the endpoint or timing only after evidence. Consider a startup probe for slow initialization. Never disable health checks as an unexplained fix. Verify readiness recovery and restart stability separately.
