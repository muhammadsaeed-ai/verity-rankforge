"""Durable, database-backed keyword monitoring missions and leased job execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import core
from .providers import ProviderError, get_provider

MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 43200
LEASE_MINUTES = 30


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    with core.connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS monitoring_missions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          keyword TEXT NOT NULL,
          website TEXT,
          market TEXT NOT NULL,
          language TEXT NOT NULL,
          depth INTEGER NOT NULL,
          interval_minutes INTEGER NOT NULL CHECK(interval_minutes >= 60 AND interval_minutes <= 43200),
          enabled INTEGER NOT NULL DEFAULT 1,
          next_run_at TEXT NOT NULL,
          last_run_at TEXT,
          last_run_id INTEGER,
          last_error TEXT,
          consecutive_failures INTEGER NOT NULL DEFAULT 0,
          lease_until TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_monitoring_due
          ON monitoring_missions(enabled, next_run_at, lease_until);
        """)


def _row(row) -> dict[str, Any]:
    return {
        "id": row["id"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        "keyword": row["keyword"], "website": row["website"], "market": row["market"],
        "language": row["language"], "depth": row["depth"], "interval_minutes": row["interval_minutes"],
        "enabled": bool(row["enabled"]), "next_run_at": row["next_run_at"],
        "last_run_at": row["last_run_at"], "last_run_id": row["last_run_id"],
        "last_error": row["last_error"], "consecutive_failures": row["consecutive_failures"],
        "lease_until": row["lease_until"], "min_interval_minutes": MIN_INTERVAL_MINUTES,
    }


def create_mission(keyword: str, website: str | None, market: str, language: str, depth: int, interval_minutes: int) -> dict:
    phrase = " ".join(keyword.split())
    if len(phrase) < 2 or len(phrase) > 180:
        raise ValueError("Keyword must contain between 2 and 180 characters.")
    if not MIN_INTERVAL_MINUTES <= int(interval_minutes) <= MAX_INTERVAL_MINUTES:
        raise ValueError(f"Interval must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES} minutes.")
    if not 5 <= int(depth) <= 20:
        raise ValueError("Result depth must be between 5 and 20.")
    clean_website = core.validate_public_url(website) if website else None
    if not market.strip() or len(market) > 80:
        raise ValueError("Market must contain 1 to 80 characters.")
    if not language.strip() or len(language) > 12:
        raise ValueError("Language must contain 1 to 12 characters.")
    now = datetime.now(timezone.utc)
    created = now.isoformat()
    next_run = created
    with core.connect() as db:
        cur = db.execute(
            """INSERT INTO monitoring_missions
            (created_at,updated_at,keyword,website,market,language,depth,interval_minutes,enabled,next_run_at)
            VALUES(?,?,?,?,?,?,?,?,1,?)""",
            (created, created, phrase, clean_website, market.strip(), language.strip(), int(depth), int(interval_minutes), next_run),
        )
        row = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row(row)


def list_missions() -> list[dict]:
    with core.connect() as db:
        rows = db.execute("SELECT * FROM monitoring_missions ORDER BY enabled DESC, next_run_at ASC, id DESC").fetchall()
    return [_row(row) for row in rows]


def get_mission(mission_id: int) -> dict | None:
    with core.connect() as db:
        row = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (mission_id,)).fetchone()
    return _row(row) if row else None


def set_enabled(mission_id: int, enabled: bool) -> dict | None:
    now = now_iso()
    with core.connect() as db:
        row = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (mission_id,)).fetchone()
        if row is None:
            return None
        next_run = now if enabled else row["next_run_at"]
        db.execute("UPDATE monitoring_missions SET enabled=?, updated_at=?, next_run_at=? WHERE id=?",
                   (int(enabled), now, next_run, mission_id))
        row = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (mission_id,)).fetchone()
    return _row(row)


def _claim_specific(mission_id: int) -> dict | None:
    now = datetime.now(timezone.utc)
    lease = (now + timedelta(minutes=LEASE_MINUTES)).isoformat()
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (mission_id,)).fetchone()
        if row is None or not row["enabled"]:
            db.rollback()
            return None
        until = row["lease_until"]
        if until and until > now.isoformat():
            db.rollback()
            raise RuntimeError("This mission is already running.")
        db.execute("UPDATE monitoring_missions SET lease_until=?, updated_at=? WHERE id=?",
                   (lease, now.isoformat(), mission_id))
        claimed = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (mission_id,)).fetchone()
        db.commit()
    return _row(claimed)


def claim_due(limit: int = 3) -> list[dict]:
    now = datetime.now(timezone.utc)
    stamp = now.isoformat()
    lease = (now + timedelta(minutes=LEASE_MINUTES)).isoformat()
    limit = max(1, min(int(limit), 20))
    claimed = []
    with core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """SELECT * FROM monitoring_missions
               WHERE enabled=1 AND next_run_at<=? AND (lease_until IS NULL OR lease_until<=?)
               ORDER BY next_run_at ASC, id ASC LIMIT ?""",
            (stamp, stamp, limit),
        ).fetchall()
        for row in rows:
            db.execute("UPDATE monitoring_missions SET lease_until=?, updated_at=? WHERE id=?",
                       (lease, stamp, row["id"]))
            fresh = db.execute("SELECT * FROM monitoring_missions WHERE id=?", (row["id"],)).fetchone()
            claimed.append(_row(fresh))
        db.commit()
    return claimed


def _finish(mission_id: int, run_id: int | None = None, error: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    with core.connect() as db:
        row = db.execute("SELECT interval_minutes,consecutive_failures FROM monitoring_missions WHERE id=?",
                         (mission_id,)).fetchone()
        if row is None:
            return
        if error is None:
            next_at = now + timedelta(minutes=row["interval_minutes"])
            db.execute(
                """UPDATE monitoring_missions SET updated_at=?,last_run_at=?,last_run_id=?,last_error=NULL,
                   consecutive_failures=0,next_run_at=?,lease_until=NULL WHERE id=?""",
                (now.isoformat(), now.isoformat(), run_id, next_at.isoformat(), mission_id),
            )
        else:
            failures = int(row["consecutive_failures"]) + 1
            backoff_minutes = min(60, max(5, 5 * (2 ** min(failures - 1, 4))))
            db.execute(
                """UPDATE monitoring_missions SET updated_at=?,last_error=?,consecutive_failures=?,
                   next_run_at=?,lease_until=NULL WHERE id=?""",
                (now.isoformat(), error[:1000], failures, (now + timedelta(minutes=backoff_minutes)).isoformat(), mission_id),
            )


def execute_claimed(mission: dict) -> dict:
    try:
        provider = get_provider()
        run = core.run_analysis(
            mission["keyword"], mission.get("website"), mission["market"],
            mission["language"], mission["depth"], provider,
        )
        _finish(mission["id"], run_id=run["id"])
        return {"mission": get_mission(mission["id"]), "run": run}
    except Exception as exc:
        _finish(mission["id"], error=f"{type(exc).__name__}: {exc}")
        raise


def run_now(mission_id: int) -> dict:
    mission = _claim_specific(mission_id)
    if mission is None:
        existing = get_mission(mission_id)
        if existing is None:
            raise KeyError("Monitoring mission not found.")
        raise ValueError("Monitoring mission is paused. Enable it before running.")
    return execute_claimed(mission)
