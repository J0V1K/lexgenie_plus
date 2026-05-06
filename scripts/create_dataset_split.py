from __future__ import annotations

"""
Build the combined trigger-task dataset with random stratified 70/15/15 split.

Positives: citation_change == 'added' rows from filtered_case_linked_rows.csv
           (both linked and no_paragraph_link rows included for trigger task)
Negatives: combined_clean_negatives.csv (easy + hard, audit_flag == clean)

Stratification key: guide_id × importance_bin (1 / 2-3 / 4 / other)
Random seed: 42. Split proportions: train=0.70, dev=0.15, test=0.15.

Output:
  outputs/splits/trigger_dataset.csv       all rows with split column
  outputs/splits/split_report.json         counts by split / label / importance
"""

import csv
import json
import random
from collections import defaultdict
from pathlib import Path


POSITIVES_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
NEGATIVES_CSV = Path("outputs/negatives/combined_clean_negatives.csv")

OUTPUT_CSV = Path("outputs/splits/trigger_dataset.csv")
REPORT_JSON = Path("outputs/splits/split_report.json")

SEED = 42
TRAIN_FRAC = 0.70
DEV_FRAC = 0.15
# TEST_FRAC = 0.15 (remainder)


def importance_bin(level: str) -> str:
    if level == "1":
        return "1"
    if level in ("2", "3"):
        return "2-3"
    if level == "4":
        return "4"
    return "other"


def stratified_split(rows: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)

    # Group by (guide_id, importance_bin, label)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (r["guide_id"], r["_importance_bin"], r["label"])
        groups[key].append(r)

    result: list[dict] = []
    for key, grp in groups.items():
        rng.shuffle(grp)
        n = len(grp)
        n_train = max(1, round(n * TRAIN_FRAC))
        n_dev = max(0, round(n * DEV_FRAC))
        # Ensure at least 1 dev/test sample when group is large enough
        if n >= 3:
            n_dev = max(1, n_dev)
        n_test = n - n_train - n_dev
        if n_test < 0:
            n_dev += n_test
            n_test = 0

        for i, row in enumerate(grp):
            if i < n_train:
                row["split"] = "train"
            elif i < n_train + n_dev:
                row["split"] = "dev"
            else:
                row["split"] = "test"
            result.append(row)

    return result


def main() -> None:
    rows: list[dict] = []

    # Load positives
    for r in csv.DictReader(POSITIVES_CSV.open()):
        if r["citation_change"] != "added":
            continue
        rows.append({
            "source": "positive",
            "label": "1",
            "guide_id": r["guide_id"],
            "guide_title": r["guide_title"],
            "from_snapshot_date": r["from_snapshot_date"],
            "to_snapshot_date": r["to_snapshot_date"],
            "case_key": r["case_key"],
            "case_name": r["case_name"],
            "application_numbers": r["application_numbers"],
            "hudoc_importance_level": r["hudoc_importance_level"],
            "hudoc_doctype": r.get("hudoc_doctype", ""),
            "judgment_year": r.get("judgment_year", ""),
            "link_status": r.get("link_status", ""),
            "linked_sections": r.get("linked_sections", ""),
            "linked_change_types": r.get("linked_change_types", ""),
            "pre_text": r.get("pre_text", ""),
            "post_text": r.get("post_text", ""),
            "usable_for_relevance": r.get("usable_for_relevance", ""),
            "usable_for_location": r.get("usable_for_location", ""),
            "usable_for_edit_type": r.get("usable_for_edit_type", ""),
            "usable_for_generation": r.get("usable_for_generation", ""),
            "negative_type": "",
            "audit_flag": "clean",
            "_importance_bin": importance_bin(r["hudoc_importance_level"]),
        })

    # Load negatives
    for r in csv.DictReader(NEGATIVES_CSV.open()):
        rows.append({
            "source": "negative",
            "label": "0",
            "guide_id": r["guide_id"],
            "guide_title": r["guide_title"],
            "from_snapshot_date": r["from_snapshot_date"],
            "to_snapshot_date": r["to_snapshot_date"],
            "case_key": r["case_key"],
            "case_name": r["case_name"],
            "application_numbers": r["application_numbers"],
            "hudoc_importance_level": r["hudoc_importance_level"],
            "hudoc_doctype": r.get("hudoc_doctype", ""),
            "judgment_year": r.get("judgment_year", ""),
            "link_status": "",
            "linked_sections": "",
            "linked_change_types": "",
            "pre_text": "",
            "post_text": "",
            "usable_for_relevance": "",
            "usable_for_location": "",
            "usable_for_edit_type": "",
            "usable_for_generation": "",
            "negative_type": r.get("negative_type", ""),
            "audit_flag": r.get("audit_flag", "clean"),
            "_importance_bin": importance_bin(r["hudoc_importance_level"]),
        })

    rows = stratified_split(rows, SEED)

    # Remove internal column
    fieldnames = [k for k in rows[0].keys() if k != "_importance_bin"]
    # Reorder: split first
    fieldnames = ["split"] + [f for f in fieldnames if f != "split"]

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Report
    report: dict = {"total": len(rows), "by_split": {}}
    for sp in ("train", "dev", "test"):
        sp_rows = [r for r in rows if r["split"] == sp]
        pos = sum(1 for r in sp_rows if r["label"] == "1")
        neg = sum(1 for r in sp_rows if r["label"] == "0")
        report["by_split"][sp] = {
            "total": len(sp_rows),
            "positive": pos,
            "negative": neg,
            "ratio_neg_per_pos": round(neg / pos, 2) if pos else None,
        }

    for sp in ("train", "dev", "test"):
        sp_rows = [r for r in rows if r["split"] == sp]
        report["by_split"][sp]["importance_breakdown"] = {}
        for imp in ("1", "2-3", "4", "other"):
            imp_rows = [r for r in sp_rows if importance_bin(r["hudoc_importance_level"]) == imp]
            if imp_rows:
                report["by_split"][sp]["importance_breakdown"][imp] = {
                    "positive": sum(1 for r in imp_rows if r["label"] == "1"),
                    "negative": sum(1 for r in imp_rows if r["label"] == "0"),
                }

    REPORT_JSON.write_text(json.dumps(report, indent=2))

    print(f"Total rows: {len(rows)}")
    for sp in ("train", "dev", "test"):
        d = report["by_split"][sp]
        print(f"  {sp:5s}: {d['total']:4d}  (pos={d['positive']}, neg={d['negative']}, "
              f"ratio={d['ratio_neg_per_pos']:.2f}x)")
    print()
    print(f"Written: {OUTPUT_CSV}")
    print(f"Written: {REPORT_JSON}")


if __name__ == "__main__":
    main()
