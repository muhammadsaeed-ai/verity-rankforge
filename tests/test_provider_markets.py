import pytest

from rankforge.providers import BraveProvider, DataForSEOProvider, ProviderError


@pytest.mark.parametrize("market", ["Germany", "France", "New Zealand"])
def test_brave_rejects_unsupported_market_before_network_call(market):
    provider = BraveProvider("test-key", timeout=2)
    with pytest.raises(ProviderError, match="Unsupported Brave Search market"):
        provider.search("sample keyword", market, "en", 10)


@pytest.mark.parametrize("market", ["Germany", "France", "New Zealand"])
def test_dataforseo_rejects_unsupported_market_instead_of_returning_us(market):
    provider = DataForSEOProvider("test-login", "test-password", timeout=2)
    with pytest.raises(ProviderError, match="Unsupported DataForSEO market"):
        provider.search("sample keyword", market, "en", 10)


def test_provider_market_aliases_are_supported_without_network(monkeypatch):
    import rankforge.providers as providers

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"web": {"results": [{"title": "Result", "url": "https://example.com", "description": "Snippet"}]}}

    def fake_get(url, headers, params, timeout):
        captured.update(params)
        return Response()

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    BraveProvider("test-key", timeout=2).search("keyword", "US", "en", 5)
    assert captured["country"] == "US"
