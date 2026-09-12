import asyncio

from ccxt.base.errors import NetworkError
from ccxt.pro.bingx import bingx
from waterfallhunter.core.ws_streamer import _install_ccxt_ws_client_reset_compat


class _PingTask:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _MissingResetClient:
    def __init__(self) -> None:
        self.ping_looper = _PingTask()
        self.rejected = None

    async def send(self, _message) -> None:
        raise ConnectionError("closing transport")

    def reject(self, error):
        self.rejected = error
        return error


def test_missing_reset_compat_handles_bingx_pong_failure() -> None:
    client = _MissingResetClient()
    assert _install_ccxt_ws_client_reset_compat(_MissingResetClient) is True

    asyncio.run(bingx().pong(client, "Ping"))

    assert isinstance(client.rejected, NetworkError)
    assert client.ping_looper.cancelled is True


def test_existing_reset_is_never_overridden() -> None:
    class ExistingClient:
        def reset(self, error):
            return error

    original = ExistingClient.reset
    assert _install_ccxt_ws_client_reset_compat(ExistingClient) is False
    assert ExistingClient.reset is original


def test_real_ccxt_client_has_reset_after_ws_streamer_import() -> None:
    from ccxt.async_support.base.ws.client import Client

    assert callable(getattr(Client, "reset", None))
