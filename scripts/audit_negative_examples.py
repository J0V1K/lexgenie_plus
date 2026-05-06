from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path


NEGATIVES_CSV = Path("outputs/negatives/negative_examples.csv")
CASE_GUIDES_CSV = Path("outputs/case_catalog/case_guides.csv")
CASES_CATALOG_CSV = Path("outputs/case_catalog/cases_catalog.csv")
OUTPUT_CSV = Path("outputs/negatives/negative_examples_audited.csv")
REPORT_JSON = Path("outputs/negatives/negative_audit_report.json")

# Transitions ending on or after this date are flagged as right-censored.
RIGHT_CENSOR_CUTOFF = "2025-01-01"


def build_appno_to_case_key(catalog_path: Path) -> dict[str, str]:
    """Map every application number to its canonical case_key."""
    lookup: dict[str, str] = {}
    for row in csv.DictReader(catalog_path.open()):
        case_key = row["case_key"]
        for appno in row["application_numbers"].split("|"):
            appno = appno.strip()
            if appno:
                lookup[appno] = case_key
    return lookup


def build_guide_membership(
    case_guides_path: Path,
) -> dict[tuple[str, str], str]:
    """Map (case_key, guide_id) -> first_seen_snapshot_timestamp (14-char string)."""
    lookup: dict[tuple[str, str], str] = {}
    for row in csv.DictReader(case_guides_path.open()):
        key = (row["case_key"], row["guide_id"])
        lookup[key] = row["first_seen_snapshot_timestamp"]
    return lookup


def timestamp_to_date(ts: str) -> str:
    """Convert 14-char snapshot timestamp (YYYYMMDDhhmmss) to YYYY-MM-DD."""
    if len(ts) >= 8:
        return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    return ""


def audit_flag(delayed: bool, pre_existing: bool, censored: bool) -> str:
    parts = []
    if delayed:
        parts.append("delayed_positive")
    if pre_existing:
        parts.append("pre_existing")
    if censored:
        parts.append("right_censored")
    return "|".join(parts) if parts else "clean"


def main() -> None:
    appno_to_key = build_appno_to_case_key(CASES_CATALOG_CSV)
    guide_membership = build_guide_membership(CASE_GUIDES_CSV)

    negatives = list(csv.DictReader(NEGATIVES_CSV.open()))

    audited_rows: list[dict] = []
    counts: dict[str, int] = defaultdict(int)
    delayed_by_guide: dict[str, int] = defaultdict(int)
    censored_by_guide: dict[str, int] = defaultdict(int)

    for neg in negatives:
        guide_id = neg["guide_id"]
        from_date = neg["from_snapshot_date"]
        to_date = neg["to_snapshot_date"]
        raw_appnos = [a.strip() for a in neg["application_numbers"].split("|") if a.strip()]

        # Resolve to canonical case_key via any of the application numbers.
        resolved_key = ""
        for appno in raw_appnos:
            if appno in appno_to_key:
                resolved_key = appno_to_key[appno]
                break

        # Fall back to the case_key already in the negative row.
        if not resolved_key:
            resolved_key = neg.get("case_key", "")

        delayed = False
        pre_existing = False

        membership_ts = guide_membership.get((resolved_key, guide_id), "")
        if membership_ts:
            first_seen_date = timestamp_to_date(membership_ts)
            if first_seen_date > to_date:
                delayed = True
            elif first_seen_date and first_seen_date <= from_date:
                pre_existing = True

        censored = to_date >= RIGHT_CENSOR_CUTOFF

        flag = audit_flag(delayed, pre_existing, censored)
        counts[flag] += 1
        if delayed:
            delayed_by_guide[neg["guide_title"]] += 1
        if censored and not delayed:
            censored_by_guide[neg["guide_title"]] += 1

        audited_rows.append(
            {
                **neg,
                "resolved_case_key": resolved_key,
                "delayed_positive": str(delayed).lower(),
                "right_censored": str(censored).lower(),
                "audit_flag": flag,
            }
        )

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(audited_rows[0].keys()))
        writer.writeheader()
        writer.writerows(audited_rows)

    # Aggregate summary counts.
    n_delayed = sum(1 for r in audited_rows if r["delayed_positive"] == "true")
    n_pre_existing = sum(1 for r in audited_rows if "pre_existing" in r["audit_flag"])
    n_censored = sum(1 for r in audited_rows if r["right_censored"] == "true")
    n_clean = sum(1 for r in audited_rows if r["audit_flag"] == "clean")

    report = {
        "total_negatives": len(audited_rows),
        "delayed_positives": n_delayed,
        "pre_existing": n_pre_existing,
        "right_censored_any": n_censored,
        "clean": n_clean,
        "flag_distribution": dict(counts),
        "delayed_positive_by_guide": dict(sorted(delayed_by_guide.items(), key=lambda x: -x[1])),
        "right_censored_only_by_guide": dict(sorted(censored_by_guide.items(), key=lambda x: -x[1])),
    }

    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"Total negatives:    {len(audited_rows)}")
    print(f"Delayed positives:  {n_delayed}")
    print(f"Pre-existing:       {n_pre_existing}")
    print(f"Right-censored:     {n_censored}")
    print(f"Clean:              {n_clean}")
    print(f"Written: {OUTPUT_CSV}")
    print(f"Written: {REPORT_JSON}")


if __name__ == "__main__":
    main()
