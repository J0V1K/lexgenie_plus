from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from rebuild_citation_diffs_clean import APP_NO_RE, normalize_case_name, normalize_display_text


WAYBACK = Path("wayback")
EXTRACTED_CITATIONS = Path("outputs/citation_diff_cleanup/cleaned_extracted_citations.json")
OUTPUT_DIR = Path("outputs/case_catalog")
AUDIT_DIR = OUTPUT_DIR / "audit"
CASE_CATALOG_CSV = AUDIT_DIR / "cases_catalog_raw.csv"
CASE_CATALOG_JSON = AUDIT_DIR / "cases_catalog_raw.json"
CASE_APPEARANCES_CSV = OUTPUT_DIR / "case_appearances.csv"
CASE_GUIDES_CSV = OUTPUT_DIR / "case_guides.csv"

MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)
FULL_DATE_RE = re.compile(rf"\b(\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+\d{{4}})\b")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
GUIDE_FILE_RE = re.compile(r"^(?P<timestamp>\d{14})__(?P<slug>.+)\.pdf$")
REQUEST_NO_RE = re.compile(r"\bP16-\d{4}-\d{3}\b", re.IGNORECASE)
PROCEDURAL_MARKER_RE = re.compile(
    r",\s*(?:commission decision|commission report|judgment of|decision of)\b.*$",
    re.IGNORECASE,
)
TRAILING_CASE_ID_RE = re.compile(
    r",?\*?\s*nos?\.?\s+\d+/\d+(?:\s+and\s+(?:\d+\s+others?|two others))?$",
    re.IGNORECASE,
)
TRAILING_SINGLE_CASE_ID_RE = re.compile(r"\s+no\.?\s+\d+/\d+$", re.IGNORECASE)


def load_guide_titles() -> dict[str, str]:
    guide_titles: dict[str, str] = {}
    for metadata_path in WAYBACK.glob("*/snapshot_metadata.jsonl"):
        with metadata_path.open() as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                guide_titles[row["guide_id"]] = row["title"]
    return guide_titles


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_earliest_marker(citation: str, markers: list[str]) -> int | None:
    lower = citation.lower()
    positions = [lower.find(marker.lower()) for marker in markers if marker.lower() in lower]
    valid_positions = [pos for pos in positions if pos >= 0]
    return min(valid_positions) if valid_positions else None


def extract_case_name(citation: str) -> str:
    citation = normalize_display_text(citation)

    leading_markers = [
        ", no. ",
        ", nos. ",
        ", request no. ",
        ", commission decision",
        ", commission report",
        ", judgment of",
        ", decision of",
    ]
    cutoff = find_earliest_marker(citation, leading_markers)
    if cutoff is not None:
        return normalize_whitespace(citation[:cutoff])

    date_match = FULL_DATE_RE.search(citation)
    if date_match:
        prefix = citation[: date_match.start()].rstrip(" ,")
        if prefix:
            return normalize_whitespace(prefix)

    trailing_markers = [
        ", ECHR ",
        ", Series A no. ",
        ", Reports of Judgments",
        ", Decisions and Reports",
        ", Collection of decisions",
        ", DR ",
    ]
    positions = [citation.find(marker) for marker in trailing_markers if marker in citation]
    if positions:
        cutoff = min(pos for pos in positions if pos >= 0)
        return normalize_whitespace(citation[:cutoff])

    year_match = YEAR_RE.search(citation)
    if year_match and year_match.start() > 0:
        prefix = citation[: year_match.start()].rstrip(" ,")
        if prefix:
            return normalize_whitespace(prefix)

    procedural_stripped = PROCEDURAL_MARKER_RE.sub("", citation).rstrip(" ,")
    case_name = procedural_stripped or citation
    case_name = TRAILING_CASE_ID_RE.sub("", case_name).rstrip(" ,")
    case_name = TRAILING_SINGLE_CASE_ID_RE.sub("", case_name).rstrip(" ,")
    return case_name or citation


def looks_like_case_citation(case_name: str) -> bool:
    lower = case_name.lower()
    return (
        " v. " in case_name
        or " c. " in case_name
        or lower.startswith("advisory opinion")
        or lower.startswith("case ")
    )


def extract_application_numbers(citation: str) -> list[str]:
    seen: set[str] = set()
    app_numbers: list[str] = []
    for request_no in REQUEST_NO_RE.findall(citation):
        normalized = request_no.upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        app_numbers.append(normalized)

    for match in APP_NO_RE.finditer(citation):
        app_no = match.group(0)
        if (match.start() > 0 and citation[match.start() - 1] == "/") or (
            match.end() < len(citation) and citation[match.end()] == "/"
        ):
            continue
        if app_no in seen:
            continue
        seen.add(app_no)
        app_numbers.append(app_no)
    return app_numbers


