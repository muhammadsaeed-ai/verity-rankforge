from fastapi.testclient import TestClient
from rankforge import core
from rankforge import main


def test_api_dashboard_and_analysis_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "api.sqlite3"))
    monkeypatch.setenv("RANKFORGE_PROVIDER", "demo")
    monkeypatch.setenv("RANKFORGE_AI_ANALYSIS", "false")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)

    with TestClient(main.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["demo_mode"] is True

        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "RANKFORGE" in dashboard.text
        assert "Launch a research mission" in dashboard.text

        response = client.post("/api/analyze", json={
            "keyword": "AI automation agency",
            "market": "United States",
            "language": "en",
            "depth": 5,
        })
        assert response.status_code == 200, response.text
        run = response.json()
        assert run["demo_mode"] is True
        assert run["id"] > 0
        assert len(run["results"]) == 5
        assert all("DEMO ONLY" in result["snippet"] for result in run["results"])
        assert run["content_brief"]["target_keyword"] == "AI automation agency"

        detail = client.get(f"/api/runs/{run['id']}")
        assert detail.status_code == 200
        assert detail.json()["id"] == run["id"]

        history = client.get("/api/history?limit=10")
        assert history.status_code == 200
        assert history.json()["count"] == 1

        overview = client.get("/api/overview")
        assert overview.json()["total_runs"] == 1
        assert overview.json()["demo_runs"] == 1
        assert overview.json()["real_runs"] == 0

        exported = client.get("/api/export.csv")
        assert exported.status_code == 200
        assert "AI automation agency" in exported.text


def test_api_rejects_invalid_mission_and_missing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "badapi.sqlite3"))
    monkeypatch.setenv("RANKFORGE_PROVIDER", "demo")
    monkeypatch.setenv("RANKFORGE_AI_ANALYSIS", "false")
    with TestClient(main.app) as client:
        bad = client.post("/api/analyze", json={"keyword": "x", "depth": 10})
        assert bad.status_code == 422
        missing = client.get("/api/runs/9999")
        assert missing.status_code == 404
        same = client.post("/api/compare", json={"first_run_id": 1, "second_run_id": 1})
        assert same.status_code == 422
