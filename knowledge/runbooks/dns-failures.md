# DNS resolution failures: service names and resolver health

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## DNS resolution failures: service names and resolver health — evidence

Application logs such as “lookup ... no such host” indicate name-resolution failure, not necessarily CoreDNS failure. Record the exact hostname, namespace, affected callers and first failure time. Separate NXDOMAIN-like errors from timeouts and connection-refused messages.

## DNS resolution failures: service names and resolver health — investigation

Verify service spelling and namespace qualification. Compare impacted callers with healthy callers, and ask an authorized operator to check Service/EndpointSlice existence and resolver health. A dependency name changed by a Helm release may affect one application while cluster DNS remains healthy.

```bash
kubectl -n <namespace> logs <pod> -c <container> --tail=100
```

## DNS resolution failures: service names and resolver health — recommendations and uncertainty

Recommend correcting a proven service name or escalating a broader DNS outage. Do not restart CoreDNS based solely on a single application's error. In this read-only investigation, active DNS probes or new debug pods are suggestions for an operator, not executed tools.
