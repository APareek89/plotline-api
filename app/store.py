"""SQLite persistence — Phase-1 dev environment override (§7): no RDS, local
processes only. Postgres+pgvector is the documented scale-up target."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from app import config

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _init(_conn)
    return _conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            data TEXT NOT NULL DEFAULT '{}'
        );
        INSERT OR IGNORE INTO profile (id, data) VALUES (1, '{}');

        CREATE TABLE IF NOT EXISTS series (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'context_ready',
            context TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS series_plan (
            series_id TEXT PRIMARY KEY,
            version INTEGER NOT NULL DEFAULT 1,
            plan TEXT,
            feedback TEXT,
            options TEXT,
            updated_at REAL
        );

        CREATE TABLE IF NOT EXISTS concept_state (
            series_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            status TEXT NOT NULL,
            ccs INTEGER,
            order_idx INTEGER NOT NULL,
            regen_count INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (series_id, concept_id)
        );

        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id TEXT PRIMARY KEY,
            series_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at REAL NOT NULL,
            finished_at REAL
        );

        CREATE TABLE IF NOT EXISTS performance_log (
            id TEXT PRIMARY KEY,
            series_id TEXT,
            concept_id TEXT,
            platform TEXT,
            metrics TEXT NOT NULL,
            pasted_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            content_type TEXT,
            series_id TEXT,
            created_at REAL NOT NULL
        );

        -- Addendum-01 §01/§03: chat-first threads. Ordinals are per-series,
        -- assigned at creation, never reused or renumbered on delete.
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY,
            series_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'planning',
            stage TEXT NOT NULL DEFAULT 'inspiration',
            chosen_formats TEXT,
            created_at REAL NOT NULL,
            UNIQUE (series_id, ordinal)
        );

        CREATE TABLE IF NOT EXISTS thread_messages (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            role TEXT NOT NULL,             -- 'agent' | 'user'
            envelope TEXT NOT NULL,         -- AgentMessage or UserEvent JSON
            created_at REAL NOT NULL,
            UNIQUE (thread_id, seq)
        );

        -- Per-artifact version history, rendered in the panel's Activity tab.
        CREATE TABLE IF NOT EXISTS artifact_activity (
            id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            event TEXT NOT NULL,            -- proposed | downgraded | refined | approved | …
            detail TEXT,
            created_at REAL NOT NULL
        );

        -- Addendum-02 §04: Post Cards — one object by id, three surfaces.
        CREATE TABLE IF NOT EXISTS post_cards (
            id TEXT PRIMARY KEY,
            series_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            concept_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            data TEXT NOT NULL,
            posted_at REAL,
            created_at REAL NOT NULL
        );

        -- Addendum-03 §Step 8: Ad Cards — the campaign deliverable. A campaign
        -- IS a series row, so campaign_id is a series id; variant sets share a
        -- variant_group_id and differ by variant_id.
        CREATE TABLE IF NOT EXISTS ad_cards (
            id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            option_id TEXT NOT NULL,
            variant_group_id TEXT,
            variant_id TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            data TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        -- Generated assets (mock or fal) + full generation audit trail.
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
            slot TEXT,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'ready',
            cost REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS generation_log (
            id TEXT PRIMARY KEY,
            thread_id TEXT,
            asset_id TEXT,
            event TEXT NOT NULL,            -- generate | reroll | seam_qa | edit_prompt | …
            prompt TEXT,
            model TEXT,
            seed TEXT,
            cost REAL DEFAULT 0,
            created_at REAL NOT NULL
        );
        """
    )
    # Addendum-01/02 migrations on existing dev DBs.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(concept_state)").fetchall()}
    if "coverage" not in cols:
        conn.execute("ALTER TABLE concept_state ADD COLUMN coverage INTEGER")
    if "production_status" not in cols:
        # Plans slot lifecycle (§01): planned | in_production | ready | posted
        conn.execute("ALTER TABLE concept_state ADD COLUMN production_status TEXT DEFAULT 'planned'")
    # Addendum-03: variant columns for dev DBs whose ad_cards predates them.
    ad_cols = {r[1] for r in conn.execute("PRAGMA table_info(ad_cards)").fetchall()}
    for col in ("variant_group_id", "variant_id"):
        if col not in ad_cols:
            conn.execute(f"ALTER TABLE ad_cards ADD COLUMN {col} TEXT")
    conn.commit()


