from fastapi.testclient import TestClient

from rankforge import core, crawler, monitoring, main
from rankforge.providers import MultiProvider, ProviderError
from rankforge.strategy import build_strategy_report


class FixedProvider:
    demo = False

    def __init__(self, name, rows=None, error=None):
        self.name = name
        self.rows = rows or []
        self.error = error

    def search(self, keyword, market, language, depth):
        if self.error:
            raise ProviderError(self.error)
        return self.rows[:depth]


def test_multi_provider_keeps_provenance_and_reports_partial_failures():
    good = FixedProvider("Provider A", [
        {"position": 2, "title": "One", "url": "https://a.example/", "snippet": "A", "domain": "a.example"}
    ])
    failed = FixedProvider("Provider B", error="quota reached")
    provider = MultiProvider([good, failed])
    rows = provider.search("sample keyword", "United States", "en", 10)
    assert len(rows) == 1
    assert rows[0]["source"] == "Provider A"
    assert rows[0]["source_demo"] is False
    assert provider.last_errors == [{"source": "Provider B", "error": "quota reached"}]


def test_multi_source_run_preserves_rank_per_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("RANKFORGE_AI_ANALYSIS", "false")
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "runs.sqlite3"))
    core.init_db()

    class CrossSourceProvider:
        name = "Multi-source: A + B"
        demo = False
        last_errors = []

        def search(self, keyword, market, language, depth):
            return [
                {"position": 1, "source": "A", "title": "Shared", "url": "https://shared.example/", "snippet": "A", "domain": "shared.example"},
                {"position": 2, "source": "A", "title": "A result", "url": "https://a.example/", "snippet": "A", "domain": "a.example"},
                {"position": 3, "source": "B", "title": "Shared", "url": "https://shared.example/", "snippet": "B", "domain": "shared.example"},
                {"position": 4, "source": "B", "title": "B result", "url": "https://b.example/", "snippet": "B", "domain": "b.example"},
            ]

    run = core.run_analysis("example keyword", None, "United States", "en", 10, CrossSourceProvider())
    assert len(run["results"]) == 4
    shared = [r for r in run["results"] if r["url"] == "https://shared.example/"]
    assert [(r["source"], r["position"]) for r in shared] == [("A", 1), ("B", 3)]
    assert run["metrics"]["mean_observed_position"] is None
    assert run["metrics"]["source_metrics"][0]["mean_observed_position"] == 1.5
    assert run["metrics"]["cross_source_agreement"][0]["jaccard"] == 0.3333
    saved = core.get_run(run["id"])
    assert "strategy" in saved["content_brief"]
    assert saved["content_brief"]["strategy"]["target"]["found_in_observation"] is False


def test_strategy_report_never_claims_keyword_volume_or_rank_guarantees():
    report = build_strategy_report(
        "brand strategy", "https://brand.example/", "United States", "en", "Demo",
        True,
        [
            {"source": "Demo", "position": 1, "url": "https://competitor.example/a", "title": "Brand strategy guide", "snippet": "Guide"},
            {"source": "Demo", "position": 2, "url": "https://brand.example/", "title": "Brand strategy", "snippet": "Brand page"},
        ],
        {"score": 92, "issues": [], "scope": "Single page"},
        [{"priority": "P1", "category": "Technical", "title": "Review", "action": "Inspect", "evidence": "Test", "effort": "Low"}],
    )
    assert report["target"]["found_in_observation"] is True
    assert report["evidence_profile"]["mode"] == "DEMO"
    assert report["keyword_idea_warning"].startswith("These are query ideas")
    assert "No ranking position is guaranteed" in report["limits"][0]
    assert report["prioritized_actions"][0]["requires_human_approval"] is True


def test_sitemap_parser_filters_cross_host_locations():
    sitemap = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://brand.example/</loc></url>
      <url><loc>https://brand.example/services</loc></url>
      <url><loc>https://external.example/should-not-be-crawled</loc></url>
    </urlset>"""
    parsed = crawler.parse_sitemap(sitemap, "brand.example")
    assert parsed["type"] == "urlset"
    assert parsed["urls"] == ["https://brand.example/", "https://brand.example/services"]


def test_robots_parser_collects_sitemaps_and_disallow_rules():
    robots = """User-agent: *
