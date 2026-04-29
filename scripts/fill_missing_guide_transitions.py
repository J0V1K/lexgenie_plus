from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from hashlib import sha1
from pathlib import Path
from typing import Any

import fitz

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from build_case_catalog_from_guides import extract_application_numbers, extract_case_name
from rebuild_citation_diffs_clean import normalize_case_name, normalize_display_text


GROUPED_DIFFS_PATH = Path("outputs/citation_diff_cleanup/cleaned_diffs_grouped.json")
OUTPUT_DIFF_DIR = Path("anas-diff-dataset")
WAYBACK_DIR = Path("wayback")

DIFF_FILENAME_RE = re.compile(r"^diff_(\d{4}-\d{2}-\d{2})__(\d{4}-\d{2}-\d{2})\.json$")
PARA_START_RE = re.compile(r"^(\d+)\.\s+(.*)$", re.DOTALL)
NUMBERED_TITLE_RE = re.compile(r"^([IVXLCDM]+|[A-Z]|\d+|[a-z])\.\s+(.*)$")
HEADER_RE = re.compile(r"^(Guide (?:on|to)|Practical guide on)\b", re.IGNORECASE)
PAGE_COUNTER_RE = re.compile(r"^\d+/\d+$")


@dataclass(frozen=True)
class SectionAnchor:
    page_index: int
    y: float
    level: int
    path: str
    title: str
    raw_title: str


@dataclass(frozen=True)
class Paragraph:
    order: int
    para_num: int
    section_path: str
    section_title: str
    section_level: int
    text: str
    norm_text: str
    char_hash: str


def snapshot_to_date(snapshot_name: str) -> str:
    timestamp = snapshot_name.split("__", 1)[0]
    return f"{timestamp[:4]}-{timestamp[4:6]}-{timestamp[6:8]}"


def parse_iso_date(value: str) -> date:
    year, month, day = value.split("-")
    return date(int(year), int(month), int(day))


def split_section_title(raw_title: str, synthetic_counter: int) -> tuple[str, str]:
    raw_title = normalize_display_text(raw_title)
    match = NUMBERED_TITLE_RE.match(raw_title)
    if match:
        return match.group(1), match.group(2).strip()
    return f"§{synthetic_counter}", raw_title


def normalize_text_for_alignment(text: str) -> str:
    text = normalize_display_text(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def paragraph_hash(text: str) -> str:
    return sha1(normalize_text_for_alignment(text).encode("utf-8")).hexdigest()[:16]


def block_text(block: tuple[Any, ...]) -> str:
    return normalize_display_text(str(block[4]).replace("\n", " "))


def is_header_or_footer(text: str, y0: float, page_height: float) -> bool:
    if not text:
        return True
    if y0 < 45 and HEADER_RE.match(text):
        return True
    if y0 > page_height - 55 and ("European Court of Human Rights" in text or "Last update:" in text):
        return True
    if PAGE_COUNTER_RE.fullmatch(text):
        return True
    return False


def is_noise_block(text: str) -> bool:
    if not text:
        return True
    if text == "Table of contents":
        return True
    if text.startswith("European Court of Human Rights"):
        return True
    if "Last update:" in text:
        return True
    if text == "HUDOC keywords":
        return True
    if re.search(r"\.{5,}", text):
        return True
    return False


def build_section_anchors(doc: fitz.Document) -> list[SectionAnchor]:
    toc = doc.get_toc(simple=False)
    anchors: list[SectionAnchor] = []
    path_parts: dict[int, str] = {}
    synthetic_counter = 0

    for entry in toc:
        if len(entry) < 3:
            continue
        level = int(entry[0])
        raw_title = normalize_display_text(str(entry[1]))
        page_number = int(entry[2])
        meta = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}
        point = meta.get("to")
        y = float(getattr(point, "y", 0.0)) if point is not None else 0.0
        synthetic_counter += 1
        component, title = split_section_title(raw_title, synthetic_counter)
        path_parts[level] = component
        for deeper in list(path_parts):
            if deeper > level:
                del path_parts[deeper]
        path = ".".join(path_parts[index] for index in sorted(path_parts) if index <= level)
        anchors.append(
            SectionAnchor(
                page_index=max(page_number - 1, 0),
                y=y,
                level=level,
                path=path,
                title=title,
                raw_title=raw_title,
            )
        )

    anchors.sort(key=lambda item: (item.page_index, item.y, item.level))
    return anchors


