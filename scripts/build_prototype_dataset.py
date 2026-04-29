from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

INPUT_CSV = Path("outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv")
CASE_TEXT_INDEX_CSV = Path("outputs/case_texts/case_texts_index.csv")
OUTPUT_DIR = Path("outputs/prototype")
OUTPUT_CSV = OUTPUT_DIR / "filtered_case_linked_rows.csv"
OUTPUT_JSON = OUTPUT_DIR / "filtered_case_linked_rows.json"
REPORT_JSON = OUTPUT_DIR / "filtered_case_linked_rows_report.json"

CARRY_FIELDS = [
    "guide_id",
    "guide_title",
    "from_snapshot",
    "to_snapshot",
    "from_snapshot_date",
    "to_snapshot_date",
    "diff_file",
    "case_key",
    "case_name",
    "application_numbers",
    "judgment_year",
    "citation_change",
    "citation_text",
    "hudoc_itemid",
    "hudoc_importance_level",
    "hudoc_doctype",
    "hudoc_docname",
    "link_status",
    "linked_paragraph_count",
    "linked_sections",
    "linked_change_types",
    "linked_paragraph_refs",
    "linked_match_strategies",
    "pre_text",
    "post_text",
]

FLAG_FIELDS = [
    "usable_for_relevance",
    "usable_for_location",
    "usable_for_edit_type",
    "usable_for_generation",
    "strict_citation_field_match",
    "case_text_available",
]

CASE_TEXT_FIELDS = [
    "case_text_path",
    "case_text_chars",
]


def load_rows() -> list[dict[str, str]]:
    with INPUT_CSV.open() as handle:
        return list(csv.DictReader(handle))


def load_case_text_index() -> dict[str, dict[str, str]]:
    if not CASE_TEXT_INDEX_CSV.exists():
        return {}
    with CASE_TEXT_INDEX_CSV.open() as handle:
        rows = list(csv.DictReader(handle))
    return {row["hudoc_itemid"]: row for row in rows if row["status"] == "ok"}


def compute_flags(
    row: dict[str, str], case_text_index: dict[str, dict[str, str]]
) -> tuple[dict[str, bool], dict[str, str]]:
    linked = row.get("link_status") == "linked_paragraphs"
    has_sections = bool(row.get("linked_sections", "").strip())
    has_change_types = bool(row.get("linked_change_types", "").strip())
    has_post_text = bool(row.get("post_text", "").strip())
    citation_added = row.get("citation_change") == "added"
    strategies = row.get("linked_match_strategies", "").split("|")

    itemid = row.get("hudoc_itemid", "")
    case_text = case_text_index.get(itemid) if itemid else None

    usable_for_relevance = linked
    usable_for_location = linked and has_sections
    usable_for_edit_type = usable_for_location and has_change_types
    usable_for_generation = (
        usable_for_location and citation_added and has_post_text
    )
    strict_citation_field_match = "citation_field_case_key" in strategies

    flags = {
        "usable_for_relevance": usable_for_relevance,
        "usable_for_location": usable_for_location,
        "usable_for_edit_type": usable_for_edit_type,
        "usable_for_generation": usable_for_generation,
        "strict_citation_field_match": strict_citation_field_match,
        "case_text_available": bool(case_text),
    }
    text_fields = {
        "case_text_path": case_text["text_path"] if case_text else "",
        "case_text_chars": case_text["text_chars"] if case_text else "",
    }
    return flags, text_fields


def project_row(
    row: dict[str, str], case_text_index: dict[str, dict[str, str]]
) -> dict[str, Any]:
    projected: dict[str, Any] = {field: row.get(field, "") for field in CARRY_FIELDS}
    flags, text_fields = compute_flags(row, case_text_index)
    projected.update(flags)
    projected.update(text_fields)
    return projected


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    flag_counts = {
        field: sum(1 for r in rows if r[field]) for field in FLAG_FIELDS
    }
    by_guide = Counter(r["guide_id"] for r in rows if r["usable_for_location"])
    by_citation_change = Counter(
        r["citation_change"] for r in rows if r["usable_for_relevance"]
    )
    importance_counts = Counter(
        r["hudoc_importance_level"] for r in rows if r["usable_for_relevance"]
    )
    return {
        "input_rows": total,
        "flag_counts": flag_counts,
        "usable_for_location_by_guide": dict(by_guide.most_common()),
        "usable_for_relevance_by_citation_change": dict(by_citation_change),
        "usable_for_relevance_by_importance": dict(importance_counts),
    }


def write_csv(rows: list[dict[str, Any]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = CARRY_FIELDS + FLAG_FIELDS + CASE_TEXT_FIELDS
    with OUTPUT_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _csv_value(row[field]) for field in fieldnames}
            )


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def write_json(rows: list[dict[str, Any]]) -> None:
    OUTPUT_JSON.write_text(json.dumps(rows, indent=2, ensure_ascii=False))


def main() -> None:
    raw_rows = load_rows()
    case_text_index = load_case_text_index()
    projected = [project_row(row, case_text_index) for row in raw_rows]
    write_csv(projected)
    write_json(projected)
    report = build_report(projected)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
