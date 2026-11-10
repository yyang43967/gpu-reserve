"""The test run cannot reach the network beyond this machine."""

import socket

import openai
import pytest
from pytest_socket import SocketConnectBlockedError


def test_outside_hosts_are_unreachable() -> None:
    with pytest.raises(SocketConnectBlockedError):
        socket.create_connection(("192.0.2.1", 443), timeout=1)


def _blocked(exc: BaseException) -> bool:
    """Whether the block is anywhere in the error: its causes or, when the client tried
    several addresses at once, the exception group."""
    if isinstance(exc, SocketConnectBlockedError):
        return True
    if isinstance(exc, BaseExceptionGroup) and any(_blocked(e) for e in exc.exceptions):
        return True
    cause = exc.__cause__ or exc.__context__
    return cause is not None and _blocked(cause)


async def test_a_paid_api_call_is_stopped_before_it_leaves() -> None:
    client = openai.AsyncOpenAI(api_key="sk-test", max_retries=0)

    with pytest.raises(BaseException) as info:
        await client.models.list()

    assert _blocked(info.value)