def find_heading_anchor(
    *,
    page_index: int,
    y0: float,
    text: str,
    anchors: list[SectionAnchor],
) -> SectionAnchor | None:
    text_norm = normalize_case_name(text)
    if not text_norm:
        return None

    for anchor in anchors:
        if anchor.page_index != page_index:
            continue
        if abs(anchor.y - y0) > 42:
            continue
        anchor_title_norm = normalize_case_name(anchor.title)
        anchor_raw_norm = normalize_case_name(anchor.raw_title)
        if text_norm in anchor_title_norm or anchor_title_norm in text_norm:
            return anchor
        if text_norm in anchor_raw_norm or anchor_raw_norm in text_norm:
            return anchor
    return None


def extract_paragraphs(pdf_path: Path) -> tuple[list[Paragraph], dict[str, tuple[str, int]]]:
    doc = fitz.open(pdf_path)
    try:
        anchors = build_section_anchors(doc)
        sections = {anchor.path: (anchor.title, anchor.level) for anchor in anchors}
        current_section = anchors[0] if anchors else SectionAnchor(0, 0.0, 1, "§0", "Front matter", "Front matter")
        anchor_index = 0
        current_paragraph: dict[str, Any] | None = None
        output: list[Paragraph] = []
        order = 0

        def finalize_current() -> None:
            nonlocal current_paragraph, order
            if not current_paragraph:
                return
            text = normalize_text_for_alignment(current_paragraph["text"])
            if not text:
                current_paragraph = None
                return
            output.append(
                Paragraph(
                    order=order,
                    para_num=current_paragraph["para_num"],
                    section_path=current_paragraph["section_path"],
                    section_title=current_paragraph["section_title"],
                    section_level=current_paragraph["section_level"],
                    text=text,
                    norm_text=normalize_case_name(text),
                    char_hash=paragraph_hash(text),
                )
            )
            order += 1
            current_paragraph = None

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_height = float(page.rect.height)
            blocks = sorted(page.get_text("blocks"), key=lambda item: (item[1], item[0]))

            for block in blocks:
                y0 = float(block[1])
                text = block_text(block)
                if is_header_or_footer(text, y0, page_height) or is_noise_block(text):
                    continue

                while anchor_index < len(anchors):
                    anchor = anchors[anchor_index]
                    if (anchor.page_index, anchor.y) <= (page_index, y0 + 4):
                        current_section = anchor
                        anchor_index += 1
                        continue
                    break

                heading_anchor = find_heading_anchor(
                    page_index=page_index,
                    y0=y0,
                    text=text,
                    anchors=anchors,
                )
                if heading_anchor is not None:
                    finalize_current()
                    current_section = heading_anchor
                    continue

                match = PARA_START_RE.match(text)
                if match:
                    finalize_current()
                    current_paragraph = {
                        "para_num": int(match.group(1)),
                        "section_path": current_section.path,
                        "section_title": current_section.title,
                        "section_level": current_section.level,
                        "text": match.group(2).strip(),
                    }
                    continue

                if current_paragraph is not None:
                    current_paragraph["text"] = f"{current_paragraph['text']} {text}".strip()

        finalize_current()
        return output, sections
    finally:
        doc.close()


def sequence_similarity(text_a: str, text_b: str) -> float:
    return SequenceMatcher(None, text_a, text_b).ratio()


def unique_text_matches(paragraphs: list[Paragraph]) -> dict[str, int]:
    counts: dict[str, int] = {}
    positions: dict[str, int] = {}
    for index, paragraph in enumerate(paragraphs):
        counts[paragraph.norm_text] = counts.get(paragraph.norm_text, 0) + 1
        positions[paragraph.norm_text] = index
    return {text: positions[text] for text, count in counts.items() if count == 1 and text}


