from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MANUAL_OVERRIDE_CSV = Path("data/manual_case_overrides.csv")


@dataclass(frozen=True)
class ManualCaseOverride:
    action: str
    case_key: str
    guide_id: str
    from_version: str
    to_version: str
    change: str
    citation: str
    hudoc_itemid: str
    hudoc_docname: str
    hudoc_doctype: str
    hudoc_importance_level: str
    hudoc_languageisocode: str
    hudoc_appno: str
    hudoc_kpdate: str
    notes: str


def load_manual_case_overrides() -> list[ManualCaseOverride]:
    if not MANUAL_OVERRIDE_CSV.exists():
        return []
    with MANUAL_OVERRIDE_CSV.open() as handle:
        rows = list(csv.DictReader(handle))
    return [
        ManualCaseOverride(
            action=(row.get("action") or "").strip(),
            case_key=(row.get("case_key") or "").strip(),
            guide_id=(row.get("guide_id") or "").strip(),
            from_version=(row.get("from_version") or "").strip(),
            to_version=(row.get("to_version") or "").strip(),
            change=(row.get("change") or "").strip(),
            citation=(row.get("citation") or "").strip(),
            hudoc_itemid=(row.get("hudoc_itemid") or "").strip(),
            hudoc_docname=(row.get("hudoc_docname") or "").strip(),
            hudoc_doctype=(row.get("hudoc_doctype") or "").strip(),
            hudoc_importance_level=(row.get("hudoc_importance_level") or "").strip(),
            hudoc_languageisocode=(row.get("hudoc_languageisocode") or "").strip(),
            hudoc_appno=(row.get("hudoc_appno") or "").strip(),
            hudoc_kpdate=(row.get("hudoc_kpdate") or "").strip(),
            notes=(row.get("notes") or "").strip(),
        )
        for row in rows
    ]


def manual_match_overrides_by_case_key() -> dict[str, ManualCaseOverride]:
    return {
        override.case_key: override
        for override in load_manual_case_overrides()
        if override.action == "manual_match" and override.case_key
    }


def manual_drop_overrides() -> list[ManualCaseOverride]:
    return [
        override
        for override in load_manual_case_overrides()
        if override.action == "drop_citation_diff"
    ]


def matches_manual_drop(
    row: dict[str, Any], overrides: list[ManualCaseOverride] | None = None
) -> ManualCaseOverride | None:
    for override in overrides or manual_drop_overrides():
        if override.guide_id and row.get("guide_id", "") != override.guide_id:
            continue
        if override.from_version and row.get("from_version", "") != override.from_version:
            continue
        if override.to_version and row.get("to_version", "") != override.to_version:
            continue
        if override.change and row.get("change", "") != override.change:
            continue
        if override.citation and row.get("citation", "") != override.citation:
            continue
        return override
    return None


def official_doctype_priority(doctype: str, language: str) -> tuple[int, int, str]:
    if doctype in {"HEJUD", "HEDEC"} and language == "ENG":
        return (0, 0, doctype)
    if doctype in {"HFJUD", "HFDEC"} and language == "FRE":
        return (1, 0, doctype)
    if len(doctype) == 5 and doctype.startswith("HE") and language == "ENG":
        return (2, 0, doctype)
    if len(doctype) == 5 and doctype.startswith("HF") and language == "FRE":
        return (3, 0, doctype)
    if doctype.startswith("HECOM") or doctype.startswith("HFCOM"):
        return (7, 0, doctype)
    if doctype == "CLIN" and language == "ENG":
        return (8, 0, doctype)
    if doctype == "CLINF" and language == "FRE":
        return (9, 0, doctype)
    if doctype.startswith("H"):
        return (6, 0, doctype)
    if doctype.startswith("ADVPRO16OP"):
        return (10, 0, doctype)
    return (12, 0, doctype)


def is_official_case_document(doctype: str, language: str) -> bool:
    return official_doctype_priority(doctype, language)[0] <= 3


def guide_version_to_iso(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%d %B %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def semantic_transition_dates(row: dict[str, Any]) -> tuple[str, str]:
    explicit_from = (row.get("from_guide_version_date", "") or "").strip()
    explicit_to = (row.get("to_guide_version_date", "") or "").strip()
    parsed_from = explicit_from or guide_version_to_iso(str(row.get("from_version", "") or ""))
    parsed_to = explicit_to or guide_version_to_iso(str(row.get("to_version", "") or ""))
    snapshot_from = str(row.get("from_snapshot_date", "") or "")
    snapshot_to = str(row.get("to_snapshot_date", "") or "")

    if parsed_from and parsed_to and parsed_from <= parsed_to:
        return parsed_from, parsed_to
    if snapshot_from and snapshot_to and snapshot_from <= snapshot_to:
        return snapshot_from, snapshot_to
    return parsed_from or snapshot_from, parsed_to or snapshot_to


def semantic_from_date(row: dict[str, Any]) -> str:
    return semantic_transition_dates(row)[0]


def semantic_to_date(row: dict[str, Any]) -> str:
    return semantic_transition_dates(row)[1]


def temporal_split(row: dict[str, Any], cutoff: str) -> str:
    return "test" if semantic_to_date(row) >= cutoff else "dev"