Disallow: /private
Disallow: /checkout

Sitemap: https://brand.example/sitemap.xml
"""
    sitemaps, disallow = crawler.parse_robots(robots)
    assert sitemaps == ["https://brand.example/sitemap.xml"]
    assert disallow == ["/checkout", "/private"]


def test_monitoring_missions_are_durable_and_leased(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "monitor.sqlite3"))
    core.init_db()
    monitoring.init_db()
    mission = monitoring.create_mission("AI automation agency", None, "United States", "en", 10, 1440)
    assert mission["enabled"] is True
    assert mission["interval_minutes"] == 1440
    claimed = monitoring.claim_due(limit=3)
    assert [item["id"] for item in claimed] == [mission["id"]]
    # An unexpired lease prevents a second worker from running the same mission.
    assert monitoring.claim_due(limit=3) == []
    loaded = monitoring.get_mission(mission["id"])
    assert loaded["lease_until"] is not None
    paused = monitoring.set_enabled(mission["id"], False)
    assert paused["enabled"] is False
    assert monitoring.list_missions()[0]["id"] == mission["id"]


def test_monitoring_api_and_console_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "api-monitor.sqlite3"))
    monkeypatch.setenv("RANKFORGE_PROVIDER", "demo")
    monkeypatch.setenv("RANKFORGE_AI_ANALYSIS", "false")
    with TestClient(main.app) as client:
        response = client.post("/api/missions", json={
            "keyword": "AI automation agency",
            "market": "United States",
            "language": "en",
            "depth": 10,
            "interval_minutes": 1440,
        })
        assert response.status_code == 200, response.text
        mission = response.json()
        assert mission["id"] > 0
        assert client.get("/api/missions").json()["missions"][0]["id"] == mission["id"]
        page = client.get("/monitoring")
        assert page.status_code == 200
        assert "MISSION BUILDER" in page.text
        execution = client.post(f"/api/missions/{mission['id']}/run", json={})
        assert execution.status_code == 200, execution.text
        assert execution.json()["run"]["demo_mode"] is True
        run_id = execution.json()["run"]["id"]
        strategy = client.get(f"/api/runs/{run_id}/strategy")
        assert strategy.status_code == 200
        assert strategy.json()["engine"] == "Verity RankForge Strategy Engine"
        paused = client.patch(f"/api/missions/{mission['id']}", json={"enabled": False})
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False
        too_frequent = client.post("/api/missions", json={
            "keyword": "sample keyword", "interval_minutes": 15
        })
        assert too_frequent.status_code == 422


def test_crawl_site_aggregates_duplicate_metadata_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "validate_public_url", lambda url: url if "://" in url else "https://" + url)
    sitemap = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://brand.example/</loc></url>
      <url><loc>https://brand.example/services</loc></url>
    </urlset>"""

    def fake_fetch(url, hostname, max_bytes=crawler.MAX_SITEMAP_BYTES):
        if url.endswith("/robots.txt"):
            return "Sitemap: https://brand.example/sitemap.xml"
        if url.endswith("/sitemap.xml"):
            return sitemap
        return None

    def fake_audit(url):
        return {
            "url": url, "final_url": url, "title": "Brand Services", "meta_description": "Same description",
            "score": 80, "issues": [{"title": "Missing canonical link", "priority": "P2"}],
        }

    monkeypatch.setattr(crawler, "_fetch_text", fake_fetch)
    monkeypatch.setattr(core, "audit_website", fake_audit)
    result = crawler.crawl_site("https://brand.example/", max_pages=10)
    assert result["pages_audited"] == 2
    assert result["page_failures"] == []
    assert len(result["duplicate_signals"]["titles"]) == 1
    assert len(result["duplicate_signals"]["meta_descriptions"]) == 1
    assert result["common_issues"] == [{"issue": "Missing canonical link", "page_count": 2}]