def align_paragraphs(paragraphs_a: list[Paragraph], paragraphs_b: list[Paragraph]) -> dict[int, int]:
    matches: dict[int, int] = {}
    matched_b: set[int] = set()

    unique_a = unique_text_matches(paragraphs_a)
    unique_b = unique_text_matches(paragraphs_b)
    for text, index_a in unique_a.items():
        index_b = unique_b.get(text)
        if index_b is None:
            continue
        matches[index_a] = index_b
        matched_b.add(index_b)

    def try_match(predicate, threshold: float) -> None:
        for index_a, paragraph_a in enumerate(paragraphs_a):
            if index_a in matches:
                continue
            best_index_b: int | None = None
            best_score = threshold
            for index_b, paragraph_b in enumerate(paragraphs_b):
                if index_b in matched_b or not predicate(paragraph_a, paragraph_b):
                    continue
                score = sequence_similarity(paragraph_a.norm_text, paragraph_b.norm_text)
                if score > best_score:
                    best_score = score
                    best_index_b = index_b
            if best_index_b is None:
                continue
            matches[index_a] = best_index_b
            matched_b.add(best_index_b)

    try_match(
        lambda paragraph_a, paragraph_b: (
            paragraph_a.para_num == paragraph_b.para_num
            and paragraph_a.section_path == paragraph_b.section_path
        ),
        0.82,
    )
    try_match(lambda paragraph_a, paragraph_b: paragraph_a.para_num == paragraph_b.para_num, 0.9)
    try_match(
        lambda paragraph_a, paragraph_b: (
            paragraph_a.section_path == paragraph_b.section_path
            and abs(paragraph_a.para_num - paragraph_b.para_num) <= 2
        ),
        0.92,
    )

    return matches


def build_citation_markers(citations: list[str]) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for citation in citations:
        case_name = extract_case_name(citation)
        markers.append(
            {
                "citation": citation,
                "case_name_norm": normalize_case_name(case_name),
                "application_numbers": extract_application_numbers(citation),
            }
        )
    return markers


def citation_present(text: str | None, marker: dict[str, Any]) -> bool:
    if not text:
        return False
    text_norm = normalize_case_name(text)
    if marker["case_name_norm"] and marker["case_name_norm"] in text_norm:
        return True
    return any(app_no in text for app_no in marker["application_numbers"])


