"""End to end over STDIO: start server.py as an MCP client would and call its tools.

No network is needed: the calls below fail validation, or fail on the missing API key,
before any request leaves the machine.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = Path(__file__).resolve().parents[1] / "server.py"


async def _session_calls() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "RESTCOUNTRIES_API_KEY"}
    env["RESTCOUNTRIES_API_KEY"] = ""  # also overrides a local .env, which never replaces a set value
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        empty = await session.call_tool("search_countries", {"query": "  "})
        no_key = await session.call_tool("get_country_details", {"name": "Turkey"})
        return {"tools": tools.tools, "empty": empty, "no_key": no_key}


def _payload(result) -> dict:
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text)


def test_tools_are_listed_and_errors_come_back_as_data():
    out = asyncio.run(_session_calls())
    tools = {t.name: t for t in out["tools"]}
    assert set(tools) == {"search_countries", "get_country_details"}
    assert tools["get_country_details"].annotations.readOnlyHint is True
    assert "name" in tools["get_country_details"].inputSchema["properties"]

    assert not out["empty"].isError
    assert _payload(out["empty"])["error"]["code"] == "invalid_input"
    assert _payload(out["no_key"])["error"]["code"] == "missing_api_key"
