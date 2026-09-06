import pytest

from app.services.search_sources import (
    InvalidHHSearchURL,
    parse_hh_search_url,
    query_pairs_to_multidict,
)


def test_parse_search_url_preserves_repeated_parameters_and_unknowns():
    parsed = parse_hh_search_url(
        "https://tambov.hh.ru/search/vacancy?text=CIO&area=1&area=2"
        "&search_field=name&search_field=company_name&future_param=x"
    )

    assert parsed.host == "tambov.hh.ru"
    assert parsed.parameters["area"] == ("1", "2")
    assert parsed.parameters["search_field"] == ("name", "company_name")
    assert parsed.unsupported_parameters == ("future_param",)
    assert parsed.query_pairs_json() == [
        {"key": "text", "value": "CIO"},
        {"key": "area", "value": "1"},
        {"key": "area", "value": "2"},
        {"key": "search_field", "value": "name"},
        {"key": "search_field", "value": "company_name"},
        {"key": "future_param", "value": "x"},
    ]


def test_multidict_roundtrip_keeps_duplicate_values():
    grouped = query_pairs_to_multidict(
        [
            {"key": "area", "value": "1"},
            {"key": "area", "value": "2"},
            {"key": "text", "value": "CDTO"},
        ]
    )
    assert grouped == {"area": ["1", "2"], "text": ["CDTO"]}


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/search/vacancy?text=CIO",
        "https://hh.ru/vacancy/123",
        "ftp://hh.ru/search/vacancy?text=CIO",
        "",
    ],
)
def test_rejects_non_search_urls(url):
    with pytest.raises(InvalidHHSearchURL):
        parse_hh_search_url(url)
