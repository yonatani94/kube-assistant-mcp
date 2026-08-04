"""Shared pytest fixtures. We build lightweight fake objects that mimic the
shape of kubernetes.client models (metadata.name, status.container_statuses...)
without importing the real `kubernetes` package, so the whole test suite runs
with zero cluster access and zero network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def make_fake_pod(
    name: str,
    namespace: str = "default",
    restart_count: int = 0,
    waiting_reason: str | None = None,
    waiting_message: str = "",
    last_terminated_reason: str | None = None,
    last_terminated_exit_code: int | None = None,
    container_name: str = "app",
    node_name: str = "node-1",
):
    """Builds a SimpleNamespace tree shaped like a kubernetes V1Pod, with just
    the fields our K8sClient._classify_pod code actually reads.
    """
    waiting = SimpleNamespace(reason=waiting_reason, message=waiting_message) if waiting_reason else None
    terminated = None
    last_terminated = (
        SimpleNamespace(reason=last_terminated_reason, exit_code=last_terminated_exit_code)
        if last_terminated_reason
        else None
    )

    container_status = SimpleNamespace(
        name=container_name,
        restart_count=restart_count,
        state=SimpleNamespace(waiting=waiting, terminated=terminated),
        last_state=SimpleNamespace(terminated=last_terminated) if last_terminated else SimpleNamespace(terminated=None),
    )

    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        status=SimpleNamespace(container_statuses=[container_status]),
        spec=SimpleNamespace(node_name=node_name),
    )


@pytest.fixture
def fake_pod_factory():
    return make_fake_pod