def _now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ----------------------------------------------------------------- profile --


def get_profile() -> dict[str, Any]:
    with _lock:
        row = get_conn().execute("SELECT data FROM profile WHERE id = 1").fetchone()
    return json.loads(row["data"]) if row else {}


def save_profile(data: dict[str, Any]) -> None:
    with _lock:
        get_conn().execute("UPDATE profile SET data = ? WHERE id = 1", (json.dumps(data),))
        get_conn().commit()


# ------------------------------------------------------------------ series --


def create_series(context: dict[str, Any]) -> str:
    series_id = new_id("srs")
    with _lock:
        get_conn().execute(
            "INSERT INTO series (id, name, status, context, created_at) VALUES (?,?,?,?,?)",
            (series_id, context.get("name", "Untitled"), "context_ready", json.dumps(context), _now()),
        )
        get_conn().commit()
    return series_id


def update_series_context(series_id: str, context: dict[str, Any]) -> None:
    with _lock:
        get_conn().execute(
            "UPDATE series SET context = ?, name = ? WHERE id = ?",
            (json.dumps(context), context.get("name", "Untitled"), series_id),
        )
        get_conn().commit()


def set_series_status(series_id: str, status: str) -> None:
    with _lock:
        get_conn().execute("UPDATE series SET status = ? WHERE id = ?", (status, series_id))
        get_conn().commit()


def get_series(series_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = get_conn().execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "context": json.loads(row["context"]),
        "created_at": row["created_at"],
    }


def list_series() -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute("SELECT * FROM series ORDER BY created_at DESC").fetchall()
    out = []
    for row in rows:
        states = get_concept_states(row["id"])
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "status": row["status"],
                "context": json.loads(row["context"]),
                "created_at": row["created_at"],
                "concept_total": len(states),
                "concept_approved": sum(1 for s in states if s["approved"]),
            }
        )
    return out


# -------------------------------------------------------------------- plan --


def save_plan(series_id: str, plan: dict, feedback: dict, options: dict) -> None:
    with _lock:
        get_conn().execute(
            """INSERT INTO series_plan (series_id, version, plan, feedback, options, updated_at)
               VALUES (?, 1, ?, ?, ?, ?)
               ON CONFLICT(series_id) DO UPDATE SET
                 version = version + 1, plan = excluded.plan, feedback = excluded.feedback,
                 options = excluded.options, updated_at = excluded.updated_at""",
            (series_id, json.dumps(plan), json.dumps(feedback), json.dumps(options), _now()),
        )
        get_conn().commit()


