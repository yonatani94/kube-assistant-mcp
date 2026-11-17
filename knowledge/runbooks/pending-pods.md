# Pending Pods: scheduling and volume constraints

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## Pending Pods: scheduling and volume constraints — evidence

A Pending pod may have no container statuses or logs. Read PodScheduled conditions and FailedScheduling events. Record messages about insufficient CPU/memory, untolerated taints, node affinity and unbound PersistentVolumeClaims. Pending is broader than an unschedulable state.

## Pending Pods: scheduling and volume constraints — investigation

Compare resource requests with allocatable capacity, not only observed utilization. Check selectors and tolerations against eligible nodes. If a volume is unbound, review PVC events and storage topology through an operator with permission. Increasing retries cannot solve an impossible placement constraint.

```bash
kubectl -n <namespace> describe pod <pod>
```

## Pending Pods: scheduling and volume constraints — recommendations and uncertainty

Recommend adjusting demonstrated constraints or provisioning capacity through reviewed infrastructure changes. Do not remove taints broadly or reduce requests without workload evidence. Recheck PodScheduled and container startup after the approved correction.
