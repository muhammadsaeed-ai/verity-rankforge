<div align="center">
  <img src="docs/rankforge-cover.svg" alt="Verity RankForge search intelligence command center" width="100%" />
  <h1>Verity RankForge</h1>
  <p><strong>Evidence-led SEO research, on-page diagnostics, and ranking-opportunity intelligence.</strong></p>
  <p>
    <a href="https://github.com/muhammadsaeed-ai/verity-rankforge/actions/workflows/ci.yml"><img src="https://github.com/muhammadsaeed-ai/verity-rankforge/actions/workflows/ci.yml/badge.svg" alt="Quality Gate" /></a>
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+" />
    <img src="https://img.shields.io/badge/FastAPI-API-009688?logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/SEO-evidence--led-56E6F5" alt="Evidence-led SEO" />
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License" />
  </p>
  <p><em>Research the SERP. Diagnose the page. Prioritize the next move.</em></p>
</div>

---

## What it does

RankForge investigates a keyword, collects search results through a configured provider, audits an optional target page, and turns the available evidence into a prioritized SEO action queue. Every completed run is saved in SQLite so users can revisit observations and compare changes.

- **Keyword intelligence:** modifier-based intent hypothesis, result sample, observed positions, unique-domain distribution, and target-domain presence checks.
- **Real search integrations:** Brave Search API and DataForSEO Google organic SERP API.
- **Honest local mode:** deterministic synthetic results when no provider key is configured; sample results are explicitly marked **DEMO** and use example domains.
- **Single-page SEO audit:** title, meta description, headings, image alt attributes, canonical link, robots noindex, word count, HTTP response and internal-link candidates.
- **SEO content blueprint:** title, description and H1 candidates, a content outline, and observed result-title signals; drafts require editorial validation.
- **Prioritized opportunities:** priority, rationale, next action, estimated effort, evidence and limitations.
- **Historical comparisons:** detect added, removed and moved URLs and domain changes across saved runs.
- **Futuristic command center:** responsive dark interface, motion-aware visuals, result explorer, audit telemetry, opportunity lab, content blueprint, history comparison and CSV export.
- **Developer interfaces:** FastAPI endpoints, OpenAPI docs, CLI, Dockerfile, and CI regression tests.

> **No false promises:** RankForge does not guarantee the #1 position, silently replace failed live API calls with fake data, or automatically publish website changes. SEO performance depends on the site, users, competitors, search systems and many factors outside this application.

## Interface

The committed cover art is a product visual, not a screenshot of a deployed site. The working dashboard is served by the application at the root URL.

## Quick start

**Requirements:** Python 3.11 or newer.

~~~bash
git clone https://github.com/muhammadsaeed-ai/verity-rankforge.git
cd verity-rankforge
python -m venv .venv

# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
# .venv\Scripts\Activate.ps1

python -m pip install -e ".[dev]"
cp .env.example .env
# Windows: copy .env.example .env

uvicorn rankforge.main:app --reload
~~~

Open http://127.0.0.1:8000. API documentation is at http://127.0.0.1:8000/docs and service status is at http://127.0.0.1:8000/api/health.

Without credentials the application uses deterministic sample results. These are **not actual rankings**. Set a provider before using data for business decisions.

## Enable a live search provider

Credentials remain in environment variables; never commit your local .env file.

**Brave Search API**

~~~dotenv
RANKFORGE_PROVIDER=brave
BRAVE_API_KEY=replace-with-your-key
~~~

**DataForSEO Google organic SERP**

~~~dotenv
RANKFORGE_PROVIDER=dataforseo
DATAFORSEO_LOGIN=replace-with-your-login
DATAFORSEO_PASSWORD=replace-with-your-password
~~~

**Automatic selection**

Set RANKFORGE_PROVIDER to auto. The application selects DataForSEO when both credentials are supplied, otherwise Brave when its key is supplied, otherwise the clearly disclosed demo provider. A live provider failure returns an actionable error; it does not quietly substitute sample results. Provider pricing, quotas, supported locations and language options, and terms apply.

Other runtime settings are documented in .env.example. API keys must only be supplied by the server operator.

## Run a mission

From the dashboard, enter the keyword, optionally add a target website, and choose the market, language and result depth. The application:

1. validates the request and, if supplied, validates the target host before making a potentially billable search call;
2. fetches provider results and removes duplicate or invalid URLs;
3. calculates transparent sample metrics and a keyword-modifier intent hypothesis;
4. audits the supplied page when possible, recording a page-audit error separately if the SERP run succeeded but the page could not be audited;
5. generates an editorial content blueprint and evidence-labeled opportunity queue; and
6. persists the run to SQLite for later inspection and comparison.

The dashboard history supports selecting two runs for a URL-level comparison. Comparisons are strongest when keyword, provider, market, language, result depth and collection conditions match.

## REST API

~~~bash
# Service health and active provider mode
curl http://127.0.0.1:8000/api/health

# Analyze a keyword (demo mode if no provider is configured)
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"keyword":"AI automation agency","website":"https://example.com","market":"United States","language":"en","depth":10}'

