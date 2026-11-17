# OOMKilled: memory pressure and container termination

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## OOMKilled: memory pressure and container termination — evidence

Record termination reason OOMKilled, exit code and configured memory requests/limits. Exit code 137 alone is not proof of a memory leak. Historical termination status is not necessarily a current outage. Compare repeated terminations with the application's memory trend and node pressure.

## OOMKilled: memory pressure and container termination — investigation

Distinguish a workload exceeding its memory budget from node-wide pressure. Review working-set growth, traffic, batch size and recent changes using existing metrics access. A steady increase across comparable load supports investigating retention or a leak; a short peak may be legitimate workload demand.

```bash
kubectl -n <namespace> get pod <pod> -o yaml
```

## OOMKilled: memory pressure and container termination — recommendations and uncertainty

Ask the owner to profile allocation or tune concurrency. A measured limit increase can mitigate pressure but may hide a leak and affect scheduling. Never assert a leak merely because a retrieved postmortem describes one. Validate recovery with stable memory and no new OOM terminations.
