from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from build_case_catalog_from_guides import (
    extract_application_numbers,
    extract_case_name,
    extract_date_fields,
    make_case_key,
)
from rebuild_citation_diffs_clean import normalize_case_name, normalize_display_text


INPUT_CITATION_DIFFS = Path("outputs/citation_diff_cleanup/cleaned_citation_diffs.csv")
INPUT_CASE_CATALOG = Path("outputs/case_catalog/cases_catalog.csv")
INPUT_DIFF_DIR = Path("anas-diff-dataset")
OUTPUT_DIR = Path("outputs/case_linked_guide_diffs")
CASE_LEVEL_CSV = OUTPUT_DIR / "case_linked_guide_diffs.csv"
CASE_LEVEL_JSON = OUTPUT_DIR / "case_linked_guide_diffs.json"
PARAGRAPH_LEVEL_CSV = OUTPUT_DIR / "case_linked_guide_diff_paragraphs.csv"
PARAGRAPH_LEVEL_JSON = OUTPUT_DIR / "case_linked_guide_diff_paragraphs.json"
REPORT_JSON = OUTPUT_DIR / "case_linked_guide_diffs_report.json"

DIFF_FILENAME_RE = re.compile(r"^diff_(\d{4}-\d{2}-\d{2})__(\d{4}-\d{2}-\d{2})\.json$")


def snapshot_to_date(snapshot_name: str) -> str:
    timestamp = snapshot_name.split("__", 1)[0]
    return f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def serialize_pipe(values: list[str]) -> str:
    return "|".join(values)


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def load_case_catalog() -> dict[str, dict[str, str]]:
    with INPUT_CASE_CATALOG.open() as handle:
        rows = list(csv.DictReader(handle))
    return {row["case_key"]: row for row in rows}


def load_citation_diff_rows() -> list[dict[str, str]]:
    with INPUT_CITATION_DIFFS.open() as handle:
        return list(csv.DictReader(handle))


def build_diff_path_map() -> dict[tuple[str, str, str], Path]:
    diff_paths: dict[tuple[str, str, str], Path] = {}
    for path in INPUT_DIFF_DIR.glob("*/*.json"):
        match = DIFF_FILENAME_RE.match(path.name)
        if not match:
            continue
        diff_paths[(path.parent.name, match.group(1), match.group(2))] = path
    return diff_paths


def citation_to_case_struct(citation: str) -> dict[str, Any]:
    citation = normalize_display_text(citation)
    case_name = extract_case_name(citation)
    app_numbers = extract_application_numbers(citation)
    _, judgment_year = extract_date_fields(citation)
    return {
        "case_name": case_name,
        "application_numbers": app_numbers,
        "judgment_year": judgment_year or "",
        "case_key": make_case_key(case_name, app_numbers, judgment_year),
    }


def normalized_text(text: str) -> str:
    return normalize_case_name(normalize_display_text(text))


def text_for_change(paragraph: dict[str, Any], change: str) -> str:
    key = "text_b" if change == "added" else "text_a"
    return paragraph.get(key) or ""


def citations_for_change(paragraph: dict[str, Any], change: str) -> list[str]:
    key = "citations_added" if change == "added" else "citations_removed"
    return paragraph.get(key) or []


def paragraph_match(
    paragraph: dict[str, Any],
    *,
    target_case_key: str,
    target_case_name: str,
    target_apps: list[str],
    change: str,
) -> tuple[bool, list[str]]:
    strategies: list[str] = []
    target_name_norm = normalized_text(target_case_name)
    target_apps_set = set(target_apps)

    for citation in citations_for_change(paragraph, change):
        parsed = citation_to_case_struct(citation)
        if parsed["case_key"] == target_case_key:
            strategies.append("citation_field_case_key")
            break
        if target_apps_set and target_apps_set & set(parsed["application_numbers"]):
            strategies.append("citation_field_app_overlap")
        citation_name_norm = normalized_text(parsed["case_name"])
        if target_name_norm and citation_name_norm and (
            target_name_norm in citation_name_norm or citation_name_norm in target_name_norm
        ):
            strategies.append("citation_field_name_match")

    candidate_text = text_for_change(paragraph, change)
    candidate_text_norm = normalized_text(candidate_text)
    if target_name_norm and candidate_text_norm and target_name_norm in candidate_text_norm:
        strategies.append("paragraph_text_name_match")
    if target_apps_set and any(app_no in candidate_text for app_no in target_apps):
        strategies.append("paragraph_text_app_match")

    ordered = unique_preserve_order(strategies)
    return bool(ordered), ordered


