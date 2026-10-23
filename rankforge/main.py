from __future__ import annotations
import base64
import binascii
import csv
import hmac
import io
import os
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, core, crawler, monitoring
from .providers import ProviderError, get_provider

app = FastAPI(title="Verity RankForge API", version=__version__,
              description="Evidence-led keyword research, single-page SEO checks, content briefs and saved result comparisons.")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# A browser login protects the dashboard and API without shipping a secret into
# frontend JavaScript. The shared API secret is supplied by the operator only.
_AUTH_USERNAME = "verityagent"
_PUBLIC_PATHS = {"/api/health"}
_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMITS = {"general": 60, "expensive": 5}
_EXPENSIVE_PATHS = {"/api/analyze", "/api/audit", "/api/crawl"}
app.state.rate_limit_buckets = {}
app.state.rate_limit_lock = threading.Lock()


def _decode_basic_auth(header: str | None) -> tuple[str, str] | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(header.split(None, 1)[1], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if ":" not in raw:
        return None
    return tuple(raw.split(":", 1))


@app.middleware("http")
async def secure_application(request: Request, call_next):
    """Fail closed without operator credentials; rate-limit authenticated API callers."""
    if request.url.path not in _PUBLIC_PATHS:
        expected = os.getenv("RANKFORGE_API_KEY", "").strip()
        if len(expected) < 32:
            return JSONResponse(
                status_code=503,
                content={"detail": "Protected routes are disabled. Configure RANKFORGE_API_KEY with at least 32 characters."},
            )

        credentials = _decode_basic_auth(request.headers.get("authorization"))
        if (
            credentials is None
            or not hmac.compare_digest(credentials[0].encode("utf-8"), _AUTH_USERNAME.encode("utf-8"))
            or not hmac.compare_digest(credentials[1].encode("utf-8"), expected.encode("utf-8"))
        ):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid credentials."},
                headers={"WWW-Authenticate": 'Basic realm="Verity RankForge", charset="UTF-8"'},
            )

        if request.url.path.startswith("/api/"):
            path = request.url.path
            expensive = (
                request.method == "POST"
                and (path in _EXPENSIVE_PATHS or (path.startswith("/api/missions/") and path.endswith("/run")))
            )
            bucket_kind = "expensive" if expensive else "general"
            address = request.client.host if request.client else "unknown"
            now = time.monotonic()
            key = (address, bucket_kind)
            with app.state.rate_limit_lock:
                buckets = app.state.rate_limit_buckets
                bucket = buckets.setdefault(key, deque())
                while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
                    bucket.popleft()
                limit = _RATE_LIMITS[bucket_kind]
                if len(bucket) >= limit:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Rate limit exceeded. Retry after the current one-minute window."},
                        headers={"Retry-After": str(max(1, int(_RATE_WINDOW_SECONDS - (now - bucket[0]))))},
                    )
                bucket.append(now)
                # Bound memory if a local reverse proxy or hostile authenticated
                # client causes many distinct source addresses to be observed.
                if len(buckets) > 4096:
                    stale_before = now - _RATE_WINDOW_SECONDS
                    for old_key, old_bucket in list(buckets.items()):
                        if not old_bucket or old_bucket[-1] < stale_before:
                            buckets.pop(old_key, None)
    return await call_next(request)


class AnalyzeRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=180)
    website: str | None = Field(default=None, max_length=2048)
    market: str = Field(default="United States", min_length=2, max_length=80)
    language: str = Field(default="en", min_length=2, max_length=12)
    depth: int = Field(default=10, ge=5, le=20)


class AuditRequest(BaseModel):
    website: str = Field(min_length=8, max_length=2048)


class CompareRequest(BaseModel):
    first_run_id: int = Field(gt=0)
    second_run_id: int = Field(gt=0)


class CrawlRequest(BaseModel):
    website: str = Field(min_length=8, max_length=2048)
    max_pages: int = Field(default=20, ge=1, le=30)


class MissionCreateRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=180)
    website: str | None = Field(default=None, max_length=2048)
    market: str = Field(default="United States", min_length=2, max_length=80)
    language: str = Field(default="en", min_length=2, max_length=12)
    depth: int = Field(default=10, ge=5, le=20)
    interval_minutes: int = Field(default=1440, ge=60, le=43200)


class MissionToggleRequest(BaseModel):
    enabled: bool


@app.on_event("startup")
def startup():
    core.init_db()
    monitoring.init_db()


