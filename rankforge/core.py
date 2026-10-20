"""Core SEO analysis, defensive single-page auditing, SQLite evidence and comparisons."""
from __future__ import annotations
import ipaddress
import json
import os
import socket
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

DB_PATH = os.getenv("RANKFORGE_DB_PATH", "./data/rankforge.sqlite3")
MAX_HTML_BYTES = 3_000_000
TIMEOUT = max(2, min(int(os.getenv("RANKFORGE_REQUEST_TIMEOUT", "15")), 60))
USER_AGENT = os.getenv("RANKFORGE_USER_AGENT", "VerityRankForge/0.1 (+https://github.com/muhammadsaeed-ai/verity-rankforge)")


def connect():
    Path(DB_PATH).expanduser().parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(Path(DB_PATH).expanduser()), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, keyword TEXT NOT NULL,
          website TEXT, market TEXT NOT NULL, language TEXT NOT NULL, provider TEXT NOT NULL,
          demo INTEGER NOT NULL, results_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
          audit_json TEXT, opportunities_json TEXT NOT NULL, content_brief_json TEXT NOT NULL DEFAULT '{}',
          audit_error TEXT, status TEXT NOT NULL DEFAULT 'complete'
        );
        CREATE INDEX IF NOT EXISTS idx_runs_keyword_created ON runs(keyword, created_at DESC);
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
        if "content_brief_json" not in columns:
            db.execute("ALTER TABLE runs ADD COLUMN content_brief_json TEXT NOT NULL DEFAULT '{}'")
        if "audit_error" not in columns:
            db.execute("ALTER TABLE runs ADD COLUMN audit_error TEXT")


