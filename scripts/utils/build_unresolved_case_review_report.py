from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


FILTERED_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
LINKED_CSV = Path("outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv")
CASE_GUIDES_CSV = Path("outputs/case_catalog/case_guides.csv")
RECOVERY_CSV = Path("outputs/case_catalog/audit/echr_extractor_metadata_recovery.csv")
OUTPUT_CSV = Path("outputs/prototype/unresolved_case_manual_review.csv")
OUTPUT_JSON = Path("outputs/prototype/unresolved_case_manual_review_summary.json")

KEY_FIELDS = [
    "guide_id",
    "case_key",
    "from_snapshot_date",
    "to_snapshot_date",
]

OUTPUT_FIELDS = [
    "case_key",
    "case_name",
    "application_numbers",
    "judgment_year",
    "guide_title",
    "guide_id",
    "from_snapshot_date",
    "to_snapshot_date",
    "citation_change",
    "citation_text",
    "missing_itemid",
    "missing_text",
    "hudoc_itemid",
    "hudoc_importance_level",
    "hudoc_doctype",
    "case_text_path",
    "linked_sections",
    "linked_change_types",
    "linked_match_strategies",
    "diff_file",
    "from_wayback_url",
    "to_wayback_url",
    "from_hf_url",
    "to_hf_url",
    "all_guide_memberships",
    "recovery_log_summary",
    "recovery_log_details",
    "pre_text",
    "post_text",
    "review_notes",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def make_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return tuple(row.get(field, "") for field in KEY_FIELDS)


def build_membership_index(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    by_case: dict[str, list[str]] = {}
    for row in rows:
        label = (
            f"{row.get('guide_title', '')} [{row.get('guide_id', '')}] "
            f"first_seen={row.get('first_seen_snapshot_timestamp', '')} "
            f"last_seen={row.get('last_seen_snapshot_timestamp', '')} "
            f"snapshots={row.get('snapshots_count', '')}"
        ).strip()
        by_case.setdefault(row.get("case_key", ""), []).append(label)
    return {key: sorted(values) for key, values in by_case.items()}


def build_recovery_index(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    by_case: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_case.setdefault(row.get("case_key", ""), []).append(row)
    return by_case


def summarize_recovery(rows: list[dict[str, str]]) -> tuple[str, str]:
    if not rows:
        return "", ""

    summary_parts: list[str] = []
    detail_parts: list[str] = []
    for row in rows:
        status = row.get("status", "")
        method = row.get("query_method", "")
        count = row.get("candidate_count", "")
        score = row.get("selected_score", "")
        selected = row.get("selected_itemid", "")
        summary = f"{status}"
        if method:
            summary += f":{method}"
        if count:
            summary += f":candidates={count}"
        if selected:
            summary += f":itemid={selected}"
        if score:
            summary += f":score={score}"
        summary_parts.append(summary)
        detail_parts.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return " | ".join(summary_parts), " || ".join(detail_parts)


def main() -> None:
    filtered_rows = read_csv(FILTERED_CSV)
    linked_rows = read_csv(LINKED_CSV)
    case_guide_rows = read_csv(CASE_GUIDES_CSV)
    recovery_rows = read_csv(RECOVERY_CSV) if RECOVERY_CSV.exists() else []

    linked_index = {make_key(row): row for row in linked_rows}
    membership_index = build_membership_index(case_guide_rows)
    recovery_index = build_recovery_index(recovery_rows)

    unresolved = [
        row
        for row in filtered_rows
        if row.get("link_status") == "linked_paragraphs"
        and (
            not row.get("hudoc_itemid", "").strip()
            or not row.get("case_text_path", "").strip()
        )
    ]

    report_rows: list[dict[str, Any]] = []
    for row in unresolved:
        linked_row = linked_index.get(make_key(row), {})
        recovery_summary, recovery_details = summarize_recovery(
            recovery_index.get(row.get("case_key", ""), [])
        )
        report_rows.append(
            {
                "case_key": row.get("case_key", ""),
                "case_name": row.get("case_name", ""),
                "application_numbers": row.get("application_numbers", ""),
                "judgment_year": row.get("judgment_year", ""),
                "guide_title": row.get("guide_title", ""),
                "guide_id": row.get("guide_id", ""),
                "from_snapshot_date": row.get("from_snapshot_date", ""),
                "to_snapshot_date": row.get("to_snapshot_date", ""),
                "citation_change": row.get("citation_change", ""),
                "citation_text": row.get("citation_text", ""),
                "missing_itemid": "true" if not row.get("hudoc_itemid", "").strip() else "false",
                "missing_text": "true" if not row.get("case_text_path", "").strip() else "false",
                "hudoc_itemid": row.get("hudoc_itemid", ""),
                "hudoc_importance_level": row.get("hudoc_importance_level", ""),
                "hudoc_doctype": row.get("hudoc_doctype", ""),
                "case_text_path": row.get("case_text_path", ""),
                "linked_sections": row.get("linked_sections", ""),
                "linked_change_types": row.get("linked_change_types", ""),
                "linked_match_strategies": row.get("linked_match_strategies", ""),
                "diff_file": row.get("diff_file", ""),
                "from_wayback_url": linked_row.get("from_wayback_url", ""),
                "to_wayback_url": linked_row.get("to_wayback_url", ""),
                "from_hf_url": linked_row.get("from_hf_url", ""),
                "to_hf_url": linked_row.get("to_hf_url", ""),
                "all_guide_memberships": " | ".join(
                    membership_index.get(row.get("case_key", ""), [])
                ),
                "recovery_log_summary": recovery_summary,
                "recovery_log_details": recovery_details,
                "pre_text": row.get("pre_text", ""),
                "post_text": row.get("post_text", ""),
                "review_notes": "",
            }
        )

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(report_rows)

    summary = {
        "rows": len(report_rows),
        "unique_cases": len({row["case_key"] for row in report_rows}),
        "output_csv": str(OUTPUT_CSV),
    }
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
