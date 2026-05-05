from __future__ import annotations

import csv
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import fitz

from pipeline_support import manual_drop_overrides, matches_manual_drop


WAYBACK = Path("wayback")
OUTPUT_DIR = Path("outputs/citation_diff_cleanup")
GROUPED_OUTPUT = OUTPUT_DIR / "cleaned_diffs_grouped.json"
FLAT_OUTPUT = OUTPUT_DIR / "cleaned_citation_diffs.csv"
EXTRACTED_OUTPUT = OUTPUT_DIR / "cleaned_extracted_citations.json"
SECTION_HEADER = "List of cited cases"

UPDATED_RE = re.compile(
    r"(?:Updated|Last update):\s*(\d{1,2}\.\d{2}\.\d{4}|\d{1,2}\s+\w+\s+\d{4})",
    re.IGNORECASE,
)
APP_NO_RE = re.compile(r"\b\d+/\d+\b")
PAGE_RE = re.compile(r"^\d+/\d+$")
ALPHA_DIVIDER_RE = re.compile(r"^[—\-– ]+[A-Z][—\-– ]*$")
GUIDE_HEADER_RE = re.compile(r"^(Guide (?:on|to)|Practical guide on)\b", re.IGNORECASE)
COURT_HEADER_RE = re.compile(r"^European Court of Human Rights")
NOISE_SUBSTRINGS = (
    "The case-law cited in this Guide",
    "Unless otherwise indicated",
    "The abbreviation",
    "The hyperlinks",
    "The Court delivers its judgments",
    "HUDOC also contains",
    "language versions available",
    "Article 44 § 2",
    "Grand Chamber",
)
def _starts_with_capital(text: str) -> bool:
    """True if text starts with an uppercase Unicode letter or ASCII digit."""
    return bool(text) and (text[0].isupper() or text[0].isdigit())
HANGING_DASH_RE = re.compile(r"[—\-–]\s*$")
_OCR_SPLIT_RE = re.compile(r"([A-Z]) ([a-z]{3,})")
_OCR_COMMON_WORDS = frozenset({"and", "or", "the", "of", "in", "no", "nos", "dec", "others", "another", "against"})
MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)
MANUAL_VERSION_DATES = {
    "20230923180420__guide_terrorism_eng.pdf": "31 August 2023",
    "20251025084811__guide_terrorism_eng.pdf": "28 February 2025",
    "20230923150809__guide_art_2_protocol_4_eng.pdf": "28 February 2023",
    "20240217131620__guide_art_2_protocol_4_eng.pdf": "31 August 2023",
}


@dataclass(frozen=True)
class Snapshot:
    guide_id: str
    guide_title: str
    timestamp: str
    pdf_path: Path
    pdf_name: str
    wayback_url: str
    version_date: str


def normalize_display_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace(" ,", ",")
    )
    text = re.sub(r"\b((?:request no|nos?|no)\.?)\s+", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\d)-\s+(?=\d)", "-", text)
    text = re.sub(r"\s+/\s+", "/", text)
    # Normalize "1996 I" \u2192 "1996-I" so year-volume references are canonical
    text = re.sub(r"\b((?:19|20)\d{2})\s+([IVX]{1,5})\b", r"\1-\2", text)
    text = re.sub(r"\s+", " ", text)
    # Fix OCR mid-word spaces (e.g. "G eorgia" \u2192 "Georgia"), skipping common English words
    text = _OCR_SPLIT_RE.sub(
        lambda m: m.group(1) + m.group(2) if m.group(2) not in _OCR_COMMON_WORDS else m.group(0),
        text,
    )
    text = HANGING_DASH_RE.sub("", text)
    return text.strip(" *")


