from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REPOS = {
    "echr-guide-citation-diffs-cleaned": {
        "files": [
            ("outputs/citation_diff_cleanup/cleaned_citation_diffs.csv", "cleaned_citation_diffs.csv"),
            ("outputs/citation_diff_cleanup/cleaned_diffs_grouped.json", "cleaned_diffs_grouped.json"),
            ("outputs/citation_diff_cleanup/cleaned_extracted_citations.json", "cleaned_extracted_citations.json"),
            ("outputs/citation_diff_cleanup/comparison_details.json", "comparison_details.json"),
            ("outputs/citation_diff_cleanup/reaudit_summary.md", "reaudit_summary.md"),
        ],
        "readme": """---
pretty_name: ECHR Guide Citation Diffs Cleaned
language:
- en
tags:
- legal
- echr
- temporal
- citations
- dataset-creation
size_categories:
- 1K<n<10K
---

# Dataset Card for ECHR Guide Citation Diffs Cleaned

## Dataset Summary

This dataset contains cleaned and reaudited citation additions and removals across consecutive versions of European Court of Human Rights guide PDFs.

It is a derived release built from the original `lexgenie/echr-guide-citation-diffs` dataset, which provides the underlying guide snapshots and citation-diff corpus. This cleaned release repackages the diff layer into analysis-friendly CSV and JSON files and includes the outputs used in our re-audit workflow.

## Dataset Structure

Main files:

- `cleaned_citation_diffs.csv`: flat event-level table, one row per citation addition or removal
- `cleaned_diffs_grouped.json`: grouped transition records, one item per guide version pair
- `cleaned_extracted_citations.json`: extracted citation lists per guide snapshot
- `comparison_details.json`: detailed comparison metadata
- `reaudit_summary.md`: brief notes on the cleanup and re-audit process

## Dataset Statistics

- grouped guide transitions: `103`
- flat citation-change rows: `1014`

## Intended Use

This dataset is intended for:

- studying temporal citation changes in ECHR guides
- identifying which cases are added or removed between guide versions
- serving as the base layer for downstream case linking and guide-update modeling

## Provenance

Derived from:

- `lexgenie/echr-guide-citation-diffs`

This cleaned release should be treated as a downstream processing layer over the original citation-diff corpus, not as a replacement for the underlying PDF archive.
""",
    },
    "echr-guide-case-catalog": {
        "files": [
            ("outputs/case_catalog/cases_catalog.csv", "cases_catalog.csv"),
            ("outputs/case_catalog/cases_catalog.json", "cases_catalog.json"),
            ("outputs/case_catalog/case_guides.csv", "case_guides.csv"),
            ("outputs/case_catalog/case_appearances.csv", "case_appearances.csv"),
            (
                "outputs/case_catalog/audit/cases_catalog_hudoc_report.json",
                "audit/cases_catalog_hudoc_report.json",
            ),
            (
                "outputs/case_catalog/audit/cases_catalog_hudoc_unmatched.csv",
                "audit/cases_catalog_hudoc_unmatched.csv",
            ),
        ],
        "readme": """---
pretty_name: ECHR Guide Case Catalog
language:
- en
tags:
- legal
- echr
- hudoc
- entity-linking
- dataset-creation
size_categories:
- 1K<n<10K
---

# Dataset Card for ECHR Guide Case Catalog

## Dataset Summary

This dataset links citation strings found in ECHR guide materials to canonical HUDOC case records and guide-level appearances.

It is derived from the cleaned citation-diff layer and a HUDOC enrichment pass. The goal is to move from raw citation strings to normalized case entities with stable identifiers, document types, importance levels, and guide appearance metadata.

## Dataset Structure

Main files:

- `cases_catalog.csv`: main case catalog table
- `cases_catalog.json`: JSON export of the same catalog
- `case_guides.csv`: guide-level case associations
- `case_appearances.csv`: finer-grained appearance-level mapping
- `audit/cases_catalog_hudoc_report.json`: matching statistics and audit counts
- `audit/cases_catalog_hudoc_unmatched.csv`: unresolved citation rows

## Dataset Statistics

- catalog rows: `7846`
- matched rows: `7759`
- unmatched rows: `87`
- guide-link rows in `case_guides.csv`: `12310`
- appearance rows in `case_appearances.csv`: `61646`

## Intended Use

This dataset is intended for:

- canonical case linking
- joining guide citations to HUDOC metadata
- filtering or ranking guide changes by case importance or document type
- serving as an intermediate layer between citation diffs and body-context localization

## Provenance

Derived from:

- `lexgenie/echr-guide-citation-diffs`
- the cleaned citation-diff outputs in `lexgenie/echr-guide-citation-diffs-cleaned`
- HUDOC enrichment and matching

This dataset should be treated as a normalized entity layer rather than as a raw citation-diff release.
""",
    },
    "echr-case-linked-guide-diffs": {
        "files": [
            ("outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv", "case_linked_guide_diffs.csv"),
            ("outputs/case_linked_guide_diffs/case_linked_guide_diffs.json", "case_linked_guide_diffs.json"),
            (
                "outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv",
                "case_linked_guide_diff_paragraphs.csv",
            ),
            (
                "outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.json",
                "case_linked_guide_diff_paragraphs.json",
            ),
            (
                "outputs/case_linked_guide_diffs/case_linked_guide_diffs_report.json",
                "case_linked_guide_diffs_report.json",
            ),
        ],
        "readme": """---
pretty_name: ECHR Case-Linked Guide Diffs
language:
- en
tags:
- legal
- echr
- temporal
- retrieval
- editing
- dataset-creation
size_categories:
- 1K<n<10K
---

# Dataset Card for ECHR Case-Linked Guide Diffs

## Dataset Summary

This dataset links case-level citation changes in ECHR guides to localized guide-body transitions, including section references and before/after paragraph text.

It is intended as a modeling-ready derived layer for tasks such as:

- guide-update retrieval
- section localization
- edit-type prediction
- constrained paragraph rewriting

## Dataset Structure

Main files:

- `case_linked_guide_diffs.csv`: case-level linked transitions
- `case_linked_guide_diffs.json`: JSON export of the same
- `case_linked_guide_diff_paragraphs.csv`: paragraph-level linked transitions
- `case_linked_guide_diff_paragraphs.json`: JSON export of the same
- `case_linked_guide_diffs_report.json`: coverage and linkage summary

## Dataset Statistics

- case-level rows: `1014`
- paragraph-level rows: `1489`
- linked case rows: `805`
- unlinked rows with existing transition pairs: `209`
- missing transition pairs: `0`

## Intended Use

This dataset is intended for:

- retrieving the guide section affected by a case
- predicting where a case should be inserted into a guide
- classifying whether an edit is an addition, removal, or revision
- constructing small-scale guide-update generation prototypes

## Provenance

Derived from:

- `lexgenie/echr-guide-citation-diffs`
- `lexgenie/echr-guide-case-catalog`
- guide-body transition parsing and paragraph alignment

This dataset should be treated as a derived body-context layer over the original citation-diff corpus.
""",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage split Hugging Face dataset repos with README cards and approved files."
    )
    parser.add_argument(
        "--out",
        default="/private/tmp/lexgenie_hf_split_repos",
        help="Directory to populate with staged dataset repos.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = Path(args.out).resolve()

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for repo_name, repo_spec in REPOS.items():
        repo_dir = out_dir / repo_name
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "README.md").write_text(repo_spec["readme"], encoding="utf-8")
        print(f"Wrote README for {repo_name}")

        for source_rel, dest_rel in repo_spec["files"]:
            source = repo_root / source_rel
            if not source.exists():
                raise FileNotFoundError(f"Missing source file: {source}")
            dest = repo_dir / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            print(f"  staged {source_rel} -> {repo_name}/{dest_rel}")

    print(f"Staged split repos in {out_dir}")


if __name__ == "__main__":
    main()