def get_plan(series_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = get_conn().execute("SELECT * FROM series_plan WHERE series_id = ?", (series_id,)).fetchone()
    if not row or not row["plan"]:
        return None
    return {
        "version": row["version"],
        "plan": json.loads(row["plan"]),
        "feedback": json.loads(row["feedback"]) if row["feedback"] else None,
        "options": json.loads(row["options"]) if row["options"] else None,
        "updated_at": row["updated_at"],
    }


def upsert_concept_state(
    series_id: str, concept_id: str, status: str, ccs: int, order_idx: int,
    coverage: Optional[int] = None,
) -> None:
    with _lock:
        get_conn().execute(
            """INSERT INTO concept_state (series_id, concept_id, status, ccs, order_idx, coverage)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(series_id, concept_id) DO UPDATE SET
                 status = excluded.status, ccs = excluded.ccs, coverage = excluded.coverage""",
            (series_id, concept_id, status, ccs, order_idx, coverage),
        )
        get_conn().commit()


def get_concept_states(series_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute(
            "SELECT * FROM concept_state WHERE series_id = ? ORDER BY order_idx", (series_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_concept_approved(series_id: str, concept_id: str, approved: bool) -> None:
    with _lock:
        get_conn().execute(
            "UPDATE concept_state SET approved = ?, status = CASE WHEN ? THEN 'approved' ELSE status END WHERE series_id = ? AND concept_id = ?",
            (1 if approved else 0, 1 if approved else 0, series_id, concept_id),
        )
        get_conn().commit()


def set_concept_status(series_id: str, concept_id: str, status: str, ccs: Optional[int] = None) -> None:
    with _lock:
        if ccs is None:
            get_conn().execute(
                "UPDATE concept_state SET status = ? WHERE series_id = ? AND concept_id = ?",
                (status, series_id, concept_id),
            )
        else:
            get_conn().execute(
                "UPDATE concept_state SET status = ?, ccs = ? WHERE series_id = ? AND concept_id = ?",
                (status, ccs, series_id, concept_id),
            )
        get_conn().commit()


def bump_regen(series_id: str, concept_id: str) -> None:
    with _lock:
        get_conn().execute(
            "UPDATE concept_state SET regen_count = regen_count + 1 WHERE series_id = ? AND concept_id = ?",
            (series_id, concept_id),
        )
        get_conn().commit()


def reorder_concepts(series_id: str, ordered_ids: list[str]) -> None:
    with _lock:
        for idx, concept_id in enumerate(ordered_ids):
            get_conn().execute(
                "UPDATE concept_state SET order_idx = ? WHERE series_id = ? AND concept_id = ?",
                (idx, series_id, concept_id),
            )
        get_conn().commit()


# ----------------------------------------------------------------- threads --


def create_thread(series_id: str, kind: str = "planning") -> dict[str, Any]:
    """Ordinal = max ever used + 1 for this series (never reused: deleted
    threads leave a gap by design — numbers are identity, not position)."""
    thread_id = new_id("thr")
    with _lock:
        row = get_conn().execute(
            "SELECT COALESCE(MAX(ordinal), 0) AS m FROM threads WHERE series_id = ?", (series_id,)
        ).fetchone()
        ordinal = int(row["m"]) + 1
        get_conn().execute(
            "INSERT INTO threads (id, series_id, ordinal, kind, created_at) VALUES (?,?,?,?,?)",
            (thread_id, series_id, ordinal, kind, _now()),
        )
        get_conn().commit()
    return {"id": thread_id, "series_id": series_id, "ordinal": ordinal, "kind": kind, "stage": "inspiration"}


def get_thread(thread_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = get_conn().execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


def get_series_threads(series_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute(
            "SELECT * FROM threads WHERE series_id = ? ORDER BY ordinal", (series_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_threads(kind: Optional[str] = None) -> list[dict[str, Any]]:
    """All threads, newest series first — `kind='campaign'` is how the
    Campaigns list separates campaign series from content series."""
    with _lock:
        if kind:
            rows = get_conn().execute(
                "SELECT * FROM threads WHERE kind = ? ORDER BY created_at DESC, ordinal DESC", (kind,)
            ).fetchall()
        else:
            rows = get_conn().execute("SELECT * FROM threads ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def set_thread_stage(thread_id: str, stage: str) -> None:
    with _lock:
        get_conn().execute("UPDATE threads SET stage = ? WHERE id = ?", (stage, thread_id))
        get_conn().commit()


def set_thread_formats(thread_id: str, format_ids: list[str]) -> None:
    with _lock:
        get_conn().execute(
            "UPDATE threads SET chosen_formats = ? WHERE id = ?",
            (json.dumps(format_ids), thread_id),
        )
        get_conn().commit()


def append_message(thread_id: str, role: str, envelope: dict[str, Any]) -> dict[str, Any]:
    msg_id = new_id("msg")
    with _lock:
        row = get_conn().execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM thread_messages WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        seq = int(row["m"]) + 1
        get_conn().execute(
            "INSERT INTO thread_messages (id, thread_id, seq, role, envelope, created_at) VALUES (?,?,?,?,?,?)",
            (msg_id, thread_id, seq, role, json.dumps(envelope, default=str), _now()),
        )
        get_conn().commit()
    return {"id": msg_id, "seq": seq, "role": role, "envelope": envelope}


def get_messages(thread_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute(
            "SELECT * FROM thread_messages WHERE thread_id = ? AND seq > ? ORDER BY seq",
            (thread_id, after_seq),
        ).fetchall()
    return [
        {"id": r["id"], "seq": r["seq"], "role": r["role"],
         "envelope": json.loads(r["envelope"]), "created_at": r["created_at"]}
        for r in rows
    ]


def log_artifact_activity(thread_id: str, artifact_id: str, event: str, detail: Optional[str] = None) -> None:
    with _lock:
        get_conn().execute(
            "INSERT INTO artifact_activity (id, thread_id, artifact_id, event, detail, created_at) VALUES (?,?,?,?,?,?)",
            (new_id("act"), thread_id, artifact_id, event, detail, _now()),
        )
        get_conn().commit()


def get_artifact_activity(thread_id: str, artifact_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute(
            "SELECT event, detail, created_at FROM artifact_activity WHERE thread_id = ? AND artifact_id = ? ORDER BY created_at",
            (thread_id, artifact_id),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------- Addendum-02: post cards etc --


def save_post_card(card: dict[str, Any]) -> None:
    with _lock:
        get_conn().execute(
            """INSERT INTO post_cards (id, series_id, thread_id, concept_id, status, data, posted_at, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                 data = excluded.data, posted_at = excluded.posted_at""",
            (card["id"], card["series_id"], card["thread_id"], card["concept_id"],
             card["status"], json.dumps(card, default=str), card.get("posted_at"), card.get("created_at") or _now()),
        )
        get_conn().commit()


def get_post_card(card_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = get_conn().execute("SELECT data FROM post_cards WHERE id = ?", (card_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_post_cards(series_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _lock:
        if series_id:
            rows = get_conn().execute(
                "SELECT data FROM post_cards WHERE series_id = ? ORDER BY created_at DESC", (series_id,)
            ).fetchall()
        else:
            rows = get_conn().execute("SELECT data FROM post_cards ORDER BY created_at DESC").fetchall()
    return [json.loads(r["data"]) for r in rows]


def set_production_status(series_id: str, concept_id: str, status: str) -> None:
    with _lock:
        get_conn().execute(
            "UPDATE concept_state SET production_status = ? WHERE series_id = ? AND concept_id = ?",
            (status, series_id, concept_id),
        )
        get_conn().commit()


def add_asset(thread_id: Optional[str], slot: str, kind: str, path: str,
              params: dict[str, Any], cost: float, status: str = "ready") -> str:
    asset_id = new_id("ast")
    with _lock:
        get_conn().execute(
            "INSERT INTO assets (id, thread_id, slot, kind, path, params, status, cost, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (asset_id, thread_id, slot, kind, path, json.dumps(params, default=str), status, cost, _now()),
        )
        get_conn().commit()
    return asset_id


def get_asset(asset_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = get_conn().execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not row:
        return None
    return {**dict(row), "params": json.loads(row["params"])}


def set_asset_status(asset_id: str, status: str) -> None:
    with _lock:
        get_conn().execute("UPDATE assets SET status = ? WHERE id = ?", (status, asset_id))
        get_conn().commit()


def list_assets(thread_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _lock:
        if thread_id:
            rows = get_conn().execute("SELECT * FROM assets WHERE thread_id = ? ORDER BY created_at", (thread_id,)).fetchall()
        else:
            rows = get_conn().execute("SELECT * FROM assets ORDER BY created_at DESC LIMIT 200").fetchall()
    return [{**dict(r), "params": json.loads(r["params"])} for r in rows]


def log_generation(thread_id: Optional[str], asset_id: Optional[str], event: str,
                   prompt: Optional[str] = None, model: Optional[str] = None,
                   seed: Optional[str] = None, cost: float = 0) -> None:
    with _lock:
        get_conn().execute(
            "INSERT INTO generation_log (id, thread_id, asset_id, event, prompt, model, seed, cost, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("gen"), thread_id, asset_id, event, prompt, model, seed, cost, _now()),
        )
        get_conn().commit()


def get_generation_log(thread_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute(
            "SELECT * FROM generation_log WHERE thread_id = ? ORDER BY created_at", (thread_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------ Addendum-03: campaigns + ad cards -----

# My Campaigns lifecycle (v2 §My Campaigns). Stored in series.status — a
# campaign IS a series row, so there is one status column, not two.
CAMPAIGN_STATUSES = ("draft", "planned", "in_production", "ready", "live")


def save_ad_card(card: dict[str, Any]) -> None:
    with _lock:
        get_conn().execute(
            """INSERT INTO ad_cards (id, campaign_id, thread_id, option_id, variant_group_id,
                                     variant_id, status, data, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                 variant_group_id = excluded.variant_group_id,
                 variant_id = excluded.variant_id, data = excluded.data""",
            (card["id"], card["campaign_id"], card["thread_id"], card["option_id"],
             card.get("variant_group_id"), card.get("variant_id"), card.get("status", "draft"),
             json.dumps(card, default=str), card.get("created_at") or _now()),
        )
        get_conn().commit()


def get_ad_card(card_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = get_conn().execute("SELECT data FROM ad_cards WHERE id = ?", (card_id,)).fetchone()
    return json.loads(row["data"]) if row else None


def list_ad_cards(campaign_id: Optional[str] = None) -> list[dict[str, Any]]:
    with _lock:
        if campaign_id:
            rows = get_conn().execute(
                "SELECT data FROM ad_cards WHERE campaign_id = ? ORDER BY created_at DESC", (campaign_id,)
            ).fetchall()
        else:
            rows = get_conn().execute("SELECT data FROM ad_cards ORDER BY created_at DESC").fetchall()
    return [json.loads(r["data"]) for r in rows]


def set_campaign_status(campaign_id: str, status: str) -> None:
    if status not in CAMPAIGN_STATUSES:
        raise ValueError(f"unknown campaign status {status!r} — expected one of {list(CAMPAIGN_STATUSES)}")
    set_series_status(campaign_id, status)


def get_campaign_status(campaign_id: str) -> str:
    """Empty string when the campaign doesn't exist — callers 404 on that
    rather than being handed a plausible-looking 'draft'."""
    with _lock:
        row = get_conn().execute("SELECT status FROM series WHERE id = ?", (campaign_id,)).fetchone()
    return row["status"] if row else ""


def campaign_spend(campaign_id: str) -> float:
    """Spend-to-date in USD: every asset generated in any thread of this
    campaign, mock ($0) or fal. 0.0 when nothing has been generated."""
    with _lock:
        row = get_conn().execute(
            """SELECT COALESCE(SUM(a.cost), 0) AS total FROM assets a
               JOIN threads t ON t.id = a.thread_id WHERE t.series_id = ?""",
            (campaign_id,),
        ).fetchone()
    return float(row["total"] or 0.0)


# -------------------------------------------------------------------- runs --


def create_run(series_id: str, kind: str) -> str:
    run_id = new_id("run")
    with _lock:
        get_conn().execute(
            "INSERT INTO pipeline_runs (id, series_id, kind, status, created_at) VALUES (?,?,?,?,?)",
            (run_id, series_id, kind, "running", _now()),
        )
        get_conn().commit()
    return run_id


def finish_run(run_id: str, status: str, error: Optional[str] = None) -> None:
    with _lock:
        get_conn().execute(
            "UPDATE pipeline_runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
            (status, error, _now(), run_id),
        )
        get_conn().commit()


# ------------------------------------------------------------- performance --


def add_performance(series_id: Optional[str], concept_id: Optional[str], platform: str, metrics: dict) -> str:
    row_id = new_id("perf")
    with _lock:
        get_conn().execute(
            "INSERT INTO performance_log (id, series_id, concept_id, platform, metrics, pasted_at) VALUES (?,?,?,?,?,?)",
            (row_id, series_id, concept_id, platform, json.dumps(metrics), _now()),
        )
        get_conn().commit()
    return row_id


def list_performance() -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute("SELECT * FROM performance_log ORDER BY pasted_at DESC").fetchall()
    return [
        {**dict(r), "metrics": json.loads(r["metrics"])}
        for r in rows
    ]


# ----------------------------------------------------------------- uploads --


def add_upload(filename: str, kind: str, path: str, content_type: Optional[str], series_id: Optional[str]) -> str:
    upload_id = new_id("upl")
    with _lock:
        get_conn().execute(
            "INSERT INTO uploads (id, filename, kind, path, content_type, series_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (upload_id, filename, kind, path, content_type, series_id, _now()),
        )
        get_conn().commit()
    return upload_id


def get_uploads(upload_ids: list[str]) -> list[dict[str, Any]]:
    if not upload_ids:
        return []
    marks = ",".join("?" for _ in upload_ids)
    with _lock:
        rows = get_conn().execute(f"SELECT * FROM uploads WHERE id IN ({marks})", upload_ids).fetchall()
    return [dict(r) for r in rows]


def list_uploads() -> list[dict[str, Any]]:
    with _lock:
        rows = get_conn().execute("SELECT * FROM uploads ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]
