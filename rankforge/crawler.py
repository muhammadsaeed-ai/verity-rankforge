"""Bounded, same-host sitemap discovery and site-wide SEO sampling."""
from __future__ import annotations

import ipaddress
import socket
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlparse

import httpx

from . import core

MAX_SITEMAP_BYTES = 2_000_000
MAX_SITEMAPS = 6
TIMEOUT = 8


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").rstrip(".").lower()


def _same_host(url: str, hostname: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname) and _host(url) == hostname


def parse_sitemap(xml_text: str, hostname: str) -> dict:
    """Parse a sitemap URL set or sitemap index; retain only same-host HTTP(S) locations."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("Sitemap is not valid XML.") from exc
    kind = root.tag.rsplit("}", 1)[-1].lower()
    urls = []
    sitemaps = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].lower() != "loc" or not node.text:
            continue
        location = node.text.strip()
        if not _same_host(location, hostname):
            continue
        if kind == "sitemapindex":
            sitemaps.append(location)
        else:
            urls.append(location)
    return {"type": kind, "urls": urls, "sitemaps": sitemaps}


def parse_robots(text: str) -> tuple[list[str], list[str]]:
    """Return sitemap declarations and conservative disallow path prefixes."""
    sitemaps = []
    disallow = []
    selected_group = False
    group_has_rules = False
    group_agents: list[str] = []

    def commit_group():
        if "*" in group_agents or any("verityrankforge" in item for item in group_agents):
            disallow.extend(group_disallow)

    group_disallow: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "sitemap":
            if value:
                sitemaps.append(value)
        elif key == "user-agent":
            if group_agents and group_has_rules:
                commit_group()
                group_agents = []
                group_disallow = []
                group_has_rules = False
            group_agents.append(value.lower())
        elif key == "disallow" and group_agents:
            group_has_rules = True
            if value.startswith("/"):
                group_disallow.append(value)
    if group_agents:
        commit_group()
    return sitemaps, sorted(set(disallow))


def _fetch_text(url: str, hostname: str, max_bytes: int = MAX_SITEMAP_BYTES) -> str | None:
    current = core.validate_public_url(url)
    if not _same_host(current, hostname):
        raise ValueError("Cross-host sitemap and robots URLs are not followed.")
    headers = {"User-Agent": core.USER_AGENT, "Accept": "text/plain,application/xml,text/xml"}
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, trust_env=False, headers=headers) as client:
        for hop in range(4):
            current = core.validate_public_url(current)
            if not _same_host(current, hostname):
                raise ValueError("Cross-host redirects are not followed.")
            with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location or hop == 3:
                        raise ValueError("Sitemap or robots URL exceeded redirect limits.")
                    current = urljoin(current, location)
                    continue
                if response.status_code in (404, 410):
                    return None
                response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > max_bytes:
                        raise ValueError("Sitemap or robots response exceeded the size limit.")
                    chunks.extend(chunk)
                encoding = response.encoding or "utf-8"
                return bytes(chunks).decode(encoding, errors="replace")
    return None


def discover_urls(website: str, max_pages: int = 20) -> dict:
    """Discover page URLs through robots.txt and XML sitemaps, never crossing the supplied host."""
    root_url = core.validate_public_url(website)
    hostname = _host(root_url)
    parsed = urlparse(root_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots_text = _fetch_text(urljoin(origin + "/", "robots.txt"), hostname)
    declared, disallow = parse_robots(robots_text or "")
    sitemap_queue = declared[:MAX_SITEMAPS] if declared else [urljoin(origin + "/", "sitemap.xml")]
    found = []
    seen_urls = set()
    seen_sitemaps = set()
    sitemap_count = 0

    def allowed(url: str) -> bool:
        path = urlparse(url).path or "/"
        return not any(path.startswith(rule) for rule in disallow)

    while sitemap_queue and len(seen_urls) < max_pages and sitemap_count < MAX_SITEMAPS:
        sitemap_url = sitemap_queue.pop(0)
        if sitemap_url in seen_sitemaps or not _same_host(sitemap_url, hostname):
            continue
        seen_sitemaps.add(sitemap_url)
        sitemap_count += 1
        xml_text = _fetch_text(sitemap_url, hostname)
        if not xml_text:
            continue
        parsed_map = parse_sitemap(xml_text, hostname)
        if parsed_map["type"] == "sitemapindex":
            for child in parsed_map["sitemaps"]:
                if child not in seen_sitemaps and len(sitemap_queue) < MAX_SITEMAPS:
                    sitemap_queue.append(child)
            continue
        for url in parsed_map["urls"]:
            normalized = url.split("#", 1)[0]
            if allowed(normalized) and normalized not in seen_urls:
                seen_urls.add(normalized)
                found.append(normalized)
                if len(found) >= max_pages:
                    break
    root_clean = root_url.split("#", 1)[0]
    if allowed(root_clean) and root_clean not in seen_urls:
        found.insert(0, root_clean)
        seen_urls.add(root_clean)
    return {
        "website": root_url, "hostname": hostname, "urls": found[:max_pages],
        "sitemaps_checked": sitemap_count, "sitemaps_found": len(seen_sitemaps),
        "robots_available": bool(robots_text), "robots_disallow_rules": disallow,
        "discovery_limit": max_pages,
    }


def crawl_site(website: str, max_pages: int = 20) -> dict:
    max_pages = max(1, min(int(max_pages), 30))
    discovery = discover_urls(website, max_pages)
    pages = []
    failures = []
    for url in discovery["urls"]:
        try:
            pages.append(core.audit_website(url))
        except Exception as exc:
            failures.append({"url": url, "error": f"{type(exc).__name__}: {exc}"[:500]})

    title_pages = defaultdict(list)
    description_pages = defaultdict(list)
    issue_counts = Counter()
    for page in pages:
        title = " ".join(str(page.get("title", "")).casefold().split())
        description = " ".join(str(page.get("meta_description", "")).casefold().split())
        if title:
            title_pages[title].append(page.get("final_url") or page["url"])
        if description:
            description_pages[description].append(page.get("final_url") or page["url"])
        for issue in page.get("issues", []):
            issue_counts[issue.get("title", "Other issue")] += 1

    duplicates = {
        "titles": [{"value": value, "urls": urls} for value, urls in title_pages.items() if len(urls) > 1],
        "meta_descriptions": [{"value": value, "urls": urls} for value, urls in description_pages.items() if len(urls) > 1],
    }
    return {
        "website": discovery["website"], "discovery": discovery,
        "pages_audited": len(pages), "page_failures": failures, "pages": pages,
        "duplicate_signals": duplicates,
        "common_issues": [{"issue": title, "page_count": count} for title, count in issue_counts.most_common(15)],
        "limitations": [
            "This is a bounded sitemap-led sample, not a complete crawl unless the site fits within the configured limit.",
            "Disallowed robots paths are skipped conservatively; robots.txt interpretation is intentionally limited.",
            "JavaScript-rendered SEO signals may not be present in fetched HTML.",
        ],
    }