@app.get("/api/health")
def health():
    try:
        provider = get_provider()
        return {"status": "ok", "service": "Verity RankForge", "version": __version__,
                "provider": provider.name, "demo_mode": bool(provider.demo),
                "honesty": "Synthetic results are marked as demo. No ranking guarantees are made."}
    except ProviderError as exc:
        return {"status": "degraded", "service": "Verity RankForge", "version": __version__,
                "provider": "misconfigured", "demo_mode": False, "error": str(exc)}


@app.get("/api/overview")
def overview():
    runs = core.list_runs(100)
    seen = {}
    for row in runs:
        key = row["keyword"].casefold()
        seen[key] = seen.get(key, 0) + 1
    latest = [{"id": r["id"], "created_at": r["created_at"], "keyword": r["keyword"],
               "website": r["website"], "provider": r["provider"], "demo_mode": r["demo_mode"], "status": r["status"]}
              for r in runs[:8]]
    return {"total_runs": len(runs), "demo_runs": sum(r["demo_mode"] for r in runs),
            "real_runs": sum(not r["demo_mode"] for r in runs), "latest_runs": latest,
            "top_keywords": [{"keyword": key, "runs": count} for key, count in list(seen.items())[:8]]}


@app.post("/api/analyze")
def analyze(request: AnalyzeRequest):
    try:
        provider = get_provider()
        return core.run_analysis(request.keyword, request.website, request.market, request.language, request.depth, provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        # A provider error should be actionable, but an internal stack trace is not sent to clients.
        raise HTTPException(status_code=502, detail="Research mission failed: " + str(exc)) from exc


@app.post("/api/audit")
def audit(request: AuditRequest):
    try:
        return core.audit_website(request.website)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Website audit failed: " + str(exc)) from exc


@app.post("/api/crawl")
def crawl(request: CrawlRequest):
    try:
        return crawler.crawl_site(request.website, request.max_pages)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Site crawl failed: " + str(exc)) from exc


@app.get("/api/missions")
def list_missions():
    return {"missions": monitoring.list_missions()}


@app.post("/api/missions")
def create_mission(request: MissionCreateRequest):
    try:
        return monitoring.create_mission(
            request.keyword, request.website, request.market, request.language,
            request.depth, request.interval_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/missions/{mission_id}")
def toggle_mission(mission_id: int, request: MissionToggleRequest):
    mission = monitoring.set_enabled(mission_id, request.enabled)
    if mission is None:
        raise HTTPException(status_code=404, detail="Monitoring mission not found")
    return mission


@app.post("/api/missions/{mission_id}/run")
def run_mission_now(mission_id: int):
    try:
        return monitoring.run_now(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Monitoring run failed: " + str(exc)) from exc


@app.get("/monitoring")
def monitoring_console():
    return FileResponse(WEB_DIR / "monitoring.html")


@app.get("/api/history")
def history(limit: int = Query(default=20, ge=1, le=100), keyword: str | None = None):
    runs = core.list_runs(limit, keyword)
    return {"runs": runs, "count": len(runs)}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int):
    row = core.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return row


@app.get("/api/runs/{run_id}/strategy")
def strategy_detail(run_id: int):
    row = core.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    strategy = (row.get("content_brief") or {}).get("strategy")
    if strategy is None:
        raise HTTPException(status_code=409, detail="This older run has no strategy report. Re-run the keyword analysis to generate one.")
    return strategy


@app.post("/api/compare")
def compare(request: CompareRequest):
    if request.first_run_id == request.second_run_id:
        raise HTTPException(status_code=422, detail="Choose two different runs")
    result = core.compare_runs(request.first_run_id, request.second_run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="One or both runs were not found")
    return result


@app.get("/api/intent")
def intent(keyword: str = Query(min_length=2, max_length=180)):
    return core.infer_intent(keyword)


@app.get("/api/export.csv")
def export_csv(limit: int = Query(default=100, ge=1, le=100)):
    rows = core.list_runs(limit)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["run_id", "created_at", "keyword", "website", "market", "language", "provider", "demo_mode",
                     "result_count", "unique_domains", "target_domain_found_in_sample", "audit_score", "opportunity_count"])
    for row in rows:
        writer.writerow([row["id"], row["created_at"], row["keyword"], row["website"] or "", row["market"], row["language"],
                         row["provider"], row["demo_mode"], row["metrics"].get("result_count", 0),
                         row["metrics"].get("unique_domains", 0), row["metrics"].get("target_domain_found_in_sample"),
                         row["audit"].get("score") if row["audit"] else "", len(row["opportunities"])])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv",
                              headers={"Content-Disposition": "attachment; filename=rankforge-history.csv"})


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")
