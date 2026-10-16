# Data Explorer MCP server

An MCP server that gives an agent two read-only tools over the
[REST Countries](https://restcountries.com) API: search countries by name, and get one
country's currencies, languages, borders and flag. It runs over STDIO.

Week 4 of AI-Augmented Software Engineering. See [writeup.md](writeup.md) for the agent
transcript, the error handling and what I learned.

## Install

Python 3.10 or newer.

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

`mcp` is pinned to 1.30.0 on purpose: version 2 renamed `FastMCP` to `MCPServer`, so the
unpinned starter breaks on a fresh install.

### API key

REST Countries retired its old no-key API (`/v3.1`) in 2026. The current v5 API needs a key;
the free plan allows 1,000 requests a month. Sign up at https://restcountries.com/sign-up,
then either:

- put it in a `.env` file next to `server.py` (copy `.env.example`; `.env` is gitignored), or
- set `RESTCOUNTRIES_API_KEY` in the MCP client config (below).

Without a key the tools still answer, with a `missing_api_key` error that says what to do.

## Run

```bash
python server.py
```

It waits for an MCP client on stdin/stdout, so you normally let the client start it.

## Register in an MCP client

**Claude Desktop.** Settings, Developer, Edit Config, then add the server to
`claude_desktop_config.json` (use absolute paths; on Windows double the backslashes) and
restart Claude Desktop:

```json
{
  "mcpServers": {
    "data-explorer": {
      "command": "C:\\path\\to\\country-explorer-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\country-explorer-mcp\\server.py"],
      "env": { "RESTCOUNTRIES_API_KEY": "your-key" }
    }
  }
}
```

**Claude Code.**

```bash
claude mcp add data-explorer -e RESTCOUNTRIES_API_KEY=your-key -- /path/to/.venv/bin/python /path/to/server.py
```

**VS Code (Copilot agent mode).** `.vscode/mcp.json`:

```json
{
  "servers": {
    "data-explorer": {
      "type": "stdio",
      "command": "${workspaceFolder}/.venv/Scripts/python.exe",
      "args": ["${workspaceFolder}/server.py"]
    }
  }
}
```

**A scripted agent.** `agent_demo.py` starts the server with the MCP Python client and lets
an Ollama model call the tools; this is how the transcripts in the writeup were made:

```bash
pip install -r requirements-dev.txt
OLLAMA_HOST=http://<ollama-host>:11434 python agent_demo.py "Which countries border Turkey?"
```

## Tool reference

Both tools are marked read-only (`readOnlyHint`). Every failure comes back as data, never as
a crash: `{"error": {"code": "...", "message": "..."}}`.

### `search_countries(query, limit=5)`

Finds countries whose common, official, native or alternate name contains `query`
(case-insensitive).

| Parameter | Type | Rules |
| --- | --- | --- |
| `query` | string | 1 to 100 characters; letters, spaces and `' - . , ( )` only |
| `limit` | integer | 1 to 25 |

Example: `search_countries("united", 3)`

```json
{
  "query": "united",
  "count": 3,
  "results": [
    {"name": "Mexico", "official_name": "United Mexican States", "code": "MEX",
     "capital": "Mexico City", "region": "Americas", "population": 131231919},
    {"name": "Tanzania", "official_name": "United Republic of Tanzania", "code": "TZA",
     "capital": "Dodoma", "region": "Africa", "population": 70038850},
    {"name": "United Arab Emirates", "official_name": "United Arab Emirates", "code": "ARE",
     "capital": "Abu Dhabi", "region": "Asia", "population": 11027129}
  ]
}
```

No match is not an error: `search_countries("Atlantis")` returns `count: 0`, an empty
`results` list and a `message` suggesting a shorter or English name.

### `get_country_details(name)`

One country by name (`"Turkey"`, `"Republic of Türkiye"`) or ISO code (`"TR"`, `"TUR"`).
It tries an exact name or code first and falls back to a name search. Border codes are turned
into names with one extra request; if that request fails, the codes are still returned.

| Parameter | Type | Rules |
| --- | --- | --- |
| `name` | string | 1 to 100 characters; a country name or a 2 or 3 letter ISO code |

Example: `get_country_details("jp")`

```json
{
  "name": "Japan",
  "official_name": "Japan",
  "codes": {"alpha_2": "JP", "alpha_3": "JPN"},
  "capital": "Tokyo",
  "region": "Asia",
  "subregion": "Eastern Asia",
  "population": 122650000,
  "currencies": [{"code": "JPY", "name": "Japanese yen", "symbol": "¥"}],
  "languages": [{"code": "ja", "name": "Japanese", "native_name": "日本語"}],
  "borders": [],
  "flag": {
    "emoji": "🇯🇵",
    "png": "https://flags.restcountries.com/v5/w640/jp.png",
    "svg": "https://flags.restcountries.com/v5/svg/jp.svg",
    "description": "The flag of Japan features a crimson-red circle at the center of a white field."
  }
}
```

A name that fits several countries returns the candidates, so the agent can ask:

```json
{"error": {"code": "ambiguous", "message": "'Guine' matches 4 countries; ask which one is meant",
  "candidates": ["Equatorial Guinea", "Guinea", "Guinea-Bissau", "Papua New Guinea"]}}
```

### Error codes

| Code | When |
| --- | --- |
| `invalid_input` | empty, too long or odd characters; `limit` out of range; or the API returned 400 |
| `not_found` | `get_country_details` found no country |
| `ambiguous` | `get_country_details` matched several; see `candidates` |
| `missing_api_key` / `bad_api_key` | no key set, or the API returned 401 |
| `quota_or_plan` | 403: monthly limit used up, or a paid-only field |
| `rate_limited` | 429; `retry_after` holds the seconds to wait when the API sends it |
| `timeout` / `network_error` | no answer within 10 s, or the connection failed |
| `upstream_error` / `bad_response` / `api_retired` | 5xx, unreadable JSON, or 410 |
| `demo_key` | the public demo key was used; it only returns a sample country |

Answers are cached in memory for 10 minutes to save the monthly quota.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

49 tests. Most run the tools against a fake API (httpx `MockTransport`) for every error path;
one starts `server.py` over real STDIO with the MCP client and calls both tools. No network
or key is needed.
