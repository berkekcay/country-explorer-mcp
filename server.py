"""Data Explorer — MCP server skeleton for Week 4.

Wraps the free, no-key REST Countries API (https://restcountries.com) and exposes it
to an agent as MCP tools over STDIO. One tool is implemented as a worked example;
finish the second (and improve error handling) to complete the assignment.

Setup:
    pip install -r requirements.txt
Run (STDIO) — register this command in your MCP client (e.g. Claude Desktop / an IDE):
    python server.py
"""
import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://restcountries.com/v3.1"
mcp = FastMCP("data-explorer")


@mcp.tool()
def search_countries(query: str, limit: int = 5) -> list[dict]:
    """Search countries by name. Returns a list of {name, capital, region, population}.

    This tool is a WORKED EXAMPLE — study how it validates input, calls the API,
    handles errors, and returns model-friendly data.
    """
    if not query.strip():
        return [{"error": "query must not be empty"}]
    try:
        resp = httpx.get(f"{BASE_URL}/name/{query}", timeout=10)
        if resp.status_code == 404:
            return []  # no matches is not an error
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return [{"error": f"upstream request failed: {exc}"}]

    results = []
    for c in resp.json()[:limit]:
        results.append(
            {
                "name": c.get("name", {}).get("common"),
                "capital": (c.get("capital") or [None])[0],
                "region": c.get("region"),
                "population": c.get("population"),
            }
        )
    return results


# TODO: Implement a SECOND tool, e.g. get_country_details(name) returning currencies,
# languages, borders, and flag. Reuse the error-handling pattern above.
# @mcp.tool()
# def get_country_details(name: str) -> dict:
#     ...


if __name__ == "__main__":
    mcp.run()
