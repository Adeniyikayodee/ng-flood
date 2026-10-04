"""MCP server with one tool: Google's flood forecast for a Nigerian coordinate.

Keys come from GOOGLE_FLOOD_API_KEY and NG_POSTCODE_API_KEY, never from tool
arguments or results. Install with `pip install "ng-flood[mcp]"`.
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import version
from typing import Annotated, Any

import httpx
from anyio import to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .client import Client, Failure

INSTRUCTIONS = (
    "Checks a coordinate in Nigeria against Google's flood forecasts. A verdict of NONE "
    "means nothing is forecast now, not that the place is safe. UNKNOWN means the data "
    "was missing or stale. Always pass on the notice."
)


def create_server(
    google_key: str, nipost_key: str | None = None, http: httpx.Client | None = None
) -> MCPServer[Any]:
    """Build the server. Pass `http` to route calls through your own client, as tests do."""
    server: MCPServer[Any] = MCPServer(
        name="ng-flood",
        title="Nigeria Flood Forecast",
        instructions=INSTRUCTIONS,
        version=version("ng-flood"),
    )

    @server.tool(
        title="Check the flood forecast at a Nigerian location",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
        structured_output=True,
    )
    async def check_flood_forecast(
        latitude: Annotated[
            float, Field(ge=-90, le=90, description="Decimal degrees, e.g. 7.8023.")
        ],
        longitude: Annotated[
            float, Field(ge=-180, le=180, description="Decimal degrees, e.g. 6.7333.")
        ],
    ) -> dict[str, Any]:
        """Return the verdict (FORECAST, NONE or UNKNOWN), the riverine and flash flood
        findings, any gaps in the data, and the NIPOST postcode area when a key is set."""
        with Client(google_key, nipost_key, http=http) as client:
            result = await to_thread.run_sync(client.assess, latitude, longitude)
        if isinstance(result, Failure):
            raise ToolError(str(result))
        return result.as_dict()

    return server


def main() -> None:
    key = os.environ.get("GOOGLE_FLOOD_API_KEY")
    if not key:
        sys.exit("set GOOGLE_FLOOD_API_KEY")
    create_server(key, os.environ.get("NG_POSTCODE_API_KEY")).run()
