from __future__ import annotations

import argparse
import shutil
from pathlib import Path


APPROVED_FILES = [
    "outputs/citation_diff_cleanup/cleaned_citation_diffs.csv",
    "outputs/citation_diff_cleanup/cleaned_diffs_grouped.json",
    "outputs/citation_diff_cleanup/cleaned_extracted_citations.json",
    "outputs/citation_diff_cleanup/comparison_details.json",
    "outputs/citation_diff_cleanup/reaudit_summary.md",
    "outputs/case_catalog/cases_catalog.csv",
    "outputs/case_catalog/cases_catalog.json",
    "outputs/case_catalog/case_guides.csv",
    "outputs/case_catalog/case_appearances.csv",
    "outputs/case_catalog/audit/cases_catalog_hudoc_report.json",
    "outputs/case_catalog/audit/cases_catalog_hudoc_unmatched.csv",
    "outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv",
    "outputs/case_linked_guide_diffs/case_linked_guide_diffs.json",
    "outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv",
    "outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.json",
    "outputs/case_linked_guide_diffs/case_linked_guide_diffs_report.json",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage the approved LexGenie dataset subset for Hugging Face upload."
    )
    parser.add_argument(
        "--out",
        default="/tmp/lexgenie_hf_dataset_subset",
        help="Directory to populate with the staged dataset subset.",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not remove the output directory before staging.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    out_dir = Path(args.out).resolve()

    if out_dir.exists() and not args.keep_existing:
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    total_bytes = 0

    for relative_path in APPROVED_FILES:
        source = repo_root / relative_path
        if not source.exists():
            raise FileNotFoundError(f"Missing approved file: {source}")
        destination = out_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
        total_bytes += source.stat().st_size
        print(f"Staged {relative_path}")

    print(f"Staged {copied} files to {out_dir}")
    print(f"Total bytes: {total_bytes}")


if __name__ == "__main__":
    main()
