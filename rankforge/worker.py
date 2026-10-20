"""Standalone durable monitoring worker. Run with: python -m rankforge.worker."""
from __future__ import annotations

import os
import time

from . import core, monitoring


def main() -> None:
    core.init_db()
    monitoring.init_db()
    try:
        poll_seconds = int(os.getenv("RANKFORGE_WORKER_POLL_SECONDS", "15"))
    except ValueError:
        poll_seconds = 15
    poll_seconds = max(5, min(poll_seconds, 120))
    print(f"Verity RankForge monitoring worker started; poll={poll_seconds}s")
    while True:
        claimed = monitoring.claim_due(limit=3)
        for mission in claimed:
            try:
                outcome = monitoring.execute_claimed(mission)
                print(f"Mission #{mission['id']} completed as run #{outcome['run']['id']}")
            except Exception as exc:
                print(f"Mission #{mission['id']} failed: {type(exc).__name__}: {exc}")
        time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
