# Starter — Data Explorer MCP Server

A `FastMCP` server over the free, no-key **REST Countries** API. `search_countries` is a
worked example; you implement a second tool and harden both.

## Setup
```bash
pip install -r requirements.txt
```

## Run (STDIO)
```bash
python server.py
```
This speaks MCP over STDIO. Register the command in an MCP client (Claude Desktop or an
MCP-aware IDE), then invoke the tools from the client's chat.

## Register in Claude Desktop (example)
Add to your client's MCP config:
```json
{
  "mcpServers": {
    "data-explorer": { "command": "python", "args": ["/absolute/path/to/server.py"] }
  }
}
```

## Your job
- Implement `get_country_details(name)` (currencies, languages, borders, flag URL).
- Make both tools handle empty/invalid input, HTTP errors, timeouts, and no results.
