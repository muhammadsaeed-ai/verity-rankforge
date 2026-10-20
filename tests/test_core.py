from rankforge import core
from rankforge.providers import DemoProvider


def test_intent_heuristic_is_explicit():
    assert core.infer_intent("best AI automation agency pricing")["label"] == "Commercial investigation"
    result = core.infer_intent("AI automation")
    assert result["label"] == "Ambiguous / inspect SERP"
    assert "heuristic" in result["method"]


def test_metrics_and_domain_frequency():
    rows = [
        {"url": "https://a.example/path", "domain": "a.example", "position": 1},
        {"url": "https://a.example/other", "domain": "a.example", "position": 2},
        {"url": "https://b.example/", "domain": "b.example", "position": 4},
    ]
    metrics = core.result_metrics(rows, 10)
    assert metrics["result_count"] == 3
    assert metrics["unique_domains"] == 2
    assert metrics["mean_observed_position"] == 2.33


def test_demo_provider_is_deterministic_and_labeled():
    provider = DemoProvider()
    first = provider.search("demo keyword", "United States", "en", 5)
    second = provider.search("demo keyword", "United States", "en", 5)
    assert provider.demo is True
    assert first == second
    assert len(first) == 5
    assert all("DEMO ONLY" in row["snippet"] for row in first)
    assert all(".example" in row["url"] for row in first)


def test_content_brief_is_reviewable_not_auto_published():
    brief = core.build_content_brief("best AI automation agency", [], "https://brand.example", None)
    assert brief["suggested_title_length"] <= 60
    assert brief["suggested_h1"]
    assert len(brief["outline"]) >= 5
    assert "Editorial draft only" in brief["publishing_guardrail"]


def test_page_parser_captures_html_signals():
    parser = core.PageParser()
    parser.feed('<html><head><title>Test page</title><meta name="description" content="Useful summary"><link rel="canonical" href="https://example.com/"></head><body><h1>Main heading</h1><h2>Section</h2><img src="x.jpg"><img src="y.jpg" alt="Chart"></body></html>')
    assert "Test page" in " ".join(parser.title_parts)
    assert parser.meta["description"] == "Useful summary"
    assert parser.h1 == ["Main heading"]
    assert len(parser.h2) == 1
    assert parser.images == 2 and parser.images_missing_alt == 1
    assert parser.canonical == "https://example.com/"


def test_storage_comparison_tracks_added_removed_and_moved(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.sqlite3"))
    core.init_db()
    common = {"keyword": "test keyword", "website": None, "market": "United States", "language": "en",
              "provider": "Fake", "demo_mode": False, "metrics": {"requested_depth": 10, "result_count": 2},
              "audit": None, "audit_error": None, "opportunities": [], "content_brief": {}}
    first = core.save_run({**common, "results": [
        {"url": "https://a.example/", "position": 1, "domain": "a.example"},
        {"url": "https://b.example/", "position": 2, "domain": "b.example"}]})
    second = core.save_run({**common, "results": [
        {"url": "https://a.example/", "position": 2, "domain": "a.example"},
        {"url": "https://c.example/", "position": 1, "domain": "c.example"}]})
    diff = core.compare_runs(first["id"], second["id"])
    assert [row["url"] for row in diff["added"]] == ["https://c.example/"]
    assert [row["url"] for row in diff["removed"]] == ["https://b.example/"]
    assert diff["moved"][0]["delta"] == -1
    assert diff["compatible_context"] is True


def test_compare_warns_when_context_differs(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "test.sqlite3"))
    core.init_db()
    base = {"keyword": "query", "website": None, "market": "United States", "language": "en",
            "provider": "Provider A", "demo_mode": False, "results": [],
            "metrics": {"requested_depth": 10}, "audit": None, "audit_error": None, "opportunities": [], "content_brief": {}}
    a = core.save_run(base)
    b = core.save_run({**base, "provider": "Provider B"})
    assert core.compare_runs(a["id"], b["id"])["compatible_context"] is False