# Audit one page
curl -X POST http://127.0.0.1:8000/api/audit \
  -H "Content-Type: application/json" \
  -d '{"website":"https://example.com"}'

# Recent saved runs
curl "http://127.0.0.1:8000/api/history?limit=20"

# Full detail for run 1
curl http://127.0.0.1:8000/api/runs/1

# Compare runs 1 and 2
curl -X POST http://127.0.0.1:8000/api/compare \
  -H "Content-Type: application/json" \
  -d '{"first_run_id":1,"second_run_id":2}'

# Download a CSV summary
curl -o rankforge-history.csv "http://127.0.0.1:8000/api/export.csv?limit=100"
~~~

### Endpoint map

| Method | Route | Purpose |
|---|---|---|
| GET | /api/health | Runtime readiness, active provider and demo disclosure |
| GET | /api/overview | Workspace counts and recent missions |
| POST | /api/analyze | Collect, analyze and persist a keyword mission |
| POST | /api/audit | Inspect a supplied page's HTML SEO signals |
| GET | /api/history | List saved runs; optional keyword filter |
| GET | /api/runs/{run_id} | Retrieve a saved run |
| POST | /api/compare | Compare two runs |
| GET | /api/intent?keyword=... | Return the initial intent hypothesis |
| GET | /api/export.csv | Export a bounded run-history summary |

## Architecture

~~~mermaid
flowchart LR
  UI["Futuristic dashboard"] --> API["FastAPI / CLI"]
  API --> SEL["Provider selector"]
  SEL --> BR["Brave Search API"]
  SEL --> DF["DataForSEO Google SERP"]
  SEL --> DEMO["Labeled demo dataset"]
  BR --> N["Normalize and deduplicate"]
  DF --> N
  DEMO --> N
  N --> DB[("SQLite evidence store")]
  API --> AUD["Single-page SEO audit"]
  DB --> CMP["Temporal run comparison"]
  CMP --> OP["Prioritized opportunities"]
  AUD --> OP
  OP --> UI
~~~

| Module | Responsibility |
|---|---|
| rankforge/providers.py | Brave, DataForSEO, demo adapter, and explicit provider selection |
| rankforge/core.py | Intent heuristic, result metrics, site audit, content brief, SQLite history, comparisons and opportunities |
| rankforge/main.py | REST API, CSV export, and dashboard serving |
| rankforge/cli.py | JSON-output command-line client |
| web/index.html | Responsive command center and API-backed interactions |
| tests/test_core.py | Regression tests using deterministic data and temporary SQLite databases |

## CLI usage

~~~bash
rankforge "AI automation agency" --website https://example.com --market "United States" --language en --depth 10
~~~

The CLI prints the saved analysis as JSON. Use it only for domains you are authorized to assess.

## Tests and quality gate

~~~bash
python -m compileall -q rankforge tests
python -m pytest
~~~

GitHub Actions runs compilation and regression tests on pushes and pull requests. The test suite covers deterministic provider behavior, explicit intent labeling, metrics, content-brief guardrails, HTML parsing, SQLite round trips and added/removed/moved URL comparisons. Tests do not require a live API key or search-provider call.

## Docker

~~~bash
docker build -t verity-rankforge .
docker run --rm -p 8000:8000 \
  -e RANKFORGE_DB_PATH=/app/data/rankforge.sqlite3 \
  -v rankforge-data:/app/data \
  verity-rankforge
~~~

Set the chosen provider credentials as environment variables at runtime to enable live data. The container runs as a non-root user and provides a health check.

## Scope and limitations

This release is a working research and diagnostics foundation, not a complete autonomous SEO platform.

- Intent classification uses transparent keyword modifiers; it is not a trained semantic classifier.
- The auditor inspects one HTML response. It does not yet crawl a full site, render JavaScript, evaluate Core Web Vitals, inspect Search Console via OAuth, or comprehensively validate XML sitemaps and robots.txt.
- Search observations are synchronous snapshots. Durable scheduling, queue workers, adaptive sampling, alert delivery and background monitoring are planned but not yet implemented.
- Content briefs are deterministic starting drafts. An LLM research layer, deeper content-gap extraction, CMS integrations and approval-based change proposals are future work.
- The application does not automatically edit or deploy a website, and it cannot promise rankings. The heuristic audit score is not a Google score.
- Authentication and tenant isolation are not included. Put an authentication layer and network controls in front of any internet-facing deployment; do not run the development console as a public multi-user service.

## Responsible use and security

- Follow provider terms, usage limits and billing.
- Do not rotate browsers or automate prohibited queries to evade limits or manufacture search traffic.
- The page auditor only accepts HTTP(S), rejects local/private/reserved IP targets, revalidates redirects and caps redirect depth and HTML size. Audit only websites you are authorized to inspect.
- Keep secrets in environment variables or a secret manager. See SECURITY.md before deployment.

## Contributing

Issues and pull requests are welcome. Include a reproduction case and a regression test for behavior changes. Never include credentials, customer data or private target URLs in fixtures.

## Maintained by VerityAgent

Built as an open-source product initiative by **VerityAgent**.

## License

MIT — see LICENSE.
