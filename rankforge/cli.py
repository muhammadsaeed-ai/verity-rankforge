from __future__ import annotations
import argparse
import json
from . import core
from .providers import ProviderError, get_provider


def main():
    parser = argparse.ArgumentParser(description="VerityAgent RankForge SEO research CLI")
    parser.add_argument("keyword", help="Search keyword to investigate")
    parser.add_argument("--website", help="Optional target website URL")
    parser.add_argument("--market", default="United States")
    parser.add_argument("--language", default="en")
    parser.add_argument("--depth", type=int, choices=range(5, 21), default=10)
    args = parser.parse_args()
    try:
        core.init_db()
        result = core.run_analysis(args.keyword, args.website, args.market, args.language, args.depth, get_provider())
        print(json.dumps(result, indent=2))
    except (ValueError, ProviderError, RuntimeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
