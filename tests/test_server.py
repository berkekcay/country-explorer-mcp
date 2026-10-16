"""Tests for both tools against a fake REST Countries v5 API (httpx.MockTransport)."""

from __future__ import annotations

import httpx
import pytest

import server

TURKEY = {
    "names": {"common": "Turkey", "official": "Republic of Türkiye"},
    "codes": {"alpha_2": "TR", "alpha_3": "TUR"},
    "capitals": [{"name": "Ankara", "attributes": {"primary": True}}],
    "region": "Asia",
    "subregion": "Western Asia",
    "population": 85664944,
    "currencies": [{"code": "TRY", "name": "Turkish lira", "symbol": "₺"}],
    "languages": [{"iso639_1": "tr", "iso639_3": "tur", "name": "Turkish", "native_name": "Türkçe"}],
    "borders": ["ARM", "AZE", "BGR", "GEO", "GRC", "IRN", "IRQ", "SYR"],
    "flag": {
        "emoji": "🇹🇷",
        "url_png": "https://flags.restcountries.com/v5/w640/tr.png",
        "url_svg": "https://flags.restcountries.com/v5/svg/tr.svg",
        "description": "Red with a white crescent and star.",
    },
}
NEIGHBOURS = [
    {"names": {"common": name}, "codes": {"alpha_3": code}}
    for code, name in [
        ("ARM", "Armenia"), ("AZE", "Azerbaijan"), ("BGR", "Bulgaria"), ("GEO", "Georgia"),
        ("GRC", "Greece"), ("IRN", "Iran"), ("IRQ", "Iraq"), ("SYR", "Syria"),
    ]
]


def ok(objects: list[dict], **data) -> httpx.Response:
    return httpx.Response(200, json={"data": {"objects": objects, "meta": {"total": len(objects)}, **data}})


@pytest.fixture
def api(monkeypatch):
    """Route the server's HTTP client to a handler the test sets with api.handle(...)."""
    calls: list[httpx.Request] = []
    state = {"handler": lambda request: ok([])}

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        result = state["handler"](request)
        if isinstance(result, Exception):
            raise result
        return result

    client = httpx.Client(base_url=server.BASE_URL, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setattr(server, "_cache", {})
    monkeypatch.setenv("RESTCOUNTRIES_API_KEY", "test-key")

    class Api:
        def handle(self, fn):
            state["handler"] = fn

        requests = calls

    return Api()


def error_code(result: dict) -> str:
    assert set(result) == {"error"}, result
    return result["error"]["code"]


# --- search_countries ----------------------------------------------------------------------


def test_search_returns_model_friendly_rows(api):
    api.handle(lambda r: ok([TURKEY]))
    result = server.search_countries("türk", 3)
    assert result == {
        "query": "türk",
        "count": 1,
        "results": [
            {
                "name": "Turkey",
                "official_name": "Republic of Türkiye",
                "code": "TUR",
                "capital": "Ankara",
                "region": "Asia",
                "population": 85664944,
            }
        ],
    }
    request = api.requests[0]
    assert request.url.path == "/countries/v5/name"
    assert request.url.params["q"] == "türk" and request.url.params["limit"] == "3"
    assert request.headers["Authorization"] == "Bearer test-key"


def test_search_with_no_matches_is_not_an_error(api):
    api.handle(lambda r: ok([]))
    result = server.search_countries("Atlantis")
    assert result["count"] == 0 and result["results"] == []
    assert "No countries match" in result["message"]


@pytest.mark.parametrize(
    "query, limit",
    [("", 5), ("   ", 5), ("x" * 101, 5), ("12345", 5), ("<script>", 5), ("DROP;--", 5),
     ("Germany", 0), ("Germany", 26), ("Germany", True), (None, 5)],
)
def test_search_rejects_bad_input_without_calling_the_api(api, query, limit):
    assert error_code(server.search_countries(query, limit)) == "invalid_input"
    assert api.requests == []


def test_search_accepts_real_names_with_punctuation(api):
    api.handle(lambda r: ok([]))
    for name in ["Côte d'Ivoire", "Guinea-Bissau", "St. Lucia", "Korea, Republic of", "Åland Islands"]:
        assert "error" not in server.search_countries(name)


@pytest.mark.parametrize(
    "response, code",
    [
        (httpx.Response(400, json={"errors": [{"message": "q is empty"}]}), "invalid_input"),
        (httpx.Response(401, json={"errors": [{"message": "bad key"}]}), "bad_api_key"),
        (httpx.Response(403, json={"errors": [{"message": "monthly limit exceeded"}]}), "quota_or_plan"),
        (httpx.Response(410), "api_retired"),
        (httpx.Response(429, headers={"Retry-After": "7"}), "rate_limited"),
        (httpx.Response(500), "upstream_error"),
        (httpx.Response(503, text="<html>down</html>"), "upstream_error"),
        (httpx.Response(200, text="not json"), "bad_response"),
        (httpx.Response(200, json={"data": {"objects": "nope"}}), "bad_response"),
        (httpx.Response(200, json={"success": False}), "bad_response"),
    ],
)
def test_search_turns_http_problems_into_structured_errors(api, response, code):
    api.handle(lambda r: response)
    assert error_code(server.search_countries("Germany")) == code


def test_rate_limit_error_carries_retry_after(api):
    api.handle(lambda r: httpx.Response(429, headers={"Retry-After": "7"}))
    error = server.search_countries("Germany")["error"]
    assert error["retry_after"] == "7" and "7 s" in error["message"]


@pytest.mark.parametrize(
    "exc, code",
    [
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectTimeout("slow"), "timeout"),
        (httpx.ConnectError("dns failed"), "network_error"),
        (httpx.RemoteProtocolError("reset"), "network_error"),
    ],
)
def test_search_handles_timeouts_and_network_errors(api, exc, code):
    api.handle(lambda r: exc)
    assert error_code(server.search_countries("Germany")) == code


