from __future__ import annotations

"""
End-to-end pipeline evaluation.

Chains the three steps evaluated separately:
  1. Trigger  — should this case cause a guide update?
  2. Location — which guide section should be updated?
  3. Edit     — what type of update is needed?

Uses predictions already written by the individual baseline scripts.
Reports pipeline accuracy at each cascade stage and joint accuracy.

Inputs:
  outputs/trigger/trigger_predictions.csv       (trigger step scores)
  outputs/prototype/retrieval_predictions.csv   (location step scores)
  outputs/prototype/edit_type_predictions.csv   (edit type predictions)

Output:
  outputs/pipeline/pipeline_eval.json
  outputs/pipeline/pipeline_predictions.csv
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TRIGGER_CSV = Path("outputs/trigger/trigger_predictions.csv")
TRIGGER_EVAL_JSON = Path("outputs/trigger/trigger_eval.json")
RETRIEVAL_CSV = Path("outputs/prototype/retrieval_predictions.csv")
EDIT_TYPE_CSV = Path("outputs/prototype/edit_type_predictions.csv")
OUTPUT_DIR = Path("outputs/pipeline")
OUTPUT_JSON = OUTPUT_DIR / "pipeline_eval.json"
PREDICTIONS_CSV = OUTPUT_DIR / "pipeline_predictions.csv"

# Best trigger model (importance+art from trigger eval)
TRIGGER_MODEL = "score_importance+art"
TRIGGER_MODEL_NAME = "importance+art"  # bare name in trigger_eval.json

TEMPORAL_CUTOFF = "2025-11-25"


def load_dev_threshold(model_name: str, fallback: float = 0.5) -> float:
    """Read dev-optimal threshold from trigger_eval.json; falls back to `fallback`."""
    if not TRIGGER_EVAL_JSON.exists():
        return fallback
    try:
        data = json.loads(TRIGGER_EVAL_JSON.read_text())
        for m in data.get("dev", []):
            if m.get("model") == model_name:
                return float(m.get("threshold", fallback))
    except Exception:
        pass
    return fallback


def load_keyed(path: Path, key_fields: list[str]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            key = tuple(row.get(k, "") for k in key_fields)
            out[key] = row
    return out


def main() -> None:
    trigger_threshold = load_dev_threshold(TRIGGER_MODEL_NAME)
    print(f"Trigger threshold (dev-optimal): {trigger_threshold:.4f}  "
          f"(source: {TRIGGER_EVAL_JSON if TRIGGER_EVAL_JSON.exists() else 'fallback=0.5'})")

    # ── Load predictions from each step ───────────────────────────────────────
    trigger_rows = load_keyed(TRIGGER_CSV, ["guide_id", "case_key", "to_snapshot_date"])
    retrieval_rows = load_keyed(
        RETRIEVAL_CSV, ["guide_id", "case_key", "to_snapshot_date"]
    )
    edit_rows = load_keyed(
        EDIT_TYPE_CSV,
        ["guide_id", "case_key", "from_snapshot_date", "to_snapshot_date"],
    )

    # ── Build per-row pipeline records ────────────────────────────────────────
    records: list[dict[str, Any]] = []

    for tkey, trow in trigger_rows.items():
        guide_id, case_key, to_date = tkey
        split = "test" if to_date >= TEMPORAL_CUTOFF else "dev"
        gold_label = int(trow.get("label", 0))

        # Trigger prediction
        trig_score = float(trow.get(TRIGGER_MODEL, 0.0))
        trig_pred = int(trig_score >= trigger_threshold)

        # Location prediction (from retrieval baseline, enriched model)
        rkey = (guide_id, case_key, to_date)
        rrow = retrieval_rows.get(rkey)
        if rrow:
            loc_evaluable = rrow.get("evaluable", "").lower() == "true"
            loc_hit1 = int(rrow.get("hit_at_1_enriched", 0))
            loc_hit3 = int(rrow.get("hit_at_3_enriched", 0))
            loc_mrr = float(rrow.get("reciprocal_rank_enriched", 0.0))
            loc_top1 = rrow.get("top_1_enriched", "")
            gold_sections = rrow.get("gold_sections", "")
        else:
            loc_evaluable = False
            loc_hit1 = loc_hit3 = 0
            loc_mrr = 0.0
            loc_top1 = gold_sections = ""

        # Edit type prediction — use case_key and guide_id only (from_date varies)
        edit_pred_type = edit_pred_subtype = ""
        for ekey, erow in edit_rows.items():
            if ekey[0] == guide_id and ekey[1] == case_key and ekey[3] == to_date:
                edit_pred_type = erow.get("edit_type", "")
                edit_pred_subtype = erow.get("edit_subtype", "")
                break

        records.append({
            "guide_id": guide_id,
            "case_key": case_key,
            "to_snapshot_date": to_date,
            "split": split,
            "gold_trigger": gold_label,
            # Trigger
            "trig_score": round(trig_score, 4),
            "trig_pred": trig_pred,
            "trig_correct": int(trig_pred == gold_label),
            # Location (only meaningful when trigger=true and row is evaluable)
            "loc_evaluable": loc_evaluable,
            "loc_hit1": loc_hit1,
            "loc_hit3": loc_hit3,
            "loc_mrr": loc_mrr,
            "loc_top1": loc_top1,
            "gold_sections": gold_sections,
            # Edit type
            "edit_pred_type": edit_pred_type,
            "edit_pred_subtype": edit_pred_subtype,
        })

    # ── Aggregate metrics ──────────────────────────────────────────────────────
    def metrics(subset: list[dict]) -> dict[str, Any]:
        n = len(subset)
        if not n:
            return {"n": 0}

        # Trigger accuracy (all rows)
        trig_acc = sum(r["trig_correct"] for r in subset) / n
        trig_tp = sum(1 for r in subset if r["trig_pred"] == 1 and r["gold_trigger"] == 1)
        trig_fp = sum(1 for r in subset if r["trig_pred"] == 1 and r["gold_trigger"] == 0)
        trig_fn = sum(1 for r in subset if r["trig_pred"] == 0 and r["gold_trigger"] == 1)
        trig_prec = trig_tp / (trig_tp + trig_fp) if trig_tp + trig_fp else 0
        trig_rec  = trig_tp / (trig_tp + trig_fn) if trig_tp + trig_fn else 0
        trig_f1   = 2 * trig_prec * trig_rec / (trig_prec + trig_rec) if trig_prec + trig_rec else 0

        # Location (conditioned on correct trigger + evaluable)
        trig_correct_pos = [r for r in subset if r["trig_pred"] == 1 and r["gold_trigger"] == 1 and r["loc_evaluable"]]
        loc_hit1 = sum(r["loc_hit1"] for r in trig_correct_pos) / len(trig_correct_pos) if trig_correct_pos else 0
        loc_hit3 = sum(r["loc_hit3"] for r in trig_correct_pos) / len(trig_correct_pos) if trig_correct_pos else 0
        loc_mrr  = sum(r["loc_mrr"] for r in trig_correct_pos) / len(trig_correct_pos) if trig_correct_pos else 0

        # Location unconditional (gold trigger=1, evaluable rows)
        gold_pos_eval = [r for r in subset if r["gold_trigger"] == 1 and r["loc_evaluable"]]
        loc_hit1_unc = sum(r["loc_hit1"] for r in gold_pos_eval) / len(gold_pos_eval) if gold_pos_eval else 0

        # Joint accuracy: trigger correct AND location hit@1
        joint = [r for r in subset if r["gold_trigger"] == 1 and r["loc_evaluable"]]
        joint_pipe_hit1 = sum(
            1 for r in joint if r["trig_pred"] == 1 and r["loc_hit1"] == 1
        ) / len(joint) if joint else 0

        # Edit type distribution (predicted, where trigger pred=1)
        edit_dist = Counter(
            r["edit_pred_type"] for r in subset if r["trig_pred"] == 1 and r["edit_pred_type"]
        )

        return {
            "n": n,
            "trigger": {
                "accuracy": round(trig_acc, 4),
                "precision": round(trig_prec, 4),
                "recall": round(trig_rec, 4),
                "f1": round(trig_f1, 4),
                "n_predicted_positive": trig_tp + trig_fp,
            },
            "location_conditional_on_correct_trigger": {
                "n": len(trig_correct_pos),
                "hit_at_1": round(loc_hit1, 4),
                "hit_at_3": round(loc_hit3, 4),
                "mrr": round(loc_mrr, 4),
            },
            "location_unconditional_gold_trigger": {
                "n": len(gold_pos_eval),
                "hit_at_1": round(loc_hit1_unc, 4),
            },
            "pipeline_hit_at_1": {
                "n": len(joint),
                "value": round(joint_pipe_hit1, 4),
                "description": (
                    "fraction of evaluable positive rows where trigger fires correctly "
                    "AND section retrieval hits at rank 1. "
                    "Edit-type correctness is NOT included in this metric."
                ),
            },
            "edit_type_distribution_predicted_positive": dict(edit_dist),
        }

    report: dict[str, Any] = {
        "trigger_model": TRIGGER_MODEL,
        "trigger_threshold": trigger_threshold,
        "trigger_threshold_source": "dev_optimal" if TRIGGER_EVAL_JSON.exists() else "fallback_0.5",
        "location_model": "bm25_enriched",
        "edit_model": "rule_based",
        "pipeline_metric_scope": "trigger + section_retrieval (edit correctness not evaluated)",
        "all": metrics(records),
        "dev": metrics([r for r in records if r["split"] == "dev"]),
        "test": metrics([r for r in records if r["split"] == "test"]),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    fieldnames = list(records[0].keys())
    with PREDICTIONS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    def print_section(label: str, m: dict) -> None:
        t = m["trigger"]
        loc_c = m["location_conditional_on_correct_trigger"]
        pipe = m["pipeline_hit_at_1"]
        print(f"\n{label} (n={m['n']})")
        print(f"  Trigger  prec={t['precision']:.3f}  rec={t['recall']:.3f}  F1={t['f1']:.3f}  "
              f"pred_pos={t['n_predicted_positive']}")
        print(f"  Location (given correct trigger, n={loc_c['n']})  "
              f"hit@1={loc_c['hit_at_1']:.3f}  hit@3={loc_c['hit_at_3']:.3f}  MRR={loc_c['mrr']:.3f}")
        print(f"  Pipeline hit@1={pipe['value']:.3f}  (n={pipe['n']} evaluable positive rows)")

    print("\n=== END-TO-END PIPELINE EVALUATION ===")
    print_section("ALL", report["all"])
    print_section("DEV", report["dev"])
    print_section("TEST", report["test"])

    print(f"\n  Edit type distribution (predicted positives):")
    for et, cnt in sorted(report["all"]["edit_type_distribution_predicted_positive"].items(),
                          key=lambda x: -x[1]):
        print(f"    {et:<20} {cnt}")

    print(f"\nFull report: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
