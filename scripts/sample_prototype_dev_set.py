from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

INPUT_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
OUTPUT_DIR = Path("outputs/prototype")
OUTPUT_CSV = OUTPUT_DIR / "dev_audit_sample.csv"
OUTPUT_JSON = OUTPUT_DIR / "dev_audit_sample.json"
REPORT_JSON = OUTPUT_DIR / "dev_audit_sample_report.json"

TARGET_SIZE = 120
RANDOM_SEED = 42

ANNOTATION_FIELDS = [
    "gold_use_row",
    "gold_section",
    "gold_edit_type",
    "gold_link_correct",
    "gold_generation_feasible",
    "notes",
]

CARRY_FIELDS = [
    "guide_id",
    "guide_title",
    "from_snapshot_date",
    "to_snapshot_date",
    "case_key",
    "case_name",
    "application_numbers",
    "judgment_year",
    "citation_change",
    "citation_text",
    "hudoc_importance_level",
    "hudoc_doctype",
    "link_status",
    "linked_sections",
    "linked_change_types",
    "linked_match_strategies",
    "linked_paragraph_refs",
    "pre_text",
    "post_text",
    "strict_citation_field_match",
    "usable_for_relevance",
    "usable_for_location",
    "usable_for_edit_type",
    "usable_for_generation",
]


def load_usable_rows() -> list[dict[str, str]]:
    with INPUT_CSV.open() as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if row.get("usable_for_relevance") == "true"]


def stratum_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        row["guide_id"],
        row["citation_change"],
        row["strict_citation_field_match"],
    )


def stratified_sample(
    rows: list[dict[str, str]], target: int, seed: int
) -> list[dict[str, str]]:
    rng = random.Random(seed)

    buckets: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        buckets[stratum_key(row)].append(row)

    for bucket in buckets.values():
        rng.shuffle(bucket)

    stratum_order = sorted(buckets.keys())
    rng.shuffle(stratum_order)

    selected: list[dict[str, str]] = []
    cursor: dict[tuple[str, str, str], int] = {key: 0 for key in buckets}

    while len(selected) < target:
        progress = False
        for key in stratum_order:
            if len(selected) >= target:
                break
            idx = cursor[key]
            bucket = buckets[key]
            if idx < len(bucket):
                selected.append(bucket[idx])
                cursor[key] = idx + 1
                progress = True
        if not progress:
            break

    return selected


def project(row: dict[str, str]) -> dict[str, Any]:
    projected: dict[str, Any] = {field: row.get(field, "") for field in CARRY_FIELDS}
    for field in ANNOTATION_FIELDS:
        projected[field] = ""
    return projected


def build_report(sample: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter

    return {
        "sample_size": len(sample),
        "seed": RANDOM_SEED,
        "by_guide": dict(Counter(r["guide_id"] for r in sample).most_common()),
        "by_citation_change": dict(Counter(r["citation_change"] for r in sample)),
        "strict_citation_field_match_count": sum(
            1 for r in sample if r["strict_citation_field_match"] == "true"
        ),
        "usable_for_generation_count": sum(
            1 for r in sample if r["usable_for_generation"] == "true"
        ),
    }


def write_outputs(sample: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = CARRY_FIELDS + ANNOTATION_FIELDS
    with OUTPUT_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in sample:
            writer.writerow(row)
    OUTPUT_JSON.write_text(json.dumps(sample, indent=2, ensure_ascii=False))


def main() -> None:
    usable = load_usable_rows()
    sample = stratified_sample(usable, TARGET_SIZE, RANDOM_SEED)
    projected = [project(row) for row in sample]
    write_outputs(projected)
    report = build_report(projected)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
