# ImagePullBackOff: image retrieval fails

Reviewed: 2026-11-17
Owner: example-platform-team
Scope: illustrative operational guidance; verify against your environment.

## ImagePullBackOff: image retrieval fails — evidence

Collect the exact image reference and pull event message. ErrImagePull and ImagePullBackOff indicate image retrieval failure. “manifest unknown” suggests a tag or digest problem; unauthorized suggests registry credentials or identity; timeout suggests registry connectivity.

## ImagePullBackOff: image retrieval fails — investigation

Check the image exists in the intended registry and architecture. Verify imagePullSecrets references and service-account configuration without displaying credential values. On AKS or EKS, ask the platform owner to verify the workload/node registry identity path rather than assuming a password fix.

```bash
kubectl -n <namespace> describe pod <pod>
```

## ImagePullBackOff: image retrieval fails — recommendations and uncertainty

Recommend correcting the specific image reference or approved registry access configuration in source control. Retry after the underlying issue is fixed. Do not rotate credentials or alter network policies automatically. Success requires an image pulled event and a started container.
