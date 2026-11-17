# CrashLoopBackOff: repeated application exits

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## CrashLoopBackOff: repeated application exits — evidence

CrashLoopBackOff is restart backoff, not a root cause. Collect the exit reason, exit code, restart count and previous container log for the affected container. Check init containers separately. A Python traceback, panic or missing environment variable is stronger evidence than the backoff label alone.

## CrashLoopBackOff: repeated application exits — investigation

Compare the failure start with the last image or configuration rollout. Read the first meaningful exception, not only the final stack line. Verify referenced configuration key names without printing Secret values. If the process exits successfully but is expected to run continuously, inspect its entrypoint and workload type.

```bash
kubectl -n <namespace> logs <pod> -c <container> --previous --tail=100
```

## CrashLoopBackOff: repeated application exits — recommendations and uncertainty

Recommend correcting the proven configuration or application defect through the deployment pipeline. A rollback needs an owner and a known-good revision. Do not claim that every restart is caused by a recent deployment. Liveness failure and OOM can also cause restarts.