def normalize_for_matching(text: str) -> str:
    text = normalize_display_text(text).lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    text = text.replace("’", "’").replace("`", "’")
    text = re.sub(r"\bet\b", "and", text)
    text = re.sub(r"[\"’""’’()\[\]{}*]", "", text)
    text = re.sub(r"[^a-z0-9/., -]+", " ", text)
    text = re.sub(r"[.,;:]+", " ", text)
    # Strip year-volume suffixes so "1996-i" and "1996 i" both reduce to "1996"
    text = re.sub(r"\b((?:19|20)\d{2})-([ivx]{1,5})\b", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    # Strip "the" article after the "v" connector so "v. the United Kingdom" and
    # "v. United Kingdom" both normalize to "v united kingdom"
    text = re.sub(r"\bv the\b", "v", text)
    return text.strip()


def normalize_case_name(case_name: str) -> str:
    return normalize_for_matching(case_name)


def extract_version_date(pdf_path: Path) -> str:
    manual = MANUAL_VERSION_DATES.get(pdf_path.name)
    if manual:
        return manual
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return f"unknown ({pdf_path.name})"

    try:
        for page_index in range(min(5, doc.page_count)):
            text = doc.load_page(page_index).get_text("text")
            match = UPDATED_RE.search(text)
            if not match:
                continue
            version = match.group(1).strip()
            if "." in version:
                day, month, year = version.split(".")
                months = {
                    "01": "January",
                    "02": "February",
                    "03": "March",
                    "04": "April",
                    "05": "May",
                    "06": "June",
                    "07": "July",
                    "08": "August",
                    "09": "September",
                    "10": "October",
                    "11": "November",
                    "12": "December",
                }
                return f"{int(day)} {months[month]} {year}"
            return version
    finally:
        doc.close()
    return f"unknown ({pdf_path.name})"


def extract_actual_cited_cases_start_page(pdf_path: Path) -> int | None:
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return None
    try:
        matches = []
        for page_index in range(doc.page_count):
            text = doc.load_page(page_index).get_text("text")
            if SECTION_HEADER in text:
                matches.append(page_index + 1)
        return matches[-1] if matches else None
    finally:
        doc.close()


def load_range_with_pdftotext(pdf_path: Path, start_page: int, end_page: int) -> str | None:
    try:
        return subprocess.check_output(
            [
                "pdftotext",
                "-f",
                str(start_page),
                "-l",
                str(end_page),
                "-layout",
                str(pdf_path),
                "-",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None


def entry_looks_complete(entry: str) -> bool:
    return bool(
        re.search(rf"\b\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+\d{{4}}\*?\.?$", entry)
        or re.search(r"\bECHR\s+\d{4}(?:-[A-Z]+)?\b", entry)
        or "Series A no." in entry
        or "Reports of Judgments" in entry
        or "Decisions and Reports" in entry
        or re.search(r"\bDR\s+\d+\b", entry)
        or "Collection of decisions" in entry
    )


def looks_like_new_citation(raw_line: str, current_entry: str | None) -> bool:
    stripped = raw_line.strip()
    if not stripped:
        return False

    if stripped.startswith("Advisory opinion") or stripped.startswith("Case "):
        return True

    if _starts_with_capital(stripped) and " v. " in stripped:
        return True

    if _starts_with_capital(stripped) and " c. " in stripped:
        return True

    # Some citations wrap before "v.", leaving a left-aligned case name line.
    if (
        current_entry
        and entry_looks_complete(current_entry)
        and _starts_with_capital(stripped)
        and "," in stripped
        and not stripped.startswith(("The ", "Guide ", "European "))
    ):
        return True

    return False


def looks_like_case_name_fragment(raw_line: str, current_entry: str | None, next_line: str | None) -> bool:
    stripped = raw_line.strip()
    if not stripped or not current_entry or not entry_looks_complete(current_entry):
        return False
    if not _starts_with_capital(stripped):
        return False
    if stripped.startswith(("The ", "Guide ", "European ")):
        return False
    if "," in stripped:
        return False
    if next_line is None:
        return False

    next_stripped = next_line.strip()
    return next_stripped.startswith(("v. ", "c. "))


def extract_cited_cases(pdf_path: Path) -> list[str] | None:
    start_page = extract_actual_cited_cases_start_page(pdf_path)
    if start_page is None:
        return None

    doc = fitz.open(pdf_path)
    try:
        raw_text = load_range_with_pdftotext(pdf_path, start_page, doc.page_count)
    finally:
        doc.close()
    if raw_text is None:
        return None

    lines = raw_text.splitlines()
    line_index = 0
    while line_index < len(lines) and SECTION_HEADER not in lines[line_index]:
        line_index += 1
    while line_index < len(lines) and not ALPHA_DIVIDER_RE.match(lines[line_index].strip()):
        line_index += 1

    citations: list[str] = []
    current: str | None = None
    relevant_lines = lines[line_index:]
    for index, raw_line in enumerate(relevant_lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped == SECTION_HEADER:
            continue
        if ALPHA_DIVIDER_RE.match(stripped):
            continue
        if PAGE_RE.match(stripped):
            continue
        if GUIDE_HEADER_RE.match(stripped):
            continue
        if COURT_HEADER_RE.match(stripped):
            continue
        if stripped.startswith(("Updated:", "Last update:")):
            continue
        if any(noise in stripped for noise in NOISE_SUBSTRINGS):
            continue

        next_line = None
        for future_line in relevant_lines[index + 1 :]:
            if future_line.strip():
                next_line = future_line
                break

        if looks_like_new_citation(raw_line, current):
            if current:
                citations.append(normalize_display_text(current))
            current = stripped
        elif looks_like_case_name_fragment(raw_line, current, next_line):
            if current:
                citations.append(normalize_display_text(current))
            current = stripped
        elif current:
            current = f"{current} {stripped}"

    if current:
        citations.append(normalize_display_text(current))

    # Post-process: any citation that starts with "v. " or "c. " is a wrapped line fragment
    # that failed to join during extraction (e.g., a case name spanning 3+ PDF lines). Merge
    # it back onto the preceding citation.
    merged: list[str] = []
    for citation in citations:
        if (citation.startswith("v. ") or citation.startswith("c. ")) and merged:
            merged[-1] = normalize_display_text(f"{merged[-1]} {citation}")
        else:
            merged.append(citation)

    return merged


def load_snapshots() -> list[Snapshot]:
    snapshots: list[Snapshot] = []
    for metadata_path in sorted(WAYBACK.glob("*/snapshot_metadata.jsonl")):
        with metadata_path.open() as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        if not rows:
            continue

        guide_id = rows[0]["guide_id"]
        guide_title = rows[0]["title"]
        for row in rows:
            pdf_path = WAYBACK / guide_id / "snapshots" / Path(row["local_path"]).name
            if not pdf_path.exists():
                continue
            snapshots.append(
                Snapshot(
                    guide_id=guide_id,
                    guide_title=guide_title,
                    timestamp=row["timestamp"],
                    pdf_path=pdf_path,
                    pdf_name=pdf_path.name,
                    wayback_url=row["wayback_url"],
                    version_date=extract_version_date(pdf_path),
                )
            )
    return snapshots


def load_grouped_snapshots() -> dict[str, list[Snapshot]]:
    grouped: dict[str, list[Snapshot]] = {}
    for snapshot in load_snapshots():
        grouped.setdefault(snapshot.guide_id, []).append(snapshot)
    for guide_id in grouped:
        grouped[guide_id].sort(key=lambda item: item.timestamp)
    return grouped


def extract_case_name(citation: str) -> str:
    return citation.split(",", 1)[0].strip()


def extract_year(citation: str) -> str | None:
    years = re.findall(r"\b(19|20)\d{2}\b", citation)
    return years[-1] if years else None


def fuzzy_noapp_match(base_citation: str, current_citation: str) -> bool:
    base_name = normalize_case_name(extract_case_name(base_citation))
    current_name = normalize_case_name(extract_case_name(current_citation))
    if not base_name or not current_name:
        return False

    base_year = extract_year(base_citation)
    current_year = extract_year(current_citation)
    if base_year and current_year and base_year != current_year:
        return False

    if base_name == current_name:
        return True

    ratio = SequenceMatcher(None, base_name, current_name).ratio()
    return ratio >= 0.93


def build_app_key(citation: str) -> str | None:
    app_numbers = APP_NO_RE.findall(citation)
    if not app_numbers:
        return None
    deduped = []
    seen = set()
    for app_no in app_numbers:
        if app_no in seen:
            continue
        seen.add(app_no)
        deduped.append(app_no)
    return "|".join(deduped)


def diff_snapshot_pair(base_cases: list[str], current_cases: list[str]) -> tuple[list[str], list[str]]:
    base_by_app = {}
    base_noapp = []
    for citation in base_cases:
        key = build_app_key(citation)
        if key is not None:
            base_by_app[key] = citation
        else:
            base_noapp.append(citation)

    current_by_app = {}
    current_noapp = []
    for citation in current_cases:
        key = build_app_key(citation)
        if key is not None:
            current_by_app[key] = citation
        else:
            current_noapp.append(citation)

    # Primary app-key diff
    app_added = [current_by_app[key] for key in sorted(set(current_by_app) - set(base_by_app))]
    app_removed = [base_by_app[key] for key in sorted(set(base_by_app) - set(current_by_app))]

    # Noapp pool — these will be modified as cross-bucket matches are found
    unmatched_base = base_noapp[:]
    unmatched_current = current_noapp[:]
    matched_current_indexes = set()

    # Noapp ↔ noapp matching
    for base_index, base_citation in enumerate(base_noapp):
        match_index = None
        for current_index, current_citation in enumerate(current_noapp):
            if current_index in matched_current_indexes:
                continue
            if fuzzy_noapp_match(base_citation, current_citation):
                match_index = current_index
                break
        if match_index is None:
            continue
        matched_current_indexes.add(match_index)
        unmatched_base[base_index] = None
        unmatched_current[match_index] = None

    # Cross-bucket: a case with an app key in base may lack one in current (e.g., language
    # variant citations where one version omits the application number). Try to fuzzy-match
    # such "orphaned" app-key removals against the remaining noapp pool in current.
    final_removed_app: list[str] = []
    for citation in app_removed:
        matched_index = None
        for ci, current_citation in enumerate(unmatched_current):
            if current_citation is None:
                continue
            if fuzzy_noapp_match(citation, current_citation):
                matched_index = ci
                break
        if matched_index is not None:
            unmatched_current[matched_index] = None
        else:
            final_removed_app.append(citation)

    # Symmetric: a case new in current_by_app may be a re-styled version of a noapp base entry
    final_added_app: list[str] = []
    for citation in app_added:
        matched_index = None
        for bi, base_citation in enumerate(unmatched_base):
            if base_citation is None:
                continue
            if fuzzy_noapp_match(base_citation, citation):
                matched_index = bi
                break
        if matched_index is not None:
            unmatched_base[matched_index] = None
        else:
            final_added_app.append(citation)

    added = final_added_app + [c for c in unmatched_current if c is not None]
    removed = final_removed_app + [c for c in unmatched_base if c is not None]

    return sorted(added), sorted(removed)


def build_hf_url(guide_id: str, pdf_name: str) -> str:
    return (
        "https://huggingface.co/datasets/lexgenie/echr-guide-citation-diffs/"
        f"resolve/main/pdfs/{guide_id}/{pdf_name}"
    )


def write_outputs(grouped_diffs: list[dict], extracted_citations: dict[str, list[str]]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    drop_overrides = manual_drop_overrides()
    filtered_grouped_diffs: list[dict] = []
    dropped_rows: list[dict[str, str]] = []
    for diff in grouped_diffs:
        kept = dict(diff)
        for change in ("added", "removed"):
            kept_citations: list[str] = []
            for citation in diff[change]:
                row = {
                    "guide_id": diff["guide_id"],
                    "from_version": diff["from_version"],
                    "to_version": diff["to_version"],
                    "change": change,
                    "citation": citation,
                }
                matched_override = matches_manual_drop(row, drop_overrides)
                if matched_override is not None:
                    dropped_rows.append(
                        {
                            "guide_id": diff["guide_id"],
                            "from_version": diff["from_version"],
                            "to_version": diff["to_version"],
                            "change": change,
                            "citation": citation,
                            "notes": matched_override.notes,
                        }
                    )
                    continue
                kept_citations.append(citation)
            kept[change] = kept_citations
        if kept["added"] or kept["removed"]:
            filtered_grouped_diffs.append(kept)

    GROUPED_OUTPUT.write_text(json.dumps(filtered_grouped_diffs, indent=2, ensure_ascii=False))
    EXTRACTED_OUTPUT.write_text(json.dumps(extracted_citations, indent=2, ensure_ascii=False))

    rows = []
    for diff in filtered_grouped_diffs:
        for change in ("added", "removed"):
            for citation in diff[change]:
                rows.append(
                    {
                        "guide_id": diff["guide_id"],
                        "guide_title": diff["guide_title"],
                        "from_version": diff["from_version"],
                        "to_version": diff["to_version"],
                        "change": change,
                        "citation": citation,
                        "from_snapshot": diff["from_snapshot"],
                        "to_snapshot": diff["to_snapshot"],
                        "from_wayback_url": diff["from_wayback_url"],
                        "to_wayback_url": diff["to_wayback_url"],
                        "from_hf_url": diff["from_hf_url"],
                        "to_hf_url": diff["to_hf_url"],
                    }
                )

    with FLAT_OUTPUT.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "guide_id",
                "guide_title",
                "from_version",
                "to_version",
                "change",
                "citation",
                "from_snapshot",
                "to_snapshot",
                "from_wayback_url",
                "to_wayback_url",
                "from_hf_url",
                "to_hf_url",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    if dropped_rows:
        dropped_report = OUTPUT_DIR / "manual_drops_applied.json"
        dropped_report.write_text(json.dumps(dropped_rows, indent=2, ensure_ascii=False))


def main() -> None:
    if hasattr(fitz, "TOOLS"):
        try:
            fitz.TOOLS.mupdf_display_errors(False)
            fitz.TOOLS.mupdf_display_warnings(False)
        except Exception:
            pass

    grouped_snapshots = load_grouped_snapshots()
    grouped_diffs: list[dict] = []
    extracted_citations: dict[str, list[str]] = {}
    skipped_snapshots: list[str] = []

    for guide_id in sorted(grouped_snapshots):
        snapshots = grouped_snapshots[guide_id]
        if len(snapshots) < 2:
            continue

        baseline = None
        baseline_cases = None
        for candidate in snapshots:
            candidate_cases = extract_cited_cases(candidate.pdf_path)
            if not candidate_cases:
                skipped_snapshots.append(f"{guide_id}/{candidate.pdf_name}")
                continue
            baseline = candidate
            baseline_cases = candidate_cases
            extracted_citations[f"{guide_id}/{candidate.pdf_name}"] = candidate_cases
            break
        if baseline is None or baseline_cases is None:
            continue

        baseline_index = snapshots.index(baseline)
        for current in snapshots[baseline_index + 1 :]:
            current_cases = extract_cited_cases(current.pdf_path)
            if not current_cases:
                skipped_snapshots.append(f"{guide_id}/{current.pdf_name}")
                continue
            extracted_citations[f"{guide_id}/{current.pdf_name}"] = current_cases
            added, removed = diff_snapshot_pair(baseline_cases, current_cases)
            if not added and not removed:
                continue

            grouped_diffs.append(
                {
                    "guide_id": guide_id,
                    "guide_title": baseline.guide_title,
                    "from_version": baseline.version_date,
                    "to_version": current.version_date,
                    "from_snapshot": baseline.pdf_name,
                    "to_snapshot": current.pdf_name,
                    "from_wayback_url": baseline.wayback_url,
                    "to_wayback_url": current.wayback_url,
                    "from_hf_url": build_hf_url(guide_id, baseline.pdf_name),
                    "to_hf_url": build_hf_url(guide_id, current.pdf_name),
                    "added": added,
                    "removed": removed,
                }
            )

            baseline = current
            baseline_cases = current_cases

    write_outputs(grouped_diffs, extracted_citations)
    flat_rows = sum(len(diff["added"]) + len(diff["removed"]) for diff in grouped_diffs)
    print(f"Wrote {len(grouped_diffs)} grouped diffs to {GROUPED_OUTPUT}")
    print(f"Wrote {flat_rows} flat rows to {FLAT_OUTPUT}")
    print(f"Wrote extracted citations for {len(extracted_citations)} snapshots to {EXTRACTED_OUTPUT}")
    print(f"Skipped {len(skipped_snapshots)} unreadable snapshots")


if __name__ == "__main__":
    main()
