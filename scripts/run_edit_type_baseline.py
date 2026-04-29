from __future__ import annotations

"""
Paragraph-informed edit-type baseline.

Main labels:
  add_citation    - new case is incorporated through a new paragraph, paragraph
                    extension, or citation insert
  remove_citation - case is removed from the guide
  revise_text     - existing doctrinal text is materially rewritten or refreshed

This baseline uses the paragraph-linked diff rows rather than only the coarse
case-level linked_change_types field. That gives cleaner distinctions between
simple inserts and genuine rewrites.
"""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

INPUT_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
PARAGRAPH_CSV = Path("outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv")
OUTPUT_DIR = Path("outputs/prototype")
OUTPUT_JSON = OUTPUT_DIR / "edit_type_eval.json"
OUTPUT_CSV = OUTPUT_DIR / "edit_type_predictions.csv"

REVISION_TYPES = {"reformulation", "section_moved_modified", "minor_edit", "citation_updated"}
REMOVAL_TYPES = {"citation_removed", "paragraph_deleted"}
ADDITION_TYPES = {"citation_added", "paragraph_added"}


def normalize_bool(value: str) -> bool:
    return (value or "").strip().lower() == "true"


def row_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("guide_id", ""),
        row.get("case_key", ""),
        row.get("from_snapshot_date", ""),
        row.get("to_snapshot_date", ""),
    )


