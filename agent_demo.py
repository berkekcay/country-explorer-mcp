"""A small agent that drives the Data Explorer MCP server, for the writeup transcript.

It starts server.py over STDIO with the MCP Python client, hands the server's tools to an
Ollama model (default gpt-oss:20b), runs the tool calls the model asks for through the MCP
session, and prints every step. This is a test harness; the server itself is meant for
any MCP client (Claude Desktop, an IDE).

    pip install -r requirements-dev.txt
    OLLAMA_HOST=http://<ollama-server>:11434 python agent_demo.py "What currency does Japan use?"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from ollama import AsyncClient

MODEL = os.environ.get("AGENT_MODEL", "gpt-oss:20b")
SERVER = Path(__file__).with_name("server.py")
SYSTEM = (
    "You answer questions about countries. Use the tools for every fact; do not answer from "
    "memory. If a tool returns an error, explain it to the user in one sentence."
)


def to_ollama_tool(tool) -> dict:
    return {
        "type": "function",
        "function": {"name": tool.name, "description": tool.description, "parameters": tool.inputSchema},
    }


def tool_text(result) -> str:
    if result.structuredContent:
        payload = result.structuredContent.get("result", result.structuredContent)
        return json.dumps(payload, ensure_ascii=False)
    return "".join(getattr(c, "text", "") for c in result.content)


async def run(question: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=dict(os.environ))
    llm = AsyncClient(host=os.environ.get("OLLAMA_HOST"))
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        print(f"[mcp] connected, tools: {', '.join(t.name for t in tools)}")
        print(f"[user] {question}")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
        for _ in range(6):  # at most six model turns
            reply = (
                await llm.chat(
                    model=MODEL,
                    messages=messages,
                    tools=[to_ollama_tool(t) for t in tools],
                    options={"temperature": 0},
                )
            ).message
            messages.append(reply)
            if not reply.tool_calls:
                print(f"[agent] {reply.content.strip()}")
                return
            for call in reply.tool_calls:
                args = dict(call.function.arguments)
                print(f"[tool call] {call.function.name}({json.dumps(args, ensure_ascii=False)})")
                text = tool_text(await session.call_tool(call.function.name, args))
                print(f"[tool result] {text[:600]}{' ...' if len(text) > 600 else ''}")
                messages.append({"role": "tool", "content": text, "tool_name": call.function.name})
        print("[agent] (stopped after six turns)")


if __name__ == "__main__":
    asyncio.run(run(" ".join(sys.argv[1:]) or "Which countries border Turkey, and what currency does it use?"))
