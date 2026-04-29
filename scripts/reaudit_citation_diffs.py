from __future__ import annotations

import csv
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from huggingface_hub import hf_hub_download


OUTPUT_DIR = Path("outputs/citation_diff_cleanup")
LOCAL_GROUPED = OUTPUT_DIR / "cleaned_diffs_grouped.json"
LOCAL_FLAT = OUTPUT_DIR / "cleaned_citation_diffs.csv"
SUMMARY_OUTPUT = OUTPUT_DIR / "reaudit_summary.md"
DETAILS_OUTPUT = OUTPUT_DIR / "comparison_details.json"

APP_NO_RE = re.compile(r"\b\d+/\d+\b")
LOWERCASE_START_RE = re.compile(r"^[a-z]")
V_START_RE = re.compile(r"^v\.")
SPACING_ARTIFACT_RE = re.compile(r"\b[A-Z]\s+[a-z]{3,}\b")


def load_reference_paths() -> tuple[Path, Path]:
    flat = Path(
        hf_hub_download(
            repo_id="lexgenie/echr-guide-citation-diffs",
            repo_type="dataset",
            filename="citation_diffs.csv",
        )
    )
    grouped = Path(
        hf_hub_download(
            repo_id="lexgenie/echr-guide-citation-diffs",
            repo_type="dataset",
            filename="diffs_grouped.json",
        )
    )
    return flat, grouped


