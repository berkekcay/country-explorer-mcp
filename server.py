"""Data Explorer: an MCP server over the REST Countries API (v5).

Exposes two read-only tools to an agent over STDIO:
  search_countries(query, limit)  find countries by name
  get_country_details(name)       currencies, languages, borders and flag of one country

REST Countries retired its old no-key /v3.1 API in 2026. The v5 API needs a free API key
(https://restcountries.com/sign-up), read from the RESTCOUNTRIES_API_KEY environment
variable or from a `.env` file next to this script.

Every tool returns plain JSON. Problems never raise out of a tool: they come back as
{"error": {"code": ..., "message": ...}} so the agent can explain them or retry.

Setup:
    pip install -r requirements.txt
Run (STDIO):
    python server.py
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

BASE_URL = "https://api.restcountries.com/countries/v5"
TIMEOUT_SECONDS = 10.0
MAX_QUERY_LENGTH = 100
MAX_LIMIT = 25
CACHE_SECONDS = 600  # the free plan allows 1,000 requests a month

# Letters in any script, spaces and the punctuation real country names use
# ("Côte d'Ivoire", "Guinea-Bissau", "St. Lucia", "Timor-Leste", "Åland Islands").
_NAME_PATTERN = re.compile(r"^[^\W\d_]+(?:[ '\-.,()][^\W\d_]*)*\.?$")

mcp = FastMCP("data-explorer")
READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


# --- helpers --------------------------------------------------------------------------------


class ToolError(Exception):
    """A problem to report back to the agent as {"error": {"code", "message"}}."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}


def _load_dotenv() -> None:
    """Read KEY=VALUE lines from a .env file next to this script (no extra dependency)."""
    env_file = Path(__file__).with_name(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _api_key() -> str:
    key = os.environ.get("RESTCOUNTRIES_API_KEY", "").strip()
    if not key:
        raise ToolError(
            "missing_api_key",
            "RESTCOUNTRIES_API_KEY is not set. Get a free key at https://restcountries.com/sign-up "
            "and put it in the MCP client config or in a .env file next to server.py.",
        )
    return key


def _clean_text(value: Any, what: str) -> str:
    """Validate free text from the agent: a non-empty, reasonably short country name."""
    if not isinstance(value, str):
        raise ToolError("invalid_input", f"{what} must be text")
    text = " ".join(value.split())
    if not text:
        raise ToolError("invalid_input", f"{what} must not be empty")
    if len(text) > MAX_QUERY_LENGTH:
        raise ToolError("invalid_input", f"{what} is longer than {MAX_QUERY_LENGTH} characters")
    if not _NAME_PATTERN.match(text):
        raise ToolError(
            "invalid_input", f"{what} should be a country name or code, e.g. 'Germany' or 'DE'"
        )
    return text


_client: httpx.Client | None = None
_cache: dict[str, tuple[float, list[dict]]] = {}


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_SECONDS)
    return _client


def _get(path: str, params: dict[str, Any] | None = None) -> list[dict]:
    """GET a v5 endpoint and return `data.objects`. Raises ToolError for every failure."""
    params = dict(params or {})
    cache_key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    headers = {"Authorization": f"Bearer {_api_key()}"}
    try:
        resp = _http().get(path, params=params, headers=headers)
    except httpx.TimeoutException:
        raise ToolError("timeout", f"REST Countries did not answer within {TIMEOUT_SECONDS:.0f} s; try again") from None
    except httpx.HTTPError as exc:
        raise ToolError("network_error", f"could not reach REST Countries ({type(exc).__name__})") from None

    if resp.status_code == 404:
        return []  # an exact lookup with no match; "no results" is not an error
    if resp.status_code != 200:
        raise _status_error(resp)
    try:
        body = resp.json()
        data = body["data"]
        objects = data["objects"]
    except (ValueError, KeyError, TypeError):
        raise ToolError("bad_response", "REST Countries sent a response in an unexpected format") from None
    if not isinstance(objects, list):
        raise ToolError("bad_response", "REST Countries sent a response in an unexpected format")
    if isinstance(data, dict) and "_demo" in data:
        raise ToolError(
            "demo_key", "the demo key only returns a sample country; set a real RESTCOUNTRIES_API_KEY"
        )
    _cache[cache_key] = (time.monotonic(), objects)
    return objects


def _status_error(resp: httpx.Response) -> ToolError:
    detail = _api_message(resp)
    if resp.status_code == 400:
        return ToolError("invalid_input", f"REST Countries rejected the request: {detail}")
    if resp.status_code == 401:
        return ToolError("bad_api_key", "the RESTCOUNTRIES_API_KEY was not accepted (missing, revoked or expired)")
    if resp.status_code == 403:
        return ToolError("quota_or_plan", f"REST Countries refused the request (monthly limit or plan): {detail}")
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After")
        wait = f" Retry after {retry} s." if retry else ""
        return ToolError("rate_limited", f"too many requests to REST Countries.{wait}", retry_after=retry)
    if resp.status_code == 410:
        return ToolError("api_retired", "this REST Countries API version is no longer active")
    if resp.status_code >= 500:
        return ToolError("upstream_error", f"REST Countries had a server error ({resp.status_code}); try again later")
    return ToolError("upstream_error", f"unexpected HTTP {resp.status_code} from REST Countries")


def _api_message(resp: httpx.Response) -> str:
    try:
        errors = resp.json().get("errors") or []
        return "; ".join(str(e.get("message")) for e in errors if isinstance(e, dict)) or resp.reason_phrase
    except (ValueError, AttributeError):
        return resp.reason_phrase


