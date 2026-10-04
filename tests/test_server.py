"""The tool through a real MCP client, in process, with both APIs mocked."""

from __future__ import annotations

import httpx
import pytest
from mcp import Client
from mcp.types import TextContent
from test_client import LAT, LNG, STATUS, Handler, server

from ng_flood.server import create_server


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def mcp(handler: Handler) -> Client:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return Client(create_server("google", "nipost", http=http))


@pytest.mark.anyio
async def test_the_tool_returns_the_verdict_and_the_notice() -> None:
    issued = STATUS | {"issuedTime": "2999-01-01T00:00:00Z"}
    async with mcp(server([issued])) as client:
        result = await client.call_tool("check_flood_forecast", {"latitude": LAT, "longitude": LNG})
    found = result.structured_content
    assert found is not None
    assert (found["verdict"], found["area"]) == ("FORECAST", "KG-05-A12-FK")
    assert found["riverine"]["issued"] == "2999-01-01T00:00:00+00:00"
    assert "CC BY 4.0" in found["notice"]


@pytest.mark.anyio
async def test_a_failure_is_a_tool_error() -> None:
    async with mcp(server(broken=("floodStatus",))) as client:
        result = await client.call_tool("check_flood_forecast", {"latitude": LAT, "longitude": LNG})
    assert result.is_error
    assert isinstance(result.content[0], TextContent)
    assert "HTTP 503" in result.content[0].text
