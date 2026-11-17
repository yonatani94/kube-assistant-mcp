# Resource exhaustion: CPU, memory, disk and capacity

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## Resource exhaustion: CPU, memory, disk and capacity — evidence

Resource exhaustion is a hypothesis requiring a specific constrained resource. Correlate CPU throttling, memory pressure, disk pressure, eviction messages or scheduling insufficiency with workload impact. Slow requests alone do not prove CPU shortage.

## Resource exhaustion: CPU, memory, disk and capacity — investigation

Compare per-container requests/limits, node allocatable capacity and existing time-series metrics. Check whether all replicas or one node are affected. Disk pressure and ephemeral-storage growth require different investigation from memory OOM; do not treat them as interchangeable.

```bash
kubectl -n <namespace> get events --field-selector involvedObject.name=<pod>
```

## Resource exhaustion: CPU, memory, disk and capacity — recommendations and uncertainty

Recommend bounded mitigation through an owner: reduce batch concurrency, correct resource requests or provision demonstrated capacity. Avoid generic limit increases across every workload. Validate both application recovery and remaining node headroom after any approved change.