def parse_similarity(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def legacy_classify_edit_type(row: dict[str, str]) -> str:
    citation_change = row.get("citation_change", "")
    if citation_change == "removed":
        return "remove_citation"

    raw_types = row.get("linked_change_types", "")
    if not raw_types:
        return "add_citation"

    change_types = set(raw_types.split("|"))
    if "section_added" in change_types or "paragraph_added" in change_types:
        return "add_citation"

    if change_types & {"section_modified", "section_moved_modified"}:
        pre = (row.get("pre_text") or "").strip()
        post = (row.get("post_text") or "").strip()
        if pre and post:
            ratio = len(post) / len(pre) if pre else 2.0
            if ratio > 1.5:
                return "add_citation"
            return "revise_text"
        return "add_citation"

    if "section_removed" in change_types:
        return "remove_citation"

    return "add_citation"


def summarize_paragraph_group(paras: list[dict[str, str]]) -> dict[str, Any]:
    change_counts = Counter(p.get("change_type", "") for p in paras)
    similarities = [
        sim for sim in (parse_similarity(p.get("similarity", "")) for p in paras) if sim is not None
    ]
    pre_nonempty = sum(1 for p in paras if (p.get("text_a") or "").strip())
    post_nonempty = sum(1 for p in paras if (p.get("text_b") or "").strip())
    added_with_empty_pre = sum(
        1
        for p in paras
        if p.get("change_type") == "paragraph_added" and not (p.get("text_a") or "").strip()
    )
    citation_insert_like = sum(
        1
        for p in paras
        if p.get("change_type") == "citation_added"
        and (p.get("text_a") or "").strip()
        and (p.get("text_b") or "").strip()
    )
    return {
        "change_counts": change_counts,
        "change_types": sorted(k for k in change_counts if k),
        "n_linked_paragraphs": len(paras),
        "n_pre_nonempty": pre_nonempty,
        "n_post_nonempty": post_nonempty,
        "n_added_with_empty_pre": added_with_empty_pre,
        "n_citation_insert_like": citation_insert_like,
        "min_similarity": min(similarities) if similarities else None,
        "max_similarity": max(similarities) if similarities else None,
    }


def classify_edit_type(row: dict[str, str], summary: dict[str, Any] | None) -> tuple[str, str, str]:
    if row.get("citation_change") == "removed":
        return "remove_citation", "citation_remove", "high"

    if not summary:
        return legacy_classify_edit_type(row), "legacy_fallback", "low"

    change_types = set(summary["change_types"])
    change_counts: Counter[str] = summary["change_counts"]
    min_similarity = summary["min_similarity"]
    has_revision = bool(change_types & REVISION_TYPES)
    has_removal = bool(change_types & REMOVAL_TYPES)
    has_addition = bool(change_types & ADDITION_TYPES)
    has_citation_insert = change_counts["citation_added"] > 0
    has_new_paragraph = change_counts["paragraph_added"] > 0

    if has_removal and not has_addition and not has_revision:
        subtype = "paragraph_delete" if change_counts["paragraph_deleted"] else "citation_remove"
        return "remove_citation", subtype, "high"

    if has_revision:
        if has_new_paragraph and summary["n_added_with_empty_pre"] == change_counts["paragraph_added"]:
            # Mixed rows where a new paragraph is introduced alongside a light refresh are still
            # closer to an additive editorial action than a pure rewrite.
            if min_similarity is not None and min_similarity >= 0.85:
                return "add_citation", "new_paragraph", "medium"
        subtype = "doctrinal_rewrite"
        if change_counts["citation_updated"] and not (
            change_types & {"reformulation", "section_moved_modified"}
        ):
            subtype = "citation_refresh"
        elif change_counts["minor_edit"] and not (
            change_types & {"reformulation", "section_moved_modified", "citation_updated"}
        ):
            subtype = "paragraph_rewrite"
        confidence = "high" if (
            change_types & {"reformulation", "section_moved_modified"} or (min_similarity is not None and min_similarity < 0.85)
        ) else "medium"
        return "revise_text", subtype, confidence

    if has_new_paragraph and summary["n_added_with_empty_pre"] == change_counts["paragraph_added"]:
        return "add_citation", "new_paragraph", "high"

    if has_citation_insert:
        confidence = "high" if min_similarity is not None and min_similarity >= 0.95 else "medium"
        return "add_citation", "citation_insert", confidence

    if has_addition:
        return "add_citation", "paragraph_extension", "medium"

    return legacy_classify_edit_type(row), "legacy_fallback", "low"


def len_stats(subset: list[dict[str, Any]]) -> dict[str, Any]:
    ratios = [r["len_ratio"] for r in subset if r["len_ratio"] is not None]
    if not ratios:
        return {}
    ratios.sort()
    n = len(ratios)
    return {
        "n": n,
        "median_len_ratio": round(ratios[n // 2], 3),
        "mean_len_ratio": round(sum(ratios) / n, 3),
    }


def main() -> None:
    with INPUT_CSV.open() as f:
        rows = [r for r in csv.DictReader(f) if r.get("link_status") == "linked_paragraphs"]

    paragraph_groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    with PARAGRAPH_CSV.open() as f:
        for para_row in csv.DictReader(f):
            paragraph_groups[row_key(para_row)].append(para_row)

    results: list[dict[str, Any]] = []
    for row in rows:
        key = row_key(row)
        summary = summarize_paragraph_group(paragraph_groups[key]) if paragraph_groups.get(key) else None
        edit_type, edit_subtype, confidence = classify_edit_type(row, summary)
        legacy_edit_type = legacy_classify_edit_type(row)
        pre = (row.get("pre_text") or "").strip()
        post = (row.get("post_text") or "").strip()

        result = {
            "guide_id": row["guide_id"],
            "case_key": row["case_key"],
            "from_snapshot_date": row["from_snapshot_date"],
            "to_snapshot_date": row["to_snapshot_date"],
            "citation_change": row["citation_change"],
            "linked_change_types": row.get("linked_change_types", ""),
            "edit_type": edit_type,
            "edit_subtype": edit_subtype,
            "confidence": confidence,
            "legacy_edit_type": legacy_edit_type,
            "usable_for_generation": row.get("usable_for_generation", ""),
            "pre_len": len(pre),
            "post_len": len(post),
            "len_ratio": round(len(post) / len(pre), 3) if pre else None,
            "n_linked_paragraphs": summary["n_linked_paragraphs"] if summary else 0,
            "paragraph_change_types": "|".join(summary["change_types"]) if summary else "",
            "min_similarity": round(summary["min_similarity"], 4)
            if summary and summary["min_similarity"] is not None
            else None,
            "max_similarity": round(summary["max_similarity"], 4)
            if summary and summary["max_similarity"] is not None
            else None,
            "n_pre_nonempty_paragraphs": summary["n_pre_nonempty"] if summary else 0,
            "n_post_nonempty_paragraphs": summary["n_post_nonempty"] if summary else 0,
        }
        results.append(result)

    type_counts = Counter(r["edit_type"] for r in results)
    subtype_counts = Counter(r["edit_subtype"] for r in results)
    confidence_counts = Counter(r["confidence"] for r in results)
    usable = [r for r in results if normalize_bool(r["usable_for_generation"])]
    usable_types = Counter(r["edit_type"] for r in usable)
    usable_subtypes = Counter(r["edit_subtype"] for r in usable)
    legacy_agreement = sum(1 for r in results if r["edit_type"] == r["legacy_edit_type"])

    report: dict[str, Any] = {
        "n_evaluable": len(results),
        "edit_type_counts": dict(type_counts),
        "edit_type_pct": {k: round(v / len(results), 4) for k, v in type_counts.items()},
        "edit_subtype_counts": dict(subtype_counts),
        "confidence_counts": dict(confidence_counts),
        "usable_for_generation_counts": dict(usable_types),
        "usable_for_generation_subtypes": dict(usable_subtypes),
        "legacy_agreement": {
            "n_matching": legacy_agreement,
            "n_total": len(results),
            "pct_matching": round(legacy_agreement / len(results), 4) if results else 0.0,
        },
        "length_stats_by_type": {
            et: len_stats([r for r in results if r["edit_type"] == et]) for et in type_counts
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    fieldnames = list(results[0].keys())
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n=== EDIT TYPE DISTRIBUTION (linked rows, n={len(results)}) ===")
    for et, cnt in sorted(type_counts.items(), key=lambda x: (-x[1], x[0])):
        pct = cnt / len(results) * 100
        stats = report["length_stats_by_type"].get(et, {})
        med = stats.get("median_len_ratio", "—")
        print(f"  {et:<20} {cnt:4d} ({pct:.1f}%)  median len ratio: {med}")

    print("\n=== EDIT SUBTYPES ===")
    for subtype, cnt in sorted(subtype_counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {subtype:<20} {cnt:4d}")

    print(f"\n=== USABLE FOR GENERATION (n={len(usable)}) ===")
    for et, cnt in sorted(usable_types.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {et:<20} {cnt:4d}")

    print(
        "\nLegacy agreement: "
        f"{report['legacy_agreement']['n_matching']}/{report['legacy_agreement']['n_total']} "
        f"({report['legacy_agreement']['pct_matching']:.1%})"
    )
    print(f"\nFull report: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
