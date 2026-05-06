from __future__ import annotations

"""
Extract hard negative examples from the existing HUDOC cache.

Hard negatives are cases that were published in a guide transition window,
were NOT added to that guide, but share surface features with positives:
  - HUDOC importance level 1, 2, or 3
  - Convention article overlaps with the guide's primary article

These are more informative for training and evaluation than the level-4,
wrong-article cases that dominate the standard negative set.

For thematic guides whose titles don't contain an article number, the
importance filter (level 1-3) is applied without an article filter.

Outputs:
  outputs/negatives/hard_negative_examples.csv
  outputs/negatives/hard_negative_examples_report.json
  outputs/negatives/combined_clean_negatives.csv  (easy + hard, audit_flag == clean)
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path


CACHE_JSON = Path("outputs/negatives/hudoc_negatives_cache.json")
FILTERED_ROWS_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
CASE_GUIDES_CSV = Path("outputs/case_catalog/case_guides.csv")
CASES_CATALOG_CSV = Path("outputs/case_catalog/cases_catalog.csv")
EASY_NEGATIVES_CSV = Path("outputs/negatives/negative_examples_audited.csv")

HARD_OUTPUT_CSV = Path("outputs/negatives/hard_negative_examples.csv")
HARD_REPORT_JSON = Path("outputs/negatives/hard_negative_examples_report.json")
COMBINED_OUTPUT_CSV = Path("outputs/negatives/combined_clean_negatives.csv")

RIGHT_CENSOR_CUTOFF = "2025-01-01"


def guide_article_from_title(title: str) -> str | None:
    m = re.search(r"Article\s+(\d+)", title)
    return m.group(1) if m else None


def timestamp_to_date(ts: str) -> str:
    if len(ts) >= 8:
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    return ""


def build_appno_to_case_key(catalog_path: Path) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in csv.DictReader(catalog_path.open()):
        ck = row["case_key"]
        for appno in row["application_numbers"].split("|"):
            appno = appno.strip()
            if appno:
                lookup[appno] = ck
    return lookup


def build_guide_membership(case_guides_path: Path) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for row in csv.DictReader(case_guides_path.open()):
        ck = row["case_key"]
        lookup[(ck, row["guide_id"])] = row["first_seen_snapshot_timestamp"]
    return lookup


def main() -> None:
    cache = json.loads(CACHE_JSON.read_text())
    appno_to_key = build_appno_to_case_key(CASES_CATALOG_CSV)
    guide_membership = build_guide_membership(CASE_GUIDES_CSV)

    # Build positive appnos and guide metadata per transition
    positive_appnos: dict[tuple, set[str]] = defaultdict(set)
    guide_meta: dict[str, dict] = {}
    for r in csv.DictReader(FILTERED_ROWS_CSV.open()):
        key = (r["guide_id"], r["from_snapshot_date"], r["to_snapshot_date"])
        for appno in r["application_numbers"].split("|"):
            if appno.strip():
                positive_appnos[key].add(appno.strip().lower())
        gid = r["guide_id"]
        if gid not in guide_meta:
            guide_meta[gid] = {
                "guide_title": r["guide_title"],
                "article": guide_article_from_title(r["guide_title"]),
            }

    # Collect all unique transitions
    transitions: set[tuple[str, str, str]] = set()
    for r in csv.DictReader(FILTERED_ROWS_CSV.open()):
        transitions.add((r["guide_id"], r["from_snapshot_date"], r["to_snapshot_date"]))

    # Track cases already in the easy negative set to avoid duplicates
    easy_keys: set[tuple[str, str, str]] = set()
    for r in csv.DictReader(EASY_NEGATIVES_CSV.open()):
        easy_keys.add((r["guide_id"], r["from_snapshot_date"], r["to_snapshot_date"]))

    hard_rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()  # (case_key, guide_id, window_key) dedup

    for guide_id, from_d, to_d in sorted(transitions):
        cache_key = f"{from_d}|{to_d}|200"
        results = cache.get(cache_key, [])
        meta = guide_meta.get(guide_id, {})
        guide_title = meta.get("guide_title", "")
        guide_art = meta.get("article")
        pos = positive_appnos[(guide_id, from_d, to_d)]

        for r in results:
            cols = r["columns"]
            imp = str(cols.get("importance", "") or "")
            if imp not in ("1", "2", "3"):
                continue

            # Article overlap check (skip for thematic guides without article)
            if guide_art:
                arts = {a.strip() for a in (cols.get("article") or "").split(";") if a.strip()}
                if guide_art not in arts:
                    continue

            # Not a positive in this window
            appnos = {a.strip().lower() for a in (cols.get("appno") or "").split(";") if a.strip()}
            if appnos & pos:
                continue

            # Resolve canonical case_key
            resolved_key = ""
            for appno in appnos:
                if appno in appno_to_key:
                    resolved_key = appno_to_key[appno]
                    break
            if not resolved_key:
                resolved_key = f"apps:{next(iter(appnos), '')}"

            # Dedup: same case × guide × window
            dedup_key = (resolved_key, guide_id, f"{from_d}|{to_d}")
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            # Delayed positive check
            delayed = False
            pre_existing = False
            membership_ts = guide_membership.get((resolved_key, guide_id), "")
            if membership_ts:
                first_seen_date = timestamp_to_date(membership_ts)
                if first_seen_date > to_d:
                    delayed = True
                elif first_seen_date and first_seen_date <= from_d:
                    pre_existing = True

            # Right-censoring
            censored = to_d >= RIGHT_CENSOR_CUTOFF

            parts = []
            if delayed:
                parts.append("delayed_positive")
            if pre_existing:
                parts.append("pre_existing")
            if censored:
                parts.append("right_censored")
            audit_flag = "|".join(parts) if parts else "clean"

            hard_rows.append({
                "guide_id": guide_id,
                "guide_title": guide_title,
                "from_snapshot_date": from_d,
                "to_snapshot_date": to_d,
                "case_key": resolved_key,
                "case_name": (cols.get("docname") or "").replace("CASE OF ", ""),
                "application_numbers": "|".join(sorted(appnos)),
                "hudoc_itemid": cols.get("itemid", ""),
                "hudoc_importance_level": imp,
                "hudoc_doctype": cols.get("doctype", ""),
                "convention_articles": "|".join(
                    a.strip() for a in (cols.get("article") or "").split(";") if a.strip()
                ),
                "judgment_year": (cols.get("kpdate") or "")[:4],
                "label": "negative",
                "negative_type": "hard",
                "negative_reason": "published_in_window_not_added_to_guide_importance_1_3",
                "resolved_case_key": resolved_key,
                "delayed_positive": str(delayed).lower(),
                "right_censored": str(censored).lower(),
                "audit_flag": audit_flag,
            })

    HARD_OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HARD_OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(hard_rows[0].keys()))
        writer.writeheader()
        writer.writerows(hard_rows)

    n_clean = sum(1 for r in hard_rows if r["audit_flag"] == "clean")
    n_delayed = sum(1 for r in hard_rows if r["delayed_positive"] == "true")
    n_censored = sum(1 for r in hard_rows if r["right_censored"] == "true")
    n_pre = sum(1 for r in hard_rows if "pre_existing" in r["audit_flag"])

    report = {
        "total_hard_candidates": len(hard_rows),
        "clean": n_clean,
        "delayed_positives": n_delayed,
        "pre_existing": n_pre,
        "right_censored": n_censored,
    }
    HARD_REPORT_JSON.write_text(json.dumps(report, indent=2))

    # Combined clean negatives (easy + hard, both audit_flag == clean)
    easy_clean = [r for r in csv.DictReader(EASY_NEGATIVES_CSV.open())
                  if r["audit_flag"] == "clean"]
    for r in easy_clean:
        r.setdefault("negative_type", "easy")

    hard_clean = [r for r in hard_rows if r["audit_flag"] == "clean"]

    combined = easy_clean + hard_clean
    if combined:
        all_keys = list(dict.fromkeys(
            list(easy_clean[0].keys()) + list(hard_clean[0].keys())
        ))
        with COMBINED_OUTPUT_CSV.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(combined)

    print(f"Hard candidates total:   {len(hard_rows)}")
    print(f"  clean:                 {n_clean}")
    print(f"  delayed positives:     {n_delayed}")
    print(f"  right-censored:        {n_censored}")
    print(f"  pre-existing:          {n_pre}")
    print()
    print(f"Easy clean negatives:    {len(easy_clean)}")
    print(f"Hard clean negatives:    {len(hard_clean)}")
    print(f"Combined clean total:    {len(combined)}")
    print()
    print(f"Written: {HARD_OUTPUT_CSV}")
    print(f"Written: {HARD_REPORT_JSON}")
    print(f"Written: {COMBINED_OUTPUT_CSV}")


if __name__ == "__main__":
    main()