def save_run(run: dict) -> dict:
    created = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        cur = db.execute(
            """INSERT INTO runs(created_at,keyword,website,market,language,provider,demo,results_json,metrics_json,
            audit_json,opportunities_json,content_brief_json,audit_error,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (created, run["keyword"], run.get("website"), run["market"], run["language"], run["provider"],
             int(run["demo_mode"]), json.dumps(run["results"]), json.dumps(run["metrics"]),
             json.dumps(run["audit"]) if run.get("audit") else None, json.dumps(run["opportunities"]),
             json.dumps(run["content_brief"]), run.get("audit_error"), "complete"),
        )
        row_id = cur.lastrowid
    run.update({"id": row_id, "created_at": created, "status": "complete"})
    return run


def _decode(row):
    return {
        "id": row["id"], "created_at": row["created_at"], "keyword": row["keyword"], "website": row["website"],
        "market": row["market"], "language": row["language"], "provider": row["provider"],
        "demo_mode": bool(row["demo"]), "results": json.loads(row["results_json"]),
        "metrics": json.loads(row["metrics_json"]), "audit": json.loads(row["audit_json"]) if row["audit_json"] else None,
        "opportunities": json.loads(row["opportunities_json"]),
        "content_brief": json.loads(row["content_brief_json"] or "{}"), "audit_error": row["audit_error"],
        "status": row["status"],
    }


def get_run(run_id: int):
    with connect() as db:
        row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _decode(row) if row else None


def list_runs(limit: int = 20, keyword: str | None = None):
    limit = max(1, min(int(limit), 100))
    with connect() as db:
        if keyword:
            rows = db.execute("SELECT * FROM runs WHERE keyword=? COLLATE NOCASE ORDER BY id DESC LIMIT ?", (keyword, limit)).fetchall()
        else:
            rows = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_decode(row) for row in rows]


def compare_runs(first_id: int, second_id: int):
    first, second = get_run(first_id), get_run(second_id)
    if not first or not second:
        return None
    old = {(row.get("source") or first["provider"], row["url"]): row for row in first["results"]}
    new = {(row.get("source") or second["provider"], row["url"]): row for row in second["results"]}
    old_domains = {row.get("domain", "").lower().removeprefix("www.") for row in first["results"]}
    new_domains = {row.get("domain", "").lower().removeprefix("www.") for row in second["results"]}
    moved = []
    for key in old.keys() & new.keys():
        before, after = old[key].get("position"), new[key].get("position")
        if before != after:
            moved.append({"source": key[0], "url": key[1],
                          "title": new[key].get("title", old[key].get("title", "")),
                          "from_position": before, "to_position": after,
                          "delta": before - after if isinstance(before, int) and isinstance(after, int) else None})
    first_sources = sorted({row.get("source") or first["provider"] for row in first["results"]})
    second_sources = sorted({row.get("source") or second["provider"] for row in second["results"]})
    return {
        "first_run_id": first_id, "second_run_id": second_id,
        "keyword_match": first["keyword"].casefold() == second["keyword"].casefold(),
        "compatible_context": first["keyword"].casefold() == second["keyword"].casefold()
                              and first.get("website") == second.get("website")
                              and all(first[k] == second[k] for k in ("provider", "market", "language", "demo_mode"))
                              and first["metrics"].get("requested_depth") == second["metrics"].get("requested_depth")
                              and first_sources == second_sources,
        "sources_match": first_sources == second_sources,
        "first_sources": first_sources, "second_sources": second_sources,
        "added": [row for key, row in new.items() if key not in old],
        "removed": [row for key, row in old.items() if key not in new],
        "moved": sorted(moved, key=lambda item: (item["source"], item["from_position"] or 999)),
        "domain_gained": sorted(new_domains - old_domains), "domain_lost": sorted(old_domains - new_domains),
        "note": "Comparisons are strongest when keyword, provider set, market, language, depth and collection conditions match. Differences do not establish causation.",
    }

BUYING_TERMS = {"buy", "price", "pricing", "cost", "agency", "service", "services", "software", "tool", "tools", "best", "review", "comparison", "vs", "near me"}
INFO_TERMS = {"what", "why", "how", "guide", "tutorial", "examples", "meaning", "ideas", "learn", "checklist", "tips"}


def infer_intent(keyword: str) -> dict:
    phrase = keyword.casefold()
    commercial = sorted(term for term in BUYING_TERMS if term in phrase)
    informational = sorted(term for term in INFO_TERMS if term in phrase)
    if commercial and informational:
        label, why = "Mixed / commercial investigation", "The query contains both commercial and informational modifiers."
    elif commercial:
        label, why = "Commercial investigation", "Commercial modifiers suggest evaluation, selection, or purchase intent."
    elif informational:
        label, why = "Informational", "Informational modifiers suggest the searcher wants to learn or solve a problem."
    else:
        label, why = "Ambiguous / inspect SERP", "The phrase alone is insufficient to classify intent; validate against live result types."
    return {"label": label, "rationale": why, "matched_modifiers": sorted(set(commercial + informational)),
            "method": "Transparent keyword-modifier heuristic; not a trained intent model."}


def result_metrics(results: list[dict], depth: int) -> dict:
    counts = Counter((urlparse(item.get("url", "")).hostname or item.get("domain", "")).lower().removeprefix("www.") for item in results)
    positions = [int(item.get("position", idx + 1)) for idx, item in enumerate(results)]
    return {"result_count": len(results), "requested_depth": depth, "unique_domains": sum(bool(key) for key in counts),
            "domain_frequency": [{"domain": key, "appearances": value} for key, value in counts.most_common(10) if key],
            "mean_observed_position": round(sum(positions) / len(positions), 2) if positions else None,
            "complete": bool(results)}


def build_content_brief(keyword: str, results: list[dict], website: str | None, audit: dict | None) -> dict:
    """Build a transparent editorial starting brief, not auto-published content or an AI claim."""
    phrase = " ".join(keyword.split())
    title = phrase[:48].rstrip(" -,:;")
    if len(phrase) <= 43:
        title = phrase + " | Guide"
    title = title[:60]
    description = ("Explore " + phrase + ": learn the key criteria, practical approaches, trade-offs, and questions to consider before choosing a solution.")[:158]
    intent = infer_intent(phrase)
    outline = [
        "Define the problem and who this page is for",
        "How to evaluate " + phrase + ": the criteria that matter",
        "Options, capabilities, and meaningful trade-offs",
        "Practical implementation steps and examples",
        "Costs, risks, and common mistakes",
        "Frequently asked questions",
        "Recommended next step for the reader",
    ]
    if "Commercial" in intent["label"]:
        outline[2] = "Compare relevant options and explain how to choose"
        outline[4] = "Pricing factors, total cost, and evaluation checklist"
    elif intent["label"] == "Informational":
        outline[2] = "Core concepts, methods, and worked examples"
        outline[4] = "Troubleshooting, risks, and mistakes to avoid"
    competitor_signals = [row.get("title", "") for row in results[:5] if row.get("title")]
    return {
        "target_keyword": phrase, "intent_hypothesis": intent["label"], "suggested_title": title,
        "suggested_title_length": len(title), "suggested_meta_description": description,
        "suggested_h1": phrase, "outline": outline,
        "questions_to_research": [
            "What does the reader need to decide or complete after this search?",
            "Which facts, examples, or first-party evidence can the brand add that competing pages do not?",
            "What are the real limitations, costs, and alternatives?",
        ],
        "observed_result_title_signals": competitor_signals,
        "target_url": website, "audit_available": bool(audit),
        "publishing_guardrail": "Editorial draft only. Validate facts, intent, originality, product claims, brand voice, and audience usefulness before publishing. Do not stuff keywords or copy competitors.",
    }


def build_opportunities(keyword: str, results: list[dict], website: str | None, audit: dict | None) -> list[dict]:
    opportunities = []
    intent = infer_intent(keyword)
    opportunities.append({"priority": "P1", "category": "Search intent", "title": "Validate the page type against observed search intent",
                          "why": intent["rationale"], "action": "Compare the current page format with the actual results for this query; choose the format that best satisfies the audience task.",
                          "evidence": intent["method"], "effort": "Medium"})
    host = (urlparse(website if website and "://" in website else ("https://" + website if website else "")).hostname or "").lower().removeprefix("www.")
    domains = {(urlparse(item.get("url", "")).hostname or item.get("domain", "")).lower().removeprefix("www.") for item in results}
    if website and host and host not in domains:
        opportunities.append({"priority": "P1", "category": "Visibility", "title": "Target domain absent from this sampled result set",
                              "why": host + " did not appear among the " + str(len(results)) + " collected results.",
                              "action": "Check authorized Search Console data, confirm the correct page is indexable, and compare page intent with the observed result set.",
                              "evidence": "Absence from one sample does not prove the site is not indexed.", "effort": "Medium"})
    for issue in (audit or {}).get("issues", []):
        opportunities.append({"priority": issue["priority"], "category": "Technical SEO", "title": issue["title"], "why": issue["detail"],
                              "action": issue["recommendation"], "evidence": issue.get("evidence", "Single-page audit"), "effort": issue.get("effort", "Low")})
    opportunities.extend([
        {"priority": "P2", "category": "Content differentiation", "title": "Build a useful, evidence-backed content gap",
         "why": "A keyword mention alone is not a defensible reason to rank.",
         "action": "Add unique expertise, first-party examples, original data, relevant comparisons, and clear answers to audience questions.",
         "evidence": str(len(results)) + " sampled results; a full content-gap assessment needs deeper page extraction.", "effort": "High"},
        {"priority": "P2", "category": "Measurement", "title": "Establish a comparable ranking baseline",
         "why": "A single snapshot cannot establish a trend.",
         "action": "Repeat observations with consistent provider, market, language and depth; complement them with authorized Search Console clicks and impressions.",
         "evidence": "Measurement recommendation; repeated observations required.", "effort": "Low"},
    ])
    return opportunities


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = []
        self.in_title = False
        self.h1 = []
        self.h2 = []
        self.meta = {}
        self.canonical = ""
        self.images = 0
        self.images_missing_alt = 0
        self.links = []
        self.text = []
        self.heading = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta" and attrs.get("name", "").lower() in ("description", "robots"):
            self.meta[attrs["name"].lower()] = attrs.get("content", "")
        elif tag == "link" and "canonical" in attrs.get("rel", "").lower().split():
            self.canonical = attrs.get("href", "")
        elif tag == "h1":
            self.heading = "h1"
        elif tag == "h2":
            self.heading = "h2"
        elif tag == "img":
            self.images += 1
            if not attrs.get("alt", "").strip():
                self.images_missing_alt += 1
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in ("h1", "h2"):
            self.heading = None

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        if self.heading == "h1":
            self.h1.append(text)
        elif self.heading == "h2":
            self.h2.append(text)
        self.text.append(text)


def validate_public_url(url: str) -> str:
    raw = url.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid http(s) website URL")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private and local hosts cannot be audited")
    try:
        records = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("The hostname could not be resolved") from exc
    for record in records:
        addr = ipaddress.ip_address(record[4][0].split("%", 1)[0])
        if not addr.is_global:
            raise ValueError("Private, reserved, or non-public IP addresses cannot be audited")
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def audit_website(website: str) -> dict:
    current = website
    response_status = None
    final_url = None
    content_type = ""
    html_text = ""
    timeout = TIMEOUT
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}) as client:
        for hop in range(6):
            current = validate_public_url(current)
            with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Website returned a redirect without a Location header")
                    if hop == 5:
                        raise ValueError("Website exceeded the redirect limit")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                response_status = response.status_code
                final_url = str(response.url)
                content_type = response.headers.get("content-type", "")
                if "html" not in content_type.lower():
                    raise ValueError("URL did not return HTML (content-type: " + (content_type or "unknown") + ")")
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > MAX_HTML_BYTES:
                        raise ValueError("Page exceeded the 3 MB audit limit")
                    chunks.extend(chunk)
                encoding = response.encoding or "utf-8"
                html_text = bytes(chunks).decode(encoding, errors="replace")
                break
        else:
            raise ValueError("Website exceeded the redirect limit")
    parser = PageParser()
    parser.feed(html_text)
    parser.close()
    title = " ".join(parser.title_parts).strip()
    description = parser.meta.get("description", "").strip()
    word_count = len(" ".join(parser.text).split())
    issues = []

    def add(priority, title_, detail, recommendation, effort="Low"):
        issues.append({"priority": priority, "title": title_, "detail": detail, "recommendation": recommendation,
                       "effort": effort, "evidence": "Observed HTML from one page; not a full-site crawl."})

    if not title:
        add("P1", "Missing title element", "No non-empty HTML title was detected.", "Add a distinct title that accurately communicates the page purpose and satisfies search intent.")
    elif len(title) > 60:
        add("P2", "Long title candidate", "Title has " + str(len(title)) + " characters; this is a review signal, not a hard ranking limit.", "Preview the title and remove redundant wording only when it improves clarity.")
    if not description:
        add("P2", "Missing meta description", "No non-empty meta description was detected.", "Write a useful, accurate summary; search engines may generate a different snippet.")
    if not parser.h1:
        add("P1", "Missing H1 heading", "No H1 heading was detected.", "Add a clear primary heading describing the page's actual subject.")
    elif len(parser.h1) > 1:
        add("P2", "Multiple H1 headings to review", "Detected " + str(len(parser.h1)) + " H1 text fragments/headings.", "Inspect the structure for clarity; multiple H1s are not automatically a ranking penalty.")
    if not parser.h2:
        add("P2", "No H2 subheadings detected", "No H2 headings were detected in the retrieved HTML.", "Use descriptive sections where they genuinely improve scanning.")
    if parser.images_missing_alt:
        add("P2", "Images missing alternative text", str(parser.images_missing_alt) + " of " + str(parser.images) + " images lacked non-empty alt text.", "Describe meaningful images; use empty alt text for decorative images.")
    robots = parser.meta.get("robots", "")
    if "noindex" in robots.lower():
        add("P1", "Robots meta contains noindex", "The robots meta tag declares noindex.", "Confirm this is intentional before changing it; honor staging and privacy requirements.")
    if not parser.canonical:
        add("P2", "No canonical link detected", "No rel=canonical element was detected in the HTML.", "If URL variants exist, set a canonical URL consistent with the indexing strategy.")
    score = max(0, 100 - sum(18 if row["priority"] == "P1" else 8 for row in issues))
    host = urlparse(final_url or website).hostname
    internal_links = sum(1 for href in parser.links if href.startswith("/") or urlparse(href).hostname == host)
    return {
        "url": website, "final_url": final_url, "status_code": response_status, "content_type": content_type,
        "title": title, "title_length": len(title), "meta_description": description, "meta_description_length": len(description),
        "h1": parser.h1, "h2_count": len(parser.h2), "word_count": word_count, "image_count": parser.images,
        "images_missing_alt": parser.images_missing_alt, "canonical": parser.canonical, "robots": robots,
        "internal_link_candidates": internal_links, "issues": issues, "score": score,
        "score_note": "Heuristic checklist score, not a Google score or ranking prediction.",
        "scope": "Single-page HTML audit; JavaScript-rendered content and full-site architecture may not be captured.",
    }


def run_analysis(keyword: str, website: str | None, market: str, language: str, depth: int, provider) -> dict:
    keyword = " ".join(keyword.split())
    if len(keyword) < 2:
        raise ValueError("Keyword must contain at least two characters")
    if website:
        # Validate before calling a potentially billable provider.
        website = validate_public_url(website)
    results = provider.search(keyword, market, language, depth)
    unique, seen = [], set()
    for item in results:
        url = str(item.get("url", "")).strip()
        parsed = urlparse(url)
        source = str(item.get("source") or provider.name)
        dedupe_key = (source.casefold(), url.casefold())
        if parsed.scheme not in ("http", "https") or not parsed.hostname or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        position = item.get("position")
        if not isinstance(position, int) or position < 1:
            position = sum(1 for row in unique if row.get("source") == source) + 1
        unique.append({"position": position, "title": str(item.get("title", ""))[:500], "url": url,
                       "snippet": str(item.get("snippet", ""))[:2000],
                       "domain": str(item.get("domain") or parsed.hostname).lower(),
                       "source": source, "source_demo": bool(item.get("source_demo", provider.demo))})
    if not unique:
        raise RuntimeError("The provider returned no usable search results. Check credentials, quota, and market settings.")
    audit, audit_error = None, None
    if website:
        try:
            audit = audit_website(website)
        except (ValueError, httpx.HTTPError, OSError) as exc:
            audit_error = str(exc)
    metrics = result_metrics(unique, depth)
    metrics["intent"] = infer_intent(keyword)
    source_names = sorted({row["source"] for row in unique})
    metrics["sources"] = source_names
    metrics["source_metrics"] = []
    source_url_sets = {}
    for source in source_names:
        source_rows = [row for row in unique if row["source"] == source]
        source_metric = result_metrics(source_rows, depth)
        source_metric["source"] = source
        metrics["source_metrics"].append(source_metric)
        source_url_sets[source] = {row["url"].casefold() for row in source_rows}
    metrics["cross_source_agreement"] = []
    for index, source_a in enumerate(source_names):
        for source_b in source_names[index + 1:]:
            a_urls, b_urls = source_url_sets[source_a], source_url_sets[source_b]
            union = a_urls | b_urls
            metrics["cross_source_agreement"].append({
                "source_a": source_a, "source_b": source_b,
                "jaccard": round(len(a_urls & b_urls) / len(union), 4) if union else 1.0,
                "shared_urls": len(a_urls & b_urls), "union_urls": len(union),
            })
    if len(source_names) > 1:
        # A blended mean rank across different indexes is not statistically meaningful.
        metrics["mean_observed_position"] = None
    if getattr(provider, "last_errors", None):
        metrics["provider_warnings"] = provider.last_errors
    from urllib.parse import urlparse as parse_url
    host = (parse_url(website).hostname or "").lower() if website else ""
    present = any((parse_url(row["url"]).hostname or "").lower().removeprefix("www.") == host.removeprefix("www.") for row in unique) if host else None
    metrics["target_domain_found_in_sample"] = present
    brief = build_content_brief(keyword, unique, website, audit)
    opportunities = build_opportunities(keyword, unique, website, audit)
    from .strategy import build_strategy_report
    from .analyst import enrich_strategy_report
    strategy = build_strategy_report(keyword, website, market, language, provider.name, bool(provider.demo),
                                     unique, audit, opportunities)
    strategy = enrich_strategy_report(strategy, keyword, unique, audit, website)
    brief["strategy"] = strategy
    return save_run({"keyword": keyword, "website": website, "market": market, "language": language,
                     "provider": provider.name, "demo_mode": bool(provider.demo), "results": unique,
                     "metrics": metrics, "audit": audit, "audit_error": audit_error,
                     "opportunities": opportunities, "content_brief": brief})


def build_content_brief(keyword: str, results: list[dict], website: str | None, audit: dict | None) -> dict:
    phrase = " ".join(keyword.split())
    title = (phrase + " | Guide")[:60] if len(phrase) <= 43 else phrase[:60].rstrip(" -,:;")
    description = ("Explore " + phrase + ": learn the key criteria, practical approaches, trade-offs, and questions to consider before choosing a solution.")[:158]
    intent = infer_intent(phrase)
    outline = ["Define the problem and audience", "How to evaluate " + phrase + ": criteria that matter",
               "Options, capabilities, and meaningful trade-offs", "Practical implementation steps and examples",
               "Costs, risks, and common mistakes", "Frequently asked questions", "Recommended next step for the reader"]
    if "Commercial" in intent["label"]:
        outline[2] = "Compare relevant options and explain how to choose"
        outline[4] = "Pricing factors, total cost, and an evaluation checklist"
    elif intent["label"] == "Informational":
        outline[2] = "Core concepts, methods, and worked examples"
        outline[4] = "Troubleshooting, risks, and mistakes to avoid"
    return {"target_keyword": phrase, "intent_hypothesis": intent["label"], "suggested_title": title,
            "suggested_title_length": len(title), "suggested_meta_description": description, "suggested_h1": phrase,
            "outline": outline, "observed_result_title_signals": [row.get("title", "") for row in results[:5]],
            "target_url": website, "audit_available": bool(audit),
            "publishing_guardrail": "Editorial draft only. Validate intent, facts, originality, claims, brand voice and audience usefulness before publishing. Do not stuff keywords or copy competitors."}


def build_opportunities(keyword: str, results: list[dict], website: str | None, audit: dict | None) -> list[dict]:
    items = []
    intent = infer_intent(keyword)
    items.append({"priority": "P1", "category": "Search intent", "title": "Validate page type against observed search intent",
                  "why": intent["rationale"], "action": "Compare the current page format with actual results and select the format that best solves the audience task.",
                  "evidence": intent["method"], "effort": "Medium"})
    host = (urlparse(website).hostname or "").lower().removeprefix("www.") if website else ""
    domains = {(urlparse(row["url"]).hostname or row.get("domain", "")).lower().removeprefix("www.") for row in results}
    if host and host not in domains:
        items.append({"priority": "P1", "category": "Visibility", "title": "Target domain absent from this sampled result set",
                      "why": host + " did not appear among " + str(len(results)) + " collected results.",
                      "action": "Check authorized Search Console data, confirm the intended page is indexable, and compare its intent with observed pages.",
                      "evidence": "Absence from this sample does not prove the domain is not indexed.", "effort": "Medium"})
    for issue in (audit or {}).get("issues", []):
        items.append({"priority": issue["priority"], "category": "Technical SEO", "title": issue["title"], "why": issue["detail"],
                      "action": issue["recommendation"], "evidence": issue.get("evidence", "Single-page audit"), "effort": issue.get("effort", "Low")})
    items.extend([
        {"priority": "P2", "category": "Content differentiation", "title": "Create a useful, evidence-backed content gap",
         "why": "A keyword mention alone is not a defensible reason to rank.",
         "action": "Add brand expertise, original examples, verifiable data, useful comparisons and direct answers to audience questions.",
         "evidence": str(len(results)) + " sampled results; deeper page extraction is needed for a full content-gap assessment.", "effort": "High"},
        {"priority": "P2", "category": "Measurement", "title": "Establish a comparable baseline",
         "why": "One observation cannot establish a trend.",
         "action": "Repeat using consistent provider, market, language and depth, and complement it with authorized Search Console clicks and impressions.",
         "evidence": "Measurement recommendation; repeated observations required.", "effort": "Low"},
    ])
    return items