def paragraph_sort_key(paragraph: dict[str, Any]) -> tuple[str, int, int]:
    return (
        paragraph.get("section_path") or "",
        int(paragraph.get("para_num_a") or 0),
        int(paragraph.get("para_num_b") or 0),
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    case_catalog = load_case_catalog()
    citation_rows = load_citation_diff_rows()
    diff_path_map = build_diff_path_map()

    case_level_rows: list[dict[str, Any]] = []
    paragraph_level_rows: list[dict[str, Any]] = []

    report = {
        "citation_diff_rows": len(citation_rows),
        "with_diff_pair": 0,
        "missing_diff_pair": 0,
        "linked_rows": 0,
        "unlinked_rows_existing_pair": 0,
        "paragraph_rows": 0,
        "link_status_counts": {},
        "match_strategy_counts": {},
    }

    link_status_counts: dict[str, int] = {}
    strategy_counts: dict[str, int] = {}

    for row in citation_rows:
        from_date = snapshot_to_date(row["from_snapshot"])
        to_date = snapshot_to_date(row["to_snapshot"])
        pair_key = (row["guide_id"], from_date, to_date)
        diff_path = diff_path_map.get(pair_key)

        citation_case = citation_to_case_struct(row["citation"])
        case_catalog_row = case_catalog.get(citation_case["case_key"], {})

        linked_paragraphs: list[dict[str, Any]] = []
        if diff_path is not None:
            report["with_diff_pair"] += 1
            diff_data = json.loads(diff_path.read_text())
            for paragraph in diff_data.get("paragraph_changes", []):
                matched, strategies = paragraph_match(
                    paragraph,
                    target_case_key=citation_case["case_key"],
                    target_case_name=citation_case["case_name"],
                    target_apps=citation_case["application_numbers"],
                    change=row["change"],
                )
                if not matched:
                    continue
                linked_paragraphs.append(
                    {
                        "match_strategies": strategies,
                        **paragraph,
                    }
                )
        else:
            report["missing_diff_pair"] += 1

        linked_paragraphs.sort(key=paragraph_sort_key)

        if diff_path is None:
            link_status = "diff_pair_missing"
        elif linked_paragraphs:
            link_status = "linked_paragraphs"
            report["linked_rows"] += 1
        else:
            link_status = "no_paragraph_link"
            report["unlinked_rows_existing_pair"] += 1

        link_status_counts[link_status] = link_status_counts.get(link_status, 0) + 1

        linked_sections = unique_preserve_order(
            [
                f"{paragraph.get('section_path')}: {paragraph.get('section_title')}"
                if paragraph.get("section_title")
                else (paragraph.get("section_path") or "")
                for paragraph in linked_paragraphs
            ]
        )
        linked_change_types = unique_preserve_order(
            [paragraph.get("change_type") or "" for paragraph in linked_paragraphs]
        )
        linked_para_refs = unique_preserve_order(
            [
                f"{paragraph.get('section_path')}|a:{paragraph.get('para_num_a')}|b:{paragraph.get('para_num_b')}"
                for paragraph in linked_paragraphs
            ]
        )
        linked_strategy_list = unique_preserve_order(
            [
                strategy
                for paragraph in linked_paragraphs
                for strategy in paragraph["match_strategies"]
            ]
        )
        for strategy in linked_strategy_list:
            strategy_counts[strategy] = strategy_counts.get(strategy, 0) + 1

        pre_text_segments = unique_preserve_order(
            [normalize_display_text(paragraph.get("text_a") or "") for paragraph in linked_paragraphs]
        )
        post_text_segments = unique_preserve_order(
            [normalize_display_text(paragraph.get("text_b") or "") for paragraph in linked_paragraphs]
        )

        case_level_row = {
            "guide_id": row["guide_id"],
            "guide_title": row["guide_title"],
            "from_snapshot": row["from_snapshot"],
            "to_snapshot": row["to_snapshot"],
            "from_snapshot_date": from_date,
            "to_snapshot_date": to_date,
            "from_version": row["from_version"],
            "to_version": row["to_version"],
            "diff_file": str(diff_path) if diff_path is not None else "",
            "case_key": citation_case["case_key"],
            "case_name": citation_case["case_name"],
            "application_numbers": serialize_pipe(citation_case["application_numbers"]),
            "judgment_year": citation_case["judgment_year"],
            "citation_change": row["change"],
            "citation_text": row["citation"],
            "hudoc_itemid": case_catalog_row.get("hudoc_itemid", ""),
            "hudoc_importance_level": case_catalog_row.get("hudoc_importance_level", ""),
            "hudoc_doctype": case_catalog_row.get("hudoc_doctype", ""),
            "hudoc_docname": case_catalog_row.get("hudoc_docname", ""),
            "link_status": link_status,
            "linked_paragraph_count": len(linked_paragraphs),
            "linked_sections": serialize_pipe(linked_sections),
            "linked_change_types": serialize_pipe(linked_change_types),
            "linked_paragraph_refs": serialize_pipe(linked_para_refs),
            "linked_match_strategies": serialize_pipe(linked_strategy_list),
            "pre_text": "\n\n".join(pre_text_segments),
            "post_text": "\n\n".join(post_text_segments),
            "from_wayback_url": row["from_wayback_url"],
            "to_wayback_url": row["to_wayback_url"],
            "from_hf_url": row["from_hf_url"],
            "to_hf_url": row["to_hf_url"],
        }
        case_level_rows.append(case_level_row)

        for paragraph_index, paragraph in enumerate(linked_paragraphs, start=1):
            paragraph_level_rows.append(
                {
                    "guide_id": row["guide_id"],
                    "guide_title": row["guide_title"],
                    "from_snapshot": row["from_snapshot"],
                    "to_snapshot": row["to_snapshot"],
                    "from_snapshot_date": from_date,
                    "to_snapshot_date": to_date,
                    "diff_file": str(diff_path) if diff_path is not None else "",
                    "case_key": citation_case["case_key"],
                    "case_name": citation_case["case_name"],
                    "application_numbers": serialize_pipe(citation_case["application_numbers"]),
                    "citation_change": row["change"],
                    "citation_text": row["citation"],
                    "paragraph_rank": paragraph_index,
                    "paragraph_match_strategies": serialize_pipe(paragraph["match_strategies"]),
                    "change_type": paragraph.get("change_type", ""),
                    "section_path": paragraph.get("section_path", ""),
                    "section_title": paragraph.get("section_title", ""),
                    "section_level": paragraph.get("section_level", ""),
                    "para_num_a": paragraph.get("para_num_a", ""),
                    "para_num_b": paragraph.get("para_num_b", ""),
                    "similarity": paragraph.get("similarity", ""),
                    "citations_added": serialize_pipe(paragraph.get("citations_added") or []),
                    "citations_removed": serialize_pipe(paragraph.get("citations_removed") or []),
                    "text_a": normalize_display_text(paragraph.get("text_a") or ""),
                    "text_b": normalize_display_text(paragraph.get("text_b") or ""),
                }
            )

    case_level_rows.sort(
        key=lambda row: (
            row["guide_id"],
            row["from_snapshot_date"],
            row["to_snapshot_date"],
            row["citation_change"],
            normalize_case_name(row["case_name"]),
        )
    )
    paragraph_level_rows.sort(
        key=lambda row: (
            row["guide_id"],
            row["from_snapshot_date"],
            row["to_snapshot_date"],
            normalize_case_name(row["case_name"]),
            int(row["paragraph_rank"]),
        )
    )

    report["paragraph_rows"] = len(paragraph_level_rows)
    report["link_status_counts"] = link_status_counts
    report["match_strategy_counts"] = strategy_counts

    write_csv(CASE_LEVEL_CSV, case_level_rows)
    CASE_LEVEL_JSON.write_text(json.dumps(case_level_rows, indent=2, ensure_ascii=False))
    write_csv(PARAGRAPH_LEVEL_CSV, paragraph_level_rows)
    PARAGRAPH_LEVEL_JSON.write_text(json.dumps(paragraph_level_rows, indent=2, ensure_ascii=False))
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"Wrote {len(case_level_rows)} case-level rows to {CASE_LEVEL_CSV}")
    print(f"Wrote {len(paragraph_level_rows)} paragraph-level rows to {PARAGRAPH_LEVEL_CSV}")
    print(f"Wrote report to {REPORT_JSON}")


if __name__ == "__main__":
    main()