def paragraph_citation_delta(
    *,
    text_a: str | None,
    text_b: str | None,
    added_markers: list[dict[str, Any]],
    removed_markers: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    citations_added = [
        marker["citation"]
        for marker in added_markers
        if citation_present(text_b, marker) and not citation_present(text_a, marker)
    ]
    citations_removed = [
        marker["citation"]
        for marker in removed_markers
        if citation_present(text_a, marker) and not citation_present(text_b, marker)
    ]
    return citations_added, citations_removed


def build_section_events(
    sections_a: dict[str, tuple[str, int]],
    sections_b: dict[str, tuple[str, int]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for path in sorted(set(sections_a) - set(sections_b)):
        title, level = sections_a[path]
        events.append(
            {
                "change_type": "section_deleted",
                "path": path,
                "title": title,
                "level": level,
            }
        )

    for path in sorted(set(sections_b) - set(sections_a)):
        title, level = sections_b[path]
        events.append(
            {
                "change_type": "section_added",
                "path": path,
                "title": title,
                "level": level,
            }
        )

    for path in sorted(set(sections_a) & set(sections_b)):
        title_a, level_a = sections_a[path]
        title_b, _ = sections_b[path]
        if title_a == title_b:
            continue
        events.append(
            {
                "change_type": "section_title_changed",
                "path": path,
                "title_a": title_a,
                "title_b": title_b,
                "level": level_a,
            }
        )

    return events


def classify_match(
    paragraph_a: Paragraph,
    paragraph_b: Paragraph,
    *,
    citations_added: list[str],
    citations_removed: list[str],
) -> tuple[str, float | None, str | None, str | None]:
    same_text = paragraph_a.norm_text == paragraph_b.norm_text
    same_section = paragraph_a.section_path == paragraph_b.section_path

    if same_text and same_section:
        return "unchanged", None, None, None
    if same_text:
        return "section_moved", None, paragraph_a.section_path, paragraph_b.section_path

    similarity = round(sequence_similarity(paragraph_a.norm_text, paragraph_b.norm_text), 3)
    if not same_section:
        return "section_moved_modified", similarity, paragraph_a.section_path, paragraph_b.section_path
    if citations_added and citations_removed:
        return "citation_updated", similarity, None, None
    if citations_added:
        return "citation_added", similarity, None, None
    if citations_removed:
        return "citation_removed", similarity, None, None
    if similarity >= 0.95:
        return "minor_edit", similarity, None, None
    return "reformulation", similarity, None, None


def build_paragraph_changes(
    paragraphs_a: list[Paragraph],
    paragraphs_b: list[Paragraph],
    *,
    pair_added: list[str],
    pair_removed: list[str],
) -> list[dict[str, Any]]:
    matches = align_paragraphs(paragraphs_a, paragraphs_b)
    matched_b = {index_b for index_b in matches.values()}
    added_markers = build_citation_markers(pair_added)
    removed_markers = build_citation_markers(pair_removed)

    output: list[dict[str, Any]] = []
    emitted_b: set[int] = set()

    for index_a, paragraph_a in enumerate(paragraphs_a):
        index_b = matches.get(index_a)
        if index_b is None:
            citations_added, citations_removed = paragraph_citation_delta(
                text_a=paragraph_a.text,
                text_b=None,
                added_markers=added_markers,
                removed_markers=removed_markers,
            )
            output.append(
                {
                    "change_type": "paragraph_deleted",
                    "section_path": paragraph_a.section_path,
                    "section_title": paragraph_a.section_title,
                    "section_level": paragraph_a.section_level,
                    "para_num_a": paragraph_a.para_num,
                    "para_num_b": None,
                    "similarity": None,
                    "text_a": paragraph_a.text,
                    "text_b": None,
                    "char_hash_a": paragraph_a.char_hash,
                    "char_hash_b": None,
                    "citations_added": citations_added,
                    "citations_removed": citations_removed,
                    "moved_from": None,
                    "moved_to": None,
                }
            )
            continue

        paragraph_b = paragraphs_b[index_b]
        emitted_b.add(index_b)
        citations_added, citations_removed = paragraph_citation_delta(
            text_a=paragraph_a.text,
            text_b=paragraph_b.text,
            added_markers=added_markers,
            removed_markers=removed_markers,
        )
        change_type, similarity, moved_from, moved_to = classify_match(
            paragraph_a,
            paragraph_b,
            citations_added=citations_added,
            citations_removed=citations_removed,
        )
        output.append(
            {
                "change_type": change_type,
                "section_path": paragraph_b.section_path,
                "section_title": paragraph_b.section_title,
                "section_level": paragraph_b.section_level,
                "para_num_a": paragraph_a.para_num,
                "para_num_b": paragraph_b.para_num,
                "similarity": similarity,
                "text_a": paragraph_a.text,
                "text_b": paragraph_b.text,
                "char_hash_a": paragraph_a.char_hash,
                "char_hash_b": paragraph_b.char_hash,
                "citations_added": citations_added,
                "citations_removed": citations_removed,
                "moved_from": moved_from,
                "moved_to": moved_to,
            }
        )

    for index_b, paragraph_b in enumerate(paragraphs_b):
        if index_b in emitted_b or index_b in matched_b:
            continue
        citations_added, citations_removed = paragraph_citation_delta(
            text_a=None,
            text_b=paragraph_b.text,
            added_markers=added_markers,
            removed_markers=removed_markers,
        )
        output.append(
            {
                "change_type": "paragraph_added",
                "section_path": paragraph_b.section_path,
                "section_title": paragraph_b.section_title,
                "section_level": paragraph_b.section_level,
                "para_num_a": None,
                "para_num_b": paragraph_b.para_num,
                "similarity": None,
                "text_a": None,
                "text_b": paragraph_b.text,
                "char_hash_a": None,
                "char_hash_b": paragraph_b.char_hash,
                "citations_added": citations_added,
                "citations_removed": citations_removed,
                "moved_from": None,
                "moved_to": None,
            }
        )

    return output


def build_existing_pair_map() -> set[tuple[str, str, str]]:
    pairs: set[tuple[str, str, str]] = set()
    for path in OUTPUT_DIFF_DIR.glob("*/*.json"):
        match = DIFF_FILENAME_RE.match(path.name)
        if not match:
            continue
        pairs.add((path.parent.name, match.group(1), match.group(2)))
    return pairs


def load_grouped_diffs() -> list[dict[str, Any]]:
    return json.loads(GROUPED_DIFFS_PATH.read_text())


def find_missing_pairs() -> list[dict[str, Any]]:
    existing_pairs = build_existing_pair_map()
    missing: list[dict[str, Any]] = []
    for row in load_grouped_diffs():
        pair = (
            row["guide_id"],
            snapshot_to_date(row["from_snapshot"]),
            snapshot_to_date(row["to_snapshot"]),
        )
        if pair in existing_pairs:
            continue
        missing.append(row)
    return missing


def build_diff_json(row: dict[str, Any]) -> dict[str, Any]:
    guide_id = row["guide_id"]
    from_snapshot = row["from_snapshot"]
    to_snapshot = row["to_snapshot"]
    pdf_a = WAYBACK_DIR / guide_id / "snapshots" / from_snapshot
    pdf_b = WAYBACK_DIR / guide_id / "snapshots" / to_snapshot
    if not pdf_a.exists() or not pdf_b.exists():
        raise FileNotFoundError(f"Missing PDF snapshot for {guide_id}: {from_snapshot} or {to_snapshot}")

    paragraphs_a, sections_a = extract_paragraphs(pdf_a)
    paragraphs_b, sections_b = extract_paragraphs(pdf_b)

    paragraph_changes = build_paragraph_changes(
        paragraphs_a,
        paragraphs_b,
        pair_added=row.get("added", []),
        pair_removed=row.get("removed", []),
    )
    section_events = build_section_events(sections_a, sections_b)
    change_counts: dict[str, int] = {}
    for paragraph in paragraph_changes:
        change_type = paragraph["change_type"]
        change_counts[change_type] = change_counts.get(change_type, 0) + 1
    section_counts: dict[str, int] = {}
    for event in section_events:
        change_type = event["change_type"]
        section_counts[change_type] = section_counts.get(change_type, 0) + 1

    version_a = snapshot_to_date(from_snapshot)
    version_b = snapshot_to_date(to_snapshot)
    return {
        "version_a": version_a,
        "version_b": version_b,
        "delta_days": (parse_iso_date(version_b) - parse_iso_date(version_a)).days,
        "guide_title": row["guide_title"],
        "summary": {
            "total_para_changes": len(paragraph_changes),
            "paragraph_changes": change_counts,
            "section_events": section_counts,
            "cited_cases_added": len(row.get("added", [])),
            "cited_cases_removed": len(row.get("removed", [])),
        },
        "section_events": section_events,
        "paragraph_changes": paragraph_changes,
        "cited_cases_diff": {
            "added": row.get("added", []),
            "removed": row.get("removed", []),
        },
    }


def output_path_for_row(row: dict[str, Any]) -> Path:
    guide_id = row["guide_id"]
    version_a = snapshot_to_date(row["from_snapshot"])
    version_b = snapshot_to_date(row["to_snapshot"])
    return OUTPUT_DIFF_DIR / guide_id / f"diff_{version_a}__{version_b}.json"


def main() -> None:
    if hasattr(fitz, "TOOLS"):
        try:
            fitz.TOOLS.mupdf_display_errors(False)
            fitz.TOOLS.mupdf_display_warnings(False)
        except Exception:
            pass

    missing_rows = find_missing_pairs()
    written = 0
    for row in missing_rows:
        output_path = output_path_for_row(row)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        diff_json = build_diff_json(row)
        output_path.write_text(json.dumps(diff_json, indent=2, ensure_ascii=False))
        written += 1
        print(f"Wrote {output_path}")

    print(f"Wrote {written} missing transition files")


if __name__ == "__main__":
    main()
