"""Evidence-led ranking strategy synthesis. No rankings are promised or fabricated."""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "best", "by", "for", "from",
    "how", "in", "is", "it", "of", "on", "or", "the", "to", "what", "with",
    "your", "you", "our", "this", "that", "guide", "review", "reviews",
}


def _host(value: str | None) -> str:
    if not value:
        return ""
    parsed = urlparse(value if "://" in value else "https://" + value)
    return (parsed.hostname or "").lower().removeprefix("www.")


def _terms(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9+-]{2,}", text.casefold())
            if word not in STOP_WORDS and not word.isdigit()]


def build_strategy_report(
    keyword: str,
    website: str | None,
    market: str,
    language: str,
    provider: str,
    demo_mode: bool,
    results: list[dict],
    audit: dict | None,
    opportunities: list[dict],
) -> dict:
    """Create an actionable research plan from recorded observations, with explicit evidence limits."""
    target = _host(website)
    per_source: dict[str, list[dict]] = {}
    for row in results:
        per_source.setdefault(str(row.get("source") or provider), []).append(row)

    target_positions = []
    for source, rows in per_source.items():
        matches = [r for r in rows if _host(r.get("url")) == target] if target else []
        for row in matches:
            target_positions.append({
                "source": source,
                "url": row.get("url"),
                "observed_position": row.get("position"),
                "title": row.get("title", ""),
            })

    domains = Counter(_host(row.get("url")) for row in results if _host(row.get("url")))
    target_found = bool(target_positions)
    competitor_domains = [
        {"domain": domain, "sampled_appearances": count}
        for domain, count in domains.most_common(12)
        if domain and domain != target
    ]

    title_terms = Counter()
    for row in results:
        title_terms.update(_terms(str(row.get("title", ""))))
    evidence_terms = [
        {"term": term, "title_mentions": count}
        for term, count in title_terms.most_common(12)
    ]

    actions = []
    for item in opportunities:
        priority = str(item.get("priority", "P3"))
        score = {"P1": 90, "P2": 65, "P3": 40}.get(priority, 30)
        if item.get("effort") == "Low":
            score += 5
        actions.append({
            "priority_score": min(score, 100),
            "priority": priority,
            "category": item.get("category", "Research"),
            "title": item.get("title", "Review opportunity"),
            "action": item.get("action", "Investigate the evidence and decide on the next step."),
            "evidence": item.get("evidence", "No additional evidence supplied."),
            "effort": item.get("effort", "Medium"),
            "status": "proposed",
            "requires_human_approval": True,
        })
    actions.sort(key=lambda row: (-row["priority_score"], row["title"]))

    query = " ".join(keyword.split())
    keyword_ideas = [
        {"query": "how to choose " + query, "type": "decision support", "volume_status": "not measured"},
        {"query": query + " cost", "type": "commercial research", "volume_status": "not measured"},
        {"query": query + " vs alternatives", "type": "comparison", "volume_status": "not measured"},
        {"query": query + " examples", "type": "use cases", "volume_status": "not measured"},
        {"query": query + " implementation", "type": "practical guidance", "volume_status": "not measured"},
    ]

    audit_issues = (audit or {}).get("issues", [])
    technical_signal = {
        "heuristic_page_score": (audit or {}).get("score"),
        "p1_issue_count": sum(1 for issue in audit_issues if issue.get("priority") == "P1"),
        "p2_issue_count": sum(1 for issue in audit_issues if issue.get("priority") == "P2"),
        "scope": (audit or {}).get("scope", "No target page audit was completed."),
        "is_google_score": False,
    }

    phases = [
        {
            "window": "Days 1–3",
            "objective": "Protect crawlability and validate the target page",
            "actions": [
                "Confirm the intended URL returns successfully and is indexable.",
                "Resolve verified critical audit findings before cosmetic content changes.",
                "Check the canonical URL, internal links, redirects and sitemap coverage.",
            ],
            "success_signal": "Verified technical issues resolved; record before-and-after evidence.",
        },
        {
            "window": "Days 4–10",
            "objective": "Match search intent with a genuinely useful page",
            "actions": [
                "Compare observed result types and intent signals against the target page.",
                "Improve the title, primary heading and page structure where the evidence supports it.",
                "Add original examples, first-party expertise and answers that are useful beyond competitor summaries.",
            ],
            "success_signal": "Editorial review passes and the deployed page has a recorded version and URL.",
        },
        {
            "window": "Days 11–20",
            "objective": "Strengthen topic coverage and discovery",
            "actions": [
                "Review relevant internal-link opportunities and related pages.",
                "Create useful supporting content only where it serves a distinct audience task.",
                "Pursue earned links and legitimate distribution rather than link schemes.",
            ],
            "success_signal": "Approved improvements are live; crawl and internal-link checks are repeated.",
        },
        {
            "window": "Days 21–30",
            "objective": "Measure outcomes and choose the next experiment",
            "actions": [
                "Repeat comparable third-party search observations.",
                "Compare first-party impressions, clicks and query data where authorized Search Console access exists.",
                "Keep, revise or roll back changes based on evidence rather than a single ranking snapshot.",
            ],
            "success_signal": "A dated comparison report records what changed and what remains uncertain.",
        },
    ]

    return {
        "engine": "Verity RankForge Strategy Engine",
        "method": "Rule-based synthesis of collected results and single-page HTML signals; not an ML ranking predictor.",
        "target": {"url": website, "domain": target or None, "found_in_observation": target_found,
                   "observed_positions": target_positions},
        "context": {"keyword": query, "market": market, "language": language, "provider": provider},
        "evidence_profile": {
            "mode": "DEMO" if demo_mode else "LIVE PROVIDER DATA",
            "result_observations": len(results),
            "source_count": len(per_source),
            "sources": [{"source": source, "result_count": len(rows)} for source, rows in sorted(per_source.items())],
            "sampled_competitor_domains": competitor_domains,
            "title_term_signals": evidence_terms,
            "technical_page_signals": technical_signal,
        },
        "prioritized_actions": actions[:15],
        "keyword_research_ideas": keyword_ideas,
        "keyword_idea_warning": "These are query ideas, not validated keyword-volume, difficulty, or traffic estimates.",
        "30_day_improvement_cycle": phases,
        "measurement_plan": [
            "Maintain a consistent provider, market, language, device context and result depth for comparable snapshots.",
            "Use Search Console data for authorized first-party impressions, clicks and CTR when available.",
            "Annotate deployments and major content changes so later movements can be investigated.",
            "Do not interpret correlation, one sample, or heuristic scores as proof that a change caused a ranking gain.",
        ],
        "limits": [
            "No ranking position is guaranteed; search systems and competitors are outside this application's control.",
            "The audit inspects returned HTML and may miss JavaScript-rendered content or site-wide issues.",
            "Competitor title terms are descriptive signals from a bounded sample, not instructions to copy competing pages.",
            "Website changes are recommendations only; deployment requires an authorized human or separately approved integration.",
        ],
    }
