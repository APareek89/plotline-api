"""Eval harness — 20 seeded creator contexts through the full pipeline (§15).

Runs against whatever mode is configured: MOCK_LLM=1 exercises orchestration +
validators + retrieval; MOCK_LLM=0 (key in .env) evaluates the real prompts.
Treat prompt changes as unsafe until this passes (§11: run prompts through
evals with 20 seeded contexts before trusting; version every change).

Usage:  .venv/bin/python -m evals.run_evals            # uses .env settings
        MOCK_LLM=0 .venv/bin/python -m evals.run_evals # real model calls
Requires the RAG service (devrag stub or plotline-rag) on :8787.
"""
from __future__ import annotations

import json
import statistics
import time
from typing import Any

from app import orchestrator, store
from app.agents.runner import AgentHardFail
from app.rag_client import rag

NICHES = [
    ("AI tools", "aspiring creators 20-30 who want to grow with AI content"),
    ("skincare and beauty", "22-35 skincare buyers burnt by premium pricing"),
    ("fitness", "busy professionals 25-40 getting back in shape"),
    ("personal finance", "young earners 22-30 in metros"),
    ("food", "street-food lovers and home cooks"),
    ("fashion", "minimalist dressers 20-35"),
]

# 20 seeded contexts: 12 series creators (Ria-like) + 8 one-time campaigns (Kabir-like)
SEEDS: list[dict[str, Any]] = []
for i in range(12):
    area, audience = NICHES[i % len(NICHES)]
    SEEDS.append(
        {
            "name": f"eval-series-{i+1:02d}",
            "mode": "series",
            "content_area": area,
            "description": f"grow a {area} audience with consistent short-form content",
            "objective": ["followers", "engagement", "impressions"][i % 3],
            "target_audience": audience,
            "platforms": [["instagram_reels"], ["youtube_shorts"], ["instagram_reels", "youtube_shorts"]][i % 3],
            "cadence": {"type": "series", "posts_per_week": [3, 4, 5][i % 3], "weeks": [1, 2][i % 2]},
            "content_type": "text_video",
        }
    )
for i in range(8):
    area, audience = NICHES[i % len(NICHES)]
    SEEDS.append(
        {
            "name": f"eval-campaign-{i+1:02d}",
            "mode": "one_time",
            "content_area": area,
            "description": f"ad variants for a D2C {area} product launch",
            "objective": "conversions",
            "target_audience": audience,
            "platforms": [["instagram_reels"], ["tiktok"]][i % 2],
            "cadence": {"type": "one_time", "concept_count": [4, 6, 8][i % 3]},
            "content_type": "text_video",
        }
    )


def run() -> dict[str, Any]:
    # Isolated DB — eval series must never pollute the dev workspace.
    from pathlib import Path

    from app import config, store

    eval_db = Path(__file__).parent / "eval.db"
    if eval_db.exists():
        eval_db.unlink()
    config.DB_PATH = eval_db
    store._conn = None

    try:
        rag.health()
    except Exception as exc:
        raise SystemExit(
            f"RAG service not reachable on {rag.base_url} — start plotline-rag "
            f"(make serve-s3 PORT=8788) and devrag (:8787) first: {exc}"
        )

    results = []
    for i, form in enumerate(SEEDS, 1):
        started = time.time()
        record: dict[str, Any] = {"seed": form["name"], "objective": form["objective"]}
        try:
            context = orchestrator.run_intake(form, [])
            series_id = store.create_series(context.model_dump(mode="json"))
            run_id = store.create_run(series_id, "eval")
            orchestrator.generate_plan(series_id, run_id)
            bundle = store.get_plan(series_id)
            states = store.get_concept_states(series_id)
            if not bundle:
                raise RuntimeError("no plan persisted")
            run_row = store.get_conn().execute(
                "SELECT status, error FROM pipeline_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run_row["status"] != "complete":
                raise RuntimeError(run_row["error"] or "pipeline failed")
            ccs_values = [s["ccs"] for s in states]
            statuses = [s["status"] for s in states]
            options = bundle["options"]["concept_options"] if bundle["options"] else []
            record.update(
                {
                    "ok": True,
                    "concepts": len(states),
                    "ccs_mean": round(statistics.mean(ccs_values), 1),
                    "ccs_min": min(ccs_values),
                    "ccs_max": max(ccs_values),
                    "qualified": statuses.count("qualified") + statuses.count("strong"),
                    "strong": statuses.count("strong"),
                    "rework": statuses.count("rework"),
                    "options_sets": len(options),
                    "duration_s": round(time.time() - started, 2),
                }
            )
        except AgentHardFail as exc:
            record.update({"ok": False, "hard_fail": True, "error": str(exc)[:400]})
        except Exception as exc:
            record.update({"ok": False, "hard_fail": False, "error": str(exc)[:400]})
        results.append(record)
        flag = "OK " if record.get("ok") else "FAIL"
        print(f"[{i:02d}/20] {flag} {record['seed']}: " + (
            f"{record.get('concepts')} concepts, ccs {record.get('ccs_min')}-{record.get('ccs_max')}, "
            f"{record.get('qualified')} qualified, {record.get('options_sets')} option sets, {record.get('duration_s')}s"
            if record.get("ok") else record.get("error", "")
        ))

    ok = [r for r in results if r.get("ok")]
    summary = {
        "seeds": len(results),
        "passed": len(ok),
        "pass_rate": round(len(ok) / len(results), 2),
        "ccs_mean_overall": round(statistics.mean(r["ccs_mean"] for r in ok), 1) if ok else None,
        "hard_fails": sum(1 for r in results if r.get("hard_fail")),
    }
    report = {"summary": summary, "results": results}
    out = __file__.rsplit("/", 1)[0] + "/report.json"
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2)
    print("\nSUMMARY:", json.dumps(summary))
    print(f"report written to {out}")
    return report


if __name__ == "__main__":
    run()