def test_missing_api_key_is_reported(api, monkeypatch):
    monkeypatch.delenv("RESTCOUNTRIES_API_KEY")
    error = server.search_countries("Germany")["error"]
    assert error["code"] == "missing_api_key" and "restcountries.com/sign-up" in error["message"]
    assert api.requests == []


def test_demo_key_sample_is_not_passed_off_as_data(api):
    api.handle(lambda r: ok([TURKEY], _demo={"message": "demo"}))
    assert error_code(server.search_countries("Germany")) == "demo_key"


def test_repeated_calls_are_served_from_the_cache(api):
    api.handle(lambda r: ok([TURKEY]))
    server.search_countries("Turkey")
    server.search_countries("Turkey")
    assert len(api.requests) == 1


# --- get_country_details -------------------------------------------------------------------


def turkey_api(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/countries/v5/names.common/Turkey":
        return ok([TURKEY])
    if path == "/countries/v5/borders/TUR":
        return ok(NEIGHBOURS)
    return ok([])


def test_details_have_currencies_languages_borders_and_flag(api):
    api.handle(turkey_api)
    result = server.get_country_details("Turkey")
    assert result["name"] == "Turkey" and result["codes"] == {"alpha_2": "TR", "alpha_3": "TUR"}
    assert result["currencies"] == [{"code": "TRY", "name": "Turkish lira", "symbol": "₺"}]
    assert result["languages"] == [{"code": "tr", "name": "Turkish", "native_name": "Türkçe"}]
    assert result["borders"][0] == {"code": "ARM", "name": "Armenia"}
    assert [b["name"] for b in result["borders"]][-1] == "Syria"
    assert result["flag"] == {
        "emoji": "🇹🇷",
        "png": "https://flags.restcountries.com/v5/w640/tr.png",
        "svg": "https://flags.restcountries.com/v5/svg/tr.svg",
        "description": "Red with a white crescent and star.",
    }


def test_details_accept_an_iso_code(api):
    api.handle(lambda r: ok([TURKEY]) if r.url.path == "/countries/v5/codes.alpha_2/TR" else ok([]))
    assert server.get_country_details("tr")["name"] == "Turkey"


def test_details_fall_back_to_search_when_there_is_one_match(api):
    api.handle(lambda r: ok([TURKEY]) if r.url.path == "/countries/v5/name" else ok([]))
    assert server.get_country_details("Türkiye")["name"] == "Turkey"


def test_details_report_ambiguous_names_with_candidates(api):
    guinea = [{"names": {"common": n}} for n in ["Guinea", "Guinea-Bissau", "Equatorial Guinea"]]
    api.handle(lambda r: ok(guinea) if r.url.path == "/countries/v5/name" else ok([]))
    error = server.get_country_details("Guine")["error"]
    assert error["code"] == "ambiguous"
    assert error["candidates"] == ["Guinea", "Guinea-Bissau", "Equatorial Guinea"]


def test_details_not_found(api):
    api.handle(lambda r: ok([]))
    assert error_code(server.get_country_details("Atlantis")) == "not_found"


def test_details_keep_border_codes_when_the_name_lookup_fails(api):
    def handler(request):
        if request.url.path == "/countries/v5/borders/TUR":
            return httpx.Response(500)
        return turkey_api(request)

    api.handle(handler)
    result = server.get_country_details("Turkey")
    assert result["borders"][0] == {"code": "ARM", "name": None}


def test_island_has_no_borders_and_no_extra_call(api):
    iceland = {**TURKEY, "names": {"common": "Iceland"}, "codes": {"alpha_3": "ISL"}, "borders": []}
    api.handle(lambda r: ok([iceland]) if r.url.path == "/countries/v5/names.common/Iceland" else ok([]))
    assert server.get_country_details("Iceland")["borders"] == []
    assert all("/borders/" not in r.url.path for r in api.requests)


@pytest.mark.parametrize("name", ["", "   ", "123", "a" * 150, None, ["Turkey"]])
def test_details_reject_bad_input(api, name):
    assert error_code(server.get_country_details(name)) == "invalid_input"
    assert api.requests == []


@pytest.mark.parametrize(
    "exc_or_response, code",
    [(httpx.ReadTimeout("slow"), "timeout"), (httpx.Response(502), "upstream_error"), (httpx.Response(401), "bad_api_key")],
)
def test_details_errors_are_structured(api, exc_or_response, code):
    api.handle(lambda r: exc_or_response)
    assert error_code(server.get_country_details("Germany")) == code


def test_names_with_spaces_are_url_encoded(api):
    api.handle(lambda r: ok([]))
    server.get_country_details("South Africa")
    assert api.requests[0].url.raw_path.startswith(b"/countries/v5/names.common/South%20Africa")