def _first_capital(country: dict) -> str | None:
    capitals = country.get("capitals") or []
    primary = [c for c in capitals if (c.get("attributes") or {}).get("primary")]
    chosen = (primary or capitals or [None])[0]
    return chosen.get("name") if isinstance(chosen, dict) else None


def _names(country: dict) -> dict:
    return country.get("names") or {}


# --- tools ----------------------------------------------------------------------------------

SEARCH_FIELDS = "names.common,names.official,codes.alpha_2,codes.alpha_3,capitals,region,population"


@mcp.tool(annotations=READ_ONLY)
def search_countries(
    query: Annotated[str, Field(description="Part of a country name, e.g. 'united' or 'Türkiye'")],
    limit: Annotated[int, Field(description=f"How many results to return, 1 to {MAX_LIMIT}")] = 5,
) -> dict:
    """Search countries by name (common, official, native or alternate names).

    Returns {"query", "count", "results": [{name, official_name, code, capital, region,
    population}]}. No match is not an error: results is empty and a message says so.
    """
    try:
        text = _clean_text(query, "query")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ToolError("invalid_input", f"limit must be a whole number from 1 to {MAX_LIMIT}")
        countries = _get("/name", {"q": text, "limit": limit, "response_fields": SEARCH_FIELDS})
    except ToolError as exc:
        return exc.to_dict()

    results = [
        {
            "name": _names(c).get("common"),
            "official_name": _names(c).get("official"),
            "code": (c.get("codes") or {}).get("alpha_3"),
            "capital": _first_capital(c),
            "region": c.get("region"),
            "population": c.get("population"),
        }
        for c in countries[:limit]
    ]
    response: dict[str, Any] = {"query": text, "count": len(results), "results": results}
    if not results:
        response["message"] = f"No countries match {text!r}. Try a shorter or English name."
    return response


DETAIL_FIELDS = (
    "names.common,names.official,codes.alpha_2,codes.alpha_3,capitals,region,subregion,"
    "population,currencies,languages,borders,flag.emoji,flag.url_png,flag.url_svg,flag.description"
)


@mcp.tool(annotations=READ_ONLY)
def get_country_details(
    name: Annotated[
        str,
        Field(description="Country name ('Germany', 'Türkiye') or ISO code ('DE', 'DEU')"),
    ],
) -> dict:
    """Currencies, languages, bordering countries and flag URLs for one country.

    Tries an exact name or ISO code first, then a name search. If the search finds several
    countries, returns an "ambiguous" error with the candidates so the agent can ask.
    Returns {name, official_name, codes, capital, region, subregion, population,
    currencies: [{code, name, symbol}], languages: [{code, name, native_name}],
    borders: [{code, name}], flag: {emoji, png, svg, description}}.
    """
    try:
        text = _clean_text(name, "name")
        country = _find_one(text)
        return _details(country)
    except ToolError as exc:
        return exc.to_dict()


def _find_one(text: str) -> dict:
    lookups = []
    if re.fullmatch(r"[A-Za-z]{2,3}", text):  # looks like an ISO code
        lookups.append(f"/codes.alpha_{len(text)}/{text.upper()}")
    lookups += [f"/names.common/{quote(text)}", f"/names.official/{quote(text)}"]
    for path in lookups:
        found = _get(path, {"response_fields": DETAIL_FIELDS})
        if len(found) == 1:
            return found[0]

    candidates = _get("/name", {"q": text, "limit": 10, "response_fields": DETAIL_FIELDS})
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ToolError("not_found", f"no country called {text!r}; try search_countries first")
    names = [_names(c).get("common") for c in candidates]
    raise ToolError(
        "ambiguous",
        f"{text!r} matches {len(candidates)} countries; ask which one is meant",
        candidates=names,
    )


def _details(country: dict) -> dict:
    codes = country.get("codes") or {}
    flag = country.get("flag") or {}
    border_codes = [b for b in country.get("borders") or [] if isinstance(b, str)]
    return {
        "name": _names(country).get("common"),
        "official_name": _names(country).get("official"),
        "codes": {"alpha_2": codes.get("alpha_2"), "alpha_3": codes.get("alpha_3")},
        "capital": _first_capital(country),
        "region": country.get("region"),
        "subregion": country.get("subregion"),
        "population": country.get("population"),
        "currencies": [
            {"code": c.get("code"), "name": c.get("name"), "symbol": c.get("symbol")}
            for c in country.get("currencies") or []
        ],
        "languages": [
            {
                "code": lang.get("iso639_1") or lang.get("iso639_3"),
                "name": lang.get("name"),
                "native_name": lang.get("native_name"),
            }
            for lang in country.get("languages") or []
        ],
        "borders": _border_names(codes.get("alpha_3"), border_codes),
        "flag": {
            "emoji": flag.get("emoji"),
            "png": flag.get("url_png"),
            "svg": flag.get("url_svg"),
            "description": flag.get("description"),
        },
    }


def _border_names(alpha_3: str | None, border_codes: list[str]) -> list[dict]:
    """Turn ISO codes into names with one reverse lookup (/borders/{code}). If that call
    fails, the codes are still returned: partial context beats a failed tool call."""
    if not border_codes:
        return []
    names: dict[str, str] = {}
    if alpha_3:
        try:
            for c in _get(f"/borders/{alpha_3}", {"response_fields": "names.common,codes.alpha_3", "limit": 25}):
                code = (c.get("codes") or {}).get("alpha_3")
                if code:
                    names[code] = _names(c).get("common")
        except ToolError:
            pass
    return [{"code": code, "name": names.get(code)} for code in border_codes]


if __name__ == "__main__":
    _load_dotenv()
    mcp.run()
