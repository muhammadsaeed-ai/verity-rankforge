import json

from rankforge import analyst


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_ai_analysis_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("RANKFORGE_AI_ANALYSIS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = analyst.enrich_strategy_report({}, "sample keyword", [], None, None)
    assert report["ai_analysis"]["status"] == "disabled"
    assert report["ai_analysis"]["enabled"] is False


def test_ai_analysis_requires_key_without_breaking_deterministic_report(monkeypatch):
    monkeypatch.setenv("RANKFORGE_AI_ANALYSIS", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = analyst.enrich_strategy_report({"evidence_profile": {}}, "sample keyword", [], None, None)
    assert report["ai_analysis"]["status"] == "needs_configuration"


def test_ai_analysis_validates_evidence_urls_and_structure(monkeypatch):
    monkeypatch.setenv("RANKFORGE_AI_ANALYSIS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    generated = {
        "summary": "The sampled results show a useful comparison opportunity.",
        "observations": [
            {"finding": "The source title emphasizes comparisons.", "confidence": "high",
             "evidence_urls": ["https://known.example/page", "https://invented.example/"]},
            {"finding": "No source attached.", "confidence": "high", "evidence_urls": []},
        ],
        "recommended_actions": [
            {"priority": "P1", "title": "Validate title alignment", "action": "Compare the current title against observed intent.",
             "rationale": "The sample includes comparison-oriented titles.", "evidence_urls": ["https://known.example/page"],
             "effort": "low", "measurement": "Review the change after publication."}
        ],
        "content_direction": {"differentiation": "Add first-party evidence.", "sections": ["Criteria"], "questions": ["What matters?"]},
        "risks": ["The sample is small."],
    }
    captured = {}

    def fake_post(url, headers, json, timeout, trust_env):
        captured["url"] = url
        captured["body"] = json
        return FakeResponse({"choices": [{"message": {"content": __import__("json").dumps(generated)}}]})

    monkeypatch.setattr(analyst.httpx, "post", fake_post)
    report = {
        "context": {"market": "US", "language": "en", "provider": "Provider A"},
        "evidence_profile": {"mode": "LIVE PROVIDER DATA"},
        "prioritized_actions": [],
    }
    results = [{"url": "https://known.example/page", "position": 1, "title": "Comparison",
                "snippet": "Evidence sample", "source": "Provider A"}]
    returned = analyst.enrich_strategy_report(report, "sample keyword", results, None, None)
    ai = returned["ai_analysis"]
    assert ai["status"] == "complete"
    assert ai["model"] == "test-model"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert ai["observations"][0]["evidence_urls"] == ["https://known.example/page"]
    assert ai["observations"][1]["evidence_status"].startswith("unlinked")
    assert ai["recommended_actions"][0]["requires_human_approval"] is True