def extract_date_fields(citation: str) -> tuple[str | None, str | None]:
    full_date_match = FULL_DATE_RE.search(citation)
    if full_date_match:
        full_date = full_date_match.group(1)
        year = full_date[-4:]
        return full_date, year

    year_match = YEAR_RE.search(citation)
    if year_match:
        year = year_match.group(1)
        return None, year

    return None, None


def make_case_key(case_name: str, app_numbers: list[str], judgment_year: str | None) -> str:
    if app_numbers:
        return "apps:" + "|".join(app_numbers)
    suffix = judgment_year or "unknown"
    return f"name:{normalize_case_name(case_name)}::{suffix}"


def serialize_list(values: list[str]) -> str:
    return "|".join(values)


def main() -> None:
    extracted = json.loads(EXTRACTED_CITATIONS.read_text())
    guide_titles = load_guide_titles()

    case_records: dict[str, dict] = {}
    case_appearance_rows: list[dict] = []
    case_guides: dict[tuple[str, str], dict] = {}

    for snapshot_key, citations in extracted.items():
        guide_id, pdf_name = snapshot_key.split("/", 1)
        guide_title = guide_titles.get(guide_id, "")

        guide_file_match = GUIDE_FILE_RE.match(pdf_name)
        snapshot_timestamp = guide_file_match.group("timestamp") if guide_file_match else None
        snapshot_slug = guide_file_match.group("slug") if guide_file_match else pdf_name.removesuffix(".pdf")

        for citation in citations:
            citation = normalize_display_text(citation)
            case_name = extract_case_name(citation)
            if not looks_like_case_citation(case_name):
                continue
            app_numbers = extract_application_numbers(citation)
            judgment_date_raw, judgment_year = extract_date_fields(citation)
            case_key = make_case_key(case_name, app_numbers, judgment_year)

            if case_key not in case_records:
                case_records[case_key] = {
                    "case_key": case_key,
                    "case_id": serialize_list(app_numbers) if app_numbers else "",
                    "case_name": case_name,
                    "application_numbers": app_numbers[:],
                    "application_numbers_count": len(app_numbers),
                    "primary_application_number": app_numbers[0] if app_numbers else "",
                    "judgment_date_raw": judgment_date_raw or "",
                    "judgment_year": judgment_year or "",
                    "citation_example": citation,
                    "all_citation_variants": {citation},
                    "hudoc_itemid": "",
                    "hudoc_importance_level": "",
                    "hudoc_importance_bucket": "",
                    "is_key_case": "",
                    "convention_articles": [],
                    "keywords": [],
                    "respondent_states": [],
                    "summary_text": "",
                    "snapshots": set(),
                    "guide_ids": set(),
                    "guide_titles": set(),
                    "first_seen_snapshot_timestamp": snapshot_timestamp or "",
                    "last_seen_snapshot_timestamp": snapshot_timestamp or "",
                    "first_seen_snapshot_pdf": pdf_name,
                    "last_seen_snapshot_pdf": pdf_name,
                }

            record = case_records[case_key]
            record["all_citation_variants"].add(citation)
            record["snapshots"].add(snapshot_key)
            record["guide_ids"].add(guide_id)
            if guide_title:
                record["guide_titles"].add(guide_title)

            if snapshot_timestamp and (
                not record["first_seen_snapshot_timestamp"]
                or snapshot_timestamp < record["first_seen_snapshot_timestamp"]
            ):
                record["first_seen_snapshot_timestamp"] = snapshot_timestamp
                record["first_seen_snapshot_pdf"] = pdf_name
            if snapshot_timestamp and (
                not record["last_seen_snapshot_timestamp"]
                or snapshot_timestamp > record["last_seen_snapshot_timestamp"]
            ):
                record["last_seen_snapshot_timestamp"] = snapshot_timestamp
                record["last_seen_snapshot_pdf"] = pdf_name

            case_appearance_rows.append(
                {
                    "case_key": case_key,
                    "case_id": serialize_list(app_numbers) if app_numbers else "",
                    "case_name": case_name,
                    "guide_id": guide_id,
                    "guide_title": guide_title,
                    "snapshot_key": snapshot_key,
                    "snapshot_timestamp": snapshot_timestamp or "",
                    "snapshot_pdf": pdf_name,
                    "snapshot_slug": snapshot_slug,
                    "citation_text": citation,
                    "judgment_date_raw": judgment_date_raw or "",
                    "judgment_year": judgment_year or "",
                }
            )

            case_guide_key = (case_key, guide_id)
            if case_guide_key not in case_guides:
                case_guides[case_guide_key] = {
                    "case_key": case_key,
                    "case_id": serialize_list(app_numbers) if app_numbers else "",
                    "case_name": case_name,
                    "guide_id": guide_id,
                    "guide_title": guide_title,
                    "snapshots": set(),
                    "first_seen_snapshot_timestamp": snapshot_timestamp or "",
                    "last_seen_snapshot_timestamp": snapshot_timestamp or "",
                    "citation_variants": set(),
                }
            guide_row = case_guides[case_guide_key]
            guide_row["snapshots"].add(snapshot_key)
            guide_row["citation_variants"].add(citation)
            if snapshot_timestamp and (
                not guide_row["first_seen_snapshot_timestamp"]
                or snapshot_timestamp < guide_row["first_seen_snapshot_timestamp"]
            ):
                guide_row["first_seen_snapshot_timestamp"] = snapshot_timestamp
            if snapshot_timestamp and (
                not guide_row["last_seen_snapshot_timestamp"]
                or snapshot_timestamp > guide_row["last_seen_snapshot_timestamp"]
            ):
                guide_row["last_seen_snapshot_timestamp"] = snapshot_timestamp

    case_catalog_rows = []
    for record in case_records.values():
        case_catalog_rows.append(
            {
                "case_key": record["case_key"],
                "case_id": record["case_id"],
                "case_name": record["case_name"],
                "application_numbers": serialize_list(record["application_numbers"]),
                "application_numbers_count": record["application_numbers_count"],
                "primary_application_number": record["primary_application_number"],
                "judgment_date_raw": record["judgment_date_raw"],
                "judgment_year": record["judgment_year"],
                "hudoc_itemid": record["hudoc_itemid"],
                "hudoc_importance_level": record["hudoc_importance_level"],
                "hudoc_importance_bucket": record["hudoc_importance_bucket"],
                "is_key_case": record["is_key_case"],
                "convention_articles": serialize_list(record["convention_articles"]),
                "keywords": serialize_list(record["keywords"]),
                "respondent_states": serialize_list(record["respondent_states"]),
                "summary_text": record["summary_text"],
                "citation_example": record["citation_example"],
                "all_citation_variants_count": len(record["all_citation_variants"]),
                "snapshots_count": len(record["snapshots"]),
                "guides_count": len(record["guide_ids"]),
                "guide_ids": serialize_list(sorted(record["guide_ids"])),
                "guide_titles": serialize_list(sorted(record["guide_titles"])),
                "first_seen_snapshot_timestamp": record["first_seen_snapshot_timestamp"],
                "last_seen_snapshot_timestamp": record["last_seen_snapshot_timestamp"],
                "first_seen_snapshot_pdf": record["first_seen_snapshot_pdf"],
                "last_seen_snapshot_pdf": record["last_seen_snapshot_pdf"],
            }
        )

    case_guide_rows = []
    for row in case_guides.values():
        case_guide_rows.append(
            {
                "case_key": row["case_key"],
                "case_id": row["case_id"],
                "case_name": row["case_name"],
                "guide_id": row["guide_id"],
                "guide_title": row["guide_title"],
                "snapshots_count": len(row["snapshots"]),
                "first_seen_snapshot_timestamp": row["first_seen_snapshot_timestamp"],
                "last_seen_snapshot_timestamp": row["last_seen_snapshot_timestamp"],
                "citation_variants_count": len(row["citation_variants"]),
            }
        )

    case_catalog_rows.sort(key=lambda row: (normalize_case_name(row["case_name"]), row["case_key"]))
    case_appearance_rows.sort(
        key=lambda row: (row["snapshot_timestamp"], row["guide_id"], row["case_name"].lower())
    )
    case_guide_rows.sort(key=lambda row: (row["guide_id"], row["case_name"].lower()))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    with CASE_CATALOG_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(case_catalog_rows[0].keys()))
        writer.writeheader()
        writer.writerows(case_catalog_rows)

    CASE_CATALOG_JSON.write_text(json.dumps(case_catalog_rows, indent=2, ensure_ascii=False))

    with CASE_APPEARANCES_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(case_appearance_rows[0].keys()))
        writer.writeheader()
        writer.writerows(case_appearance_rows)

    with CASE_GUIDES_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(case_guide_rows[0].keys()))
        writer.writeheader()
        writer.writerows(case_guide_rows)

    print(f"Wrote {len(case_catalog_rows)} cases to {CASE_CATALOG_CSV}")
    print(f"Wrote {len(case_appearance_rows)} case appearances to {CASE_APPEARANCES_CSV}")
    print(f"Wrote {len(case_guide_rows)} case-guide rows to {CASE_GUIDES_CSV}")
    print(f"Wrote JSON catalog to {CASE_CATALOG_JSON}")


if __name__ == "__main__":
    main()