def load_grouped(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def load_flat(path: Path) -> list[dict]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def diff_key(diff: dict) -> tuple[str, str, str]:
    return (diff["guide_id"], diff["from_snapshot"], diff["to_snapshot"])


def row_is_suspicious(row: dict) -> list[str]:
    reasons = []
    citation = row["citation"].strip()
    if LOWERCASE_START_RE.match(citation):
        reasons.append("lowercase_start")
    if V_START_RE.match(citation):
        reasons.append("starts_with_v")
    if citation.endswith(("-", "–", "—")):
        reasons.append("hanging_dash")
    if SPACING_ARTIFACT_RE.search(citation):
        reasons.append("spacing_artifact")
    if not APP_NO_RE.search(citation) and re.search(r"\b(19|20)\d{2}\b", citation):
        reasons.append("no_app_number")
    return reasons


def similar_noapp_pairs(grouped: list[dict]) -> list[dict]:
    findings = []
    for diff in grouped:
        noapp_added = [c for c in diff["added"] if not APP_NO_RE.search(c)]
        noapp_removed = [c for c in diff["removed"] if not APP_NO_RE.search(c)]
        for added in noapp_added:
            for removed in noapp_removed:
                score = SequenceMatcher(None, added.lower(), removed.lower()).ratio()
                if score >= 0.9:
                    findings.append(
                        {
                            "guide_id": diff["guide_id"],
                            "from_snapshot": diff["from_snapshot"],
                            "to_snapshot": diff["to_snapshot"],
                            "added": added,
                            "removed": removed,
                            "score": round(score, 3),
                        }
                    )
    return findings


def summarize_flat(rows: list[dict]) -> dict:
    suspicious = []
    reason_counts = Counter()
    for row in rows:
        reasons = row_is_suspicious(row)
        if not reasons:
            continue
        suspicious.append({**row, "reasons": reasons})
        reason_counts.update(reasons)

    return {
        "rows": len(rows),
        "guides": len({row["guide_id"] for row in rows}),
        "added": sum(row["change"] == "added" for row in rows),
        "removed": sum(row["change"] == "removed" for row in rows),
        "suspicious_rows": len(suspicious),
        "suspicious_reason_counts": dict(reason_counts),
        "suspicious_examples": suspicious[:25],
    }


def compare_grouped(reference: list[dict], local: list[dict]) -> dict:
    reference_by_key = {diff_key(diff): diff for diff in reference}
    local_by_key = {diff_key(diff): diff for diff in local}

    shared_keys = sorted(set(reference_by_key) & set(local_by_key))
    reference_only = sorted(set(reference_by_key) - set(local_by_key))
    local_only = sorted(set(local_by_key) - set(reference_by_key))

    exact_matches = 0
    changed_pairs = []
    published_only_rows = 0
    local_only_rows = 0

    for key in shared_keys:
        reference_diff = reference_by_key[key]
        local_diff = local_by_key[key]
        reference_added = set(reference_diff["added"])
        reference_removed = set(reference_diff["removed"])
        local_added = set(local_diff["added"])
        local_removed = set(local_diff["removed"])
        if reference_added == local_added and reference_removed == local_removed:
            exact_matches += 1
            continue

        published_only = sorted((reference_added - local_added) | (reference_removed - local_removed))
        cleaned_only = sorted((local_added - reference_added) | (local_removed - reference_removed))
        published_only_rows += len(published_only)
        local_only_rows += len(cleaned_only)
        changed_pairs.append(
            {
                "guide_id": key[0],
                "from_snapshot": key[1],
                "to_snapshot": key[2],
                "reference_added": reference_diff["added"],
                "reference_removed": reference_diff["removed"],
                "local_added": local_diff["added"],
                "local_removed": local_diff["removed"],
                "published_only": published_only,
                "cleaned_only": cleaned_only,
            }
        )

    return {
        "reference_pairs": len(reference),
        "local_pairs": len(local),
        "shared_pairs": len(shared_keys),
        "exact_match_pairs": exact_matches,
        "changed_pairs": len(changed_pairs),
        "reference_only_pairs": len(reference_only),
        "local_only_pairs": len(local_only),
        "published_only_rows_across_shared_pairs": published_only_rows,
        "cleaned_only_rows_across_shared_pairs": local_only_rows,
        "reference_only_pair_examples": [list(key) for key in reference_only[:15]],
        "local_only_pair_examples": [list(key) for key in local_only[:15]],
        "changed_pair_examples": changed_pairs[:25],
    }


def main() -> None:
    reference_flat_path, reference_grouped_path = load_reference_paths()
    reference_grouped = load_grouped(reference_grouped_path)
    reference_flat = load_flat(reference_flat_path)
    local_grouped = load_grouped(LOCAL_GROUPED)
    local_flat = load_flat(LOCAL_FLAT)

    comparison = compare_grouped(reference_grouped, local_grouped)
    reference_summary = summarize_flat(reference_flat)
    local_summary = summarize_flat(local_flat)
    reference_similar = similar_noapp_pairs(reference_grouped)
    local_similar = similar_noapp_pairs(local_grouped)

    details = {
        "comparison": comparison,
        "reference_summary": reference_summary,
        "local_summary": local_summary,
        "reference_similar_noapp_pairs": reference_similar[:50],
        "local_similar_noapp_pairs": local_similar[:50],
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DETAILS_OUTPUT.write_text(json.dumps(details, indent=2, ensure_ascii=False))

    lines = [
        "# Citation Diff Cleanup Re-Audit",
        "",
        "## Pair-Level Comparison",
        f"- published grouped diffs: `{comparison['reference_pairs']}`",
        f"- local grouped diffs: `{comparison['local_pairs']}`",
        f"- shared snapshot pairs: `{comparison['shared_pairs']}`",
        f"- exact matches on shared pairs: `{comparison['exact_match_pairs']}`",
        f"- changed shared pairs: `{comparison['changed_pairs']}`",
        f"- published-only pairs: `{comparison['reference_only_pairs']}`",
        f"- local-only pairs: `{comparison['local_only_pairs']}`",
        f"- published-only rows across changed shared pairs: `{comparison['published_only_rows_across_shared_pairs']}`",
        f"- cleaned-only rows across changed shared pairs: `{comparison['cleaned_only_rows_across_shared_pairs']}`",
        "",
        "## Flat-Row Summary",
        f"- published flat rows: `{reference_summary['rows']}`",
        f"- local flat rows: `{local_summary['rows']}`",
        f"- published suspicious rows: `{reference_summary['suspicious_rows']}`",
        f"- local suspicious rows: `{local_summary['suspicious_rows']}`",
        "",
        "## Suspicious Heuristics",
        f"- published similar add/remove no-app pairs: `{len(reference_similar)}`",
        f"- local similar add/remove no-app pairs: `{len(local_similar)}`",
        f"- published suspicious reason counts: `{reference_summary['suspicious_reason_counts']}`",
        f"- local suspicious reason counts: `{local_summary['suspicious_reason_counts']}`",
        "",
        "## Notes",
        "- This re-audit is heuristic. It is intended to prioritize rows for manual review, not to replace PDF verification.",
        "- Detailed examples are written to `outputs/citation_diff_cleanup/comparison_details.json`.",
        "",
    ]
    SUMMARY_OUTPUT.write_text("\n".join(lines))

    print(SUMMARY_OUTPUT.read_text())
    print(f"Wrote detailed comparison to {DETAILS_OUTPUT}")


if __name__ == "__main__":
    main()
