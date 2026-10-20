"""Search-provider adapters. Live calls are explicit; demo data is never mislabeled."""
from __future__ import annotations
import os
from typing import TypedDict
from urllib.parse import urlparse
import httpx


class SearchResult(TypedDict):
    position: int
    title: str
    url: str
    snippet: str
    domain: str


class ProviderError(RuntimeError):
    """A provider configuration, transport, quota, or response error."""


class DemoProvider:
    name = "Demo Search Dataset"
    demo = True

    def search(self, keyword: str, market: str, language: str, depth: int) -> list[SearchResult]:
        rows = [
            ("Search intent and buyer guide", "https://example.com/guides/overview", "Sample informational guide addressing the query."),
            ("Industry comparison and alternatives", "https://example.org/research/compare", "Sample comparison of criteria, trade-offs, and examples."),
            ("Practical implementation handbook", "https://example.net/resources/handbook", "Sample resource covering implementation steps."),
            ("Frequently asked questions", "https://docs.example.com/faq", "Sample FAQ addressing common audience questions."),
            ("Service provider directory", "https://directory.example.org/providers", "Sample category page organizing providers."),
            ("Independent industry analysis", "https://insights.example.net/analysis", "Sample industry analysis with supporting evidence."),
            ("Cost and pricing explainer", "https://example.com/pricing-explained", "Sample page explaining pricing models and considerations."),
            ("Beginner's guide", "https://learn.example.org/beginners", "Sample educational resource for new readers."),
            ("Case study collection", "https://stories.example.net/case-studies", "Sample collection of use cases and outcomes."),
            ("Tools and checklist", "https://tools.example.com/checklist", "Sample list of tools and a practical checklist."),
            ("Editorial perspective", "https://journal.example.org/perspective", "Sample editorial article exploring the topic."),
            ("Technical documentation", "https://developer.example.net/docs", "Sample technical documentation landing page."),
            ("Market overview", "https://research.example.com/market", "Sample market overview with segments and definitions."),
            ("Expert answers", "https://answers.example.org/expert-guide", "Sample Q&A resource built around common questions."),
            ("Product category overview", "https://catalog.example.net/category", "Sample category overview and selection guide."),
            ("Step-by-step tutorial", "https://academy.example.com/tutorial", "Sample tutorial intended to solve a task."),
            ("Industry trends report", "https://reports.example.org/trends", "Sample report outlining trends and implications."),
            ("Vendor evaluation framework", "https://buyers.example.net/framework", "Sample framework for comparing vendors."),
            ("Glossary and definitions", "https://reference.example.com/glossary", "Sample glossary explaining key concepts."),
            ("Strategy playbook", "https://strategy.example.org/playbook", "Sample playbook with strategic recommendations."),
        ]
        output = []
        for pos, (title, url, snippet) in enumerate(rows[:depth], 1):
            output.append({"position": pos, "title": title, "url": url, "snippet": "[DEMO ONLY for " + keyword + "] " + snippet, "domain": urlparse(url).hostname or ""})
        return output


class BraveProvider:
    name = "Brave Search API"
    demo = False

    def __init__(self, api_key: str, timeout: int):
        if not api_key:
            raise ProviderError("BRAVE_API_KEY is required when RANKFORGE_PROVIDER=brave")
        self.api_key, self.timeout = api_key, timeout

    def search(self, keyword: str, market: str, language: str, depth: int) -> list[SearchResult]:
        country_map = {"united states": "US", "united kingdom": "GB", "pakistan": "PK", "canada": "CA", "australia": "AU", "india": "IN"}
        try:
            response = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                params={"q": keyword, "country": country_map.get(market.lower(), market[:2].upper()), "search_lang": language[:2].lower(), "count": min(depth, 20)},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("Brave Search request failed: " + str(exc)) from exc
        output = []
        for item in data.get("web", {}).get("results", [])[:depth]:
            url = str(item.get("url", ""))
            if urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
                continue
            output.append({"position": len(output) + 1, "title": str(item.get("title", ""))[:500], "url": url, "snippet": str(item.get("description", ""))[:2000], "domain": (urlparse(url).hostname or "").lower()})
        return output


class DataForSEOProvider:
    name = "DataForSEO Google SERP"
    demo = False

    def __init__(self, login: str, password: str, timeout: int):
        if not login or not password:
            raise ProviderError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are required")
        self.login, self.password, self.timeout = login, password, timeout

    def search(self, keyword: str, market: str, language: str, depth: int) -> list[SearchResult]:
        locations = {"united states": 2840, "united kingdom": 2826, "pakistan": 2436, "canada": 2124, "australia": 2036, "india": 2356}
        payload = [{"keyword": keyword, "location_code": locations.get(market.lower(), 2840), "language_code": language[:2].lower(), "depth": min(depth, 20)}]
        try:
            response = httpx.post("https://api.dataforseo.com/v3/serp/google/organic/live/advanced", auth=(self.login, self.password), json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("DataForSEO request failed: " + str(exc)) from exc
        tasks = body.get("tasks") or []
        if not tasks or tasks[0].get("status_code", 20000) not in (20000, 20100):
            message = tasks[0].get("status_message", "Provider returned no task") if tasks else "Provider returned no task"
            raise ProviderError("DataForSEO error: " + message)
        groups = tasks[0].get("result") or []
        items = groups[0].get("items", []) if groups else []
        output = []
        for item in items:
            if item.get("type") != "organic":
                continue
            url = str(item.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                continue
            output.append({"position": int(item.get("rank_group") or item.get("rank_absolute") or len(output) + 1), "title": str(item.get("title", ""))[:500], "url": url, "snippet": str(item.get("description", ""))[:2000], "domain": str(item.get("domain") or parsed.hostname).lower()})
            if len(output) >= depth:
                break
        return output


def get_provider():
    """Choose one real provider when configured; otherwise return marked demo data."""
    selected = os.getenv("RANKFORGE_PROVIDER", "auto").strip().lower()
    timeout = max(2, min(int(os.getenv("RANKFORGE_REQUEST_TIMEOUT", "15")), 60))
    brave_key = os.getenv("BRAVE_API_KEY", "").strip()
    dfs_login = os.getenv("DATAFORSEO_LOGIN", "").strip()
    dfs_password = os.getenv("DATAFORSEO_PASSWORD", "").strip()
    if selected == "demo":
        return DemoProvider()
    if selected == "brave":
        return BraveProvider(brave_key, timeout)
    if selected == "dataforseo":
        return DataForSEOProvider(dfs_login, dfs_password, timeout)
    if selected != "auto":
        raise ProviderError("RANKFORGE_PROVIDER must be auto, demo, brave, or dataforseo")
    if dfs_login and dfs_password:
        return DataForSEOProvider(dfs_login, dfs_password, timeout)
    if brave_key:
        return BraveProvider(brave_key, timeout)
    return DemoProvider()
