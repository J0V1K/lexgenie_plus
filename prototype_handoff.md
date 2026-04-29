# Prototype Handoff For The Next Autonomous Agent

This document is the operational handoff for building the first end-to-end prototype in this directory.

Use this file as the current source of truth for prototype work. Some older planning documents in the repo are now partially stale relative to the latest data artifacts.

## Objective

Build a thin, high-precision prototype for the ECHR guide-update pipeline using the current case-linked guide diff dataset.

Do not try to solve the full research problem in one pass. The immediate goal is to prove an end-to-end path on a filtered subset:

1. given a case,
2. identify whether it is likely to matter for a guide,
3. retrieve the likely guide section or paragraph,
4. predict the edit action,
5. optionally generate a simple update on the easiest cases.

## Current State

The most important current outputs are:

- [case_linked_guide_diffs.csv](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv)
- [case_linked_guide_diff_paragraphs.csv](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv)
- [case_linked_guide_diffs_report.json](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diffs_report.json)
- [cases_catalog.csv](/Users/jovik/Desktop/lexgenie/outputs/case_catalog/cases_catalog.csv)
- [cases_catalog_hudoc_report.json](/Users/jovik/Desktop/lexgenie/outputs/case_catalog/audit/cases_catalog_hudoc_report.json)
- [cleaned_citation_diffs.csv](/Users/jovik/Desktop/lexgenie/outputs/citation_diff_cleanup/cleaned_citation_diffs.csv)
- [cleaned_diffs_grouped.json](/Users/jovik/Desktop/lexgenie/outputs/citation_diff_cleanup/cleaned_diffs_grouped.json)

Current case-linked diff report:

- `citation_diff_rows`: `1014`
- `with_diff_pair`: `1014`
- `missing_diff_pair`: `0`
- `linked_rows`: `805`
- `unlinked_rows_existing_pair`: `209`
- `paragraph_rows`: `1489`

Current case catalog HUDOC report:

- `input_rows`: `7846`
- `matched_rows`: `7759`
- `unmatched_rows`: `87`

Important interpretation:

- The structural transition gap has been fixed. All citation diff rows now have a matching guide transition JSON.
- The remaining weakness is not coverage, but link quality.
- The `805` linked rows are the usable seed set for the first prototype.
- The `209` `no_paragraph_link` rows should not be used as positive supervision for location or generation in the first iteration.

## What Was Added Most Recently

The missing transition files were filled using:

- [fill_missing_guide_transitions.py](/Users/jovik/Desktop/lexgenie/scripts/fill_missing_guide_transitions.py)

That script:

- reads the missing citation-diff transitions from [cleaned_diffs_grouped.json](/Users/jovik/Desktop/lexgenie/outputs/citation_diff_cleanup/cleaned_diffs_grouped.json)
- parses guide PDFs from [`wayback/`](/Users/jovik/Desktop/lexgenie/wayback)
- uses bookmark anchors plus numbered body paragraphs
- creates compatible `anas-diff-dataset/<guide_id>/diff_<from>__<to>.json` files
- attaches paragraph-level citation hits where possible

The downstream linker was then rebuilt with:

- [build_case_linked_guide_diffs.py](/Users/jovik/Desktop/lexgenie/scripts/build_case_linked_guide_diffs.py)

## Files You Need To Understand

Core scripts:

- [build_case_catalog_from_guides.py](/Users/jovik/Desktop/lexgenie/scripts/build_case_catalog_from_guides.py)
- [enrich_case_catalog_from_hudoc.py](/Users/jovik/Desktop/lexgenie/scripts/enrich_case_catalog_from_hudoc.py)
- [rebuild_citation_diffs_clean.py](/Users/jovik/Desktop/lexgenie/scripts/rebuild_citation_diffs_clean.py)
- [build_case_linked_guide_diffs.py](/Users/jovik/Desktop/lexgenie/scripts/build_case_linked_guide_diffs.py)
- [fill_missing_guide_transitions.py](/Users/jovik/Desktop/lexgenie/scripts/fill_missing_guide_transitions.py)

Schema / framing docs:

- [diff_categorization_schema.md](/Users/jovik/Desktop/lexgenie/docs/diff_categorization_schema.md)
- [project_context.md](/Users/jovik/Desktop/lexgenie/project_context.md)

Viewer / inspection entry point:

- [app.py](/Users/jovik/Desktop/lexgenie/app.py)

## Dataset Fields That Matter For Prototyping

From [case_linked_guide_diffs.csv](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv):

- `guide_id`
- `guide_title`
- `from_snapshot_date`
- `to_snapshot_date`
- `case_key`
- `case_name`
- `application_numbers`
- `judgment_year`
- `citation_change`
- `citation_text`
- `hudoc_itemid`
- `hudoc_importance_level`
- `hudoc_doctype`
- `link_status`
- `linked_paragraph_count`
- `linked_sections`
- `linked_change_types`
- `linked_paragraph_refs`
- `linked_match_strategies`
- `pre_text`
- `post_text`

From [case_linked_guide_diff_paragraphs.csv](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv):

- `paragraph_match_strategies`
- `change_type`
- `section_path`
- `section_title`
- `para_num_a`
- `para_num_b`
- `similarity`
- `citations_added`
- `citations_removed`
- `text_a`
- `text_b`

## Recommended Prototype Scope

Do not start with generation.

Build the first prototype in this order:

1. high-confidence training slice
2. candidate retrieval over guide sections or paragraphs
3. relevance / routing baseline
4. location prediction baseline
5. edit-type baseline
6. optional minimal generation for easy `citation_added` cases

### High-Confidence Slice

Start with rows where:

- `link_status == linked_paragraphs`
- `pre_text` or `post_text` is non-empty
- `linked_sections` is non-empty

For a stricter slice, prefer rows where `linked_match_strategies` contains:

- `citation_field_case_key`

Then include:

- `citation_field_name_match`
- `paragraph_text_name_match`

Avoid using these rows for first-pass supervision:

- `link_status == no_paragraph_link`
- rows with obviously generic or empty `pre_text` and `post_text`
- rows that appear to be list-only additions with no body localization

## First Concrete Deliverables

The next agent should create these artifacts:

1. a filtered prototype dataset
2. a small audited dev set
3. a retrieval baseline
4. a location baseline
5. an edit-type baseline
6. a short experiment report with error analysis

Recommended output locations:

- `outputs/prototype/filtered_case_linked_rows.csv`
- `outputs/prototype/filtered_case_linked_rows.json`
- `outputs/prototype/dev_audit_sample.csv`
- `outputs/prototype/retrieval_eval.json`
- `outputs/prototype/location_eval.json`
- `outputs/prototype/edit_type_eval.json`
- `outputs/prototype/prototype_notes.md`

Recommended scripts:

- `scripts/build_prototype_dataset.py`
- `scripts/sample_prototype_dev_set.py`
- `scripts/run_retrieval_baseline.py`
- `scripts/run_location_baseline.py`
- `scripts/run_edit_type_baseline.py`

## Exact Prototype Tasks

### Task 1: Build The Filtered Prototype Dataset

Create a script that reads [case_linked_guide_diffs.csv](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv) and emits a filtered modeling table.

Each output row should include at least:

- guide metadata
- case metadata
- `citation_change`
- `hudoc_importance_level`
- `linked_sections`
- `linked_change_types`
- `linked_match_strategies`
- `pre_text`
- `post_text`
- a derived `usable_for_relevance`
- a derived `usable_for_location`
- a derived `usable_for_edit_type`
- a derived `usable_for_generation`

Suggested initial heuristics:

- `usable_for_relevance = link_status == linked_paragraphs`
- `usable_for_location = link_status == linked_paragraphs and linked_sections != ""`
- `usable_for_edit_type = usable_for_location and linked_change_types != ""`
- `usable_for_generation = usable_for_location and citation_change == "added" and post_text != ""`

Keep this conservative.

### Task 2: Create A Small Audited Dev Set

Sample `100-150` rows from the filtered dataset.

Stratify across:

- guide titles
- `citation_change`
- `linked_change_types`
- match strategy confidence

Add annotation columns:

- `gold_use_row`
- `gold_section`
- `gold_edit_type`
- `gold_link_correct`
- `gold_generation_feasible`
- `notes`

This is essential. Do not trust automatic linking blindly.

### Task 3: Retrieval Baseline

Treat this as a ranking problem first.

For each row:

- use the pre-update guide state as the candidate document space
- retrieve candidate sections or paragraphs for the case
- compare retrieved candidates with `linked_sections` or paragraph-level matches

Start simple:

- BM25 or TF-IDF over section titles plus paragraph text
- query built from:
  - case name
  - application numbers
  - HUDOC metadata if useful
  - possibly citation text without punctuation noise

Evaluation:

- section hit@1
- section hit@3
- section MRR

### Task 4: Location Baseline

Once retrieval works, predict the single best target section or paragraph.

Candidate units:

- section-level first
- paragraph-level second

Use the paragraph dataset at:

- [case_linked_guide_diff_paragraphs.csv](/Users/jovik/Desktop/lexgenie/outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv)

Evaluation:

- exact section accuracy
- paragraph hit@k
- accuracy on the audited dev subset

### Task 5: Edit-Type Baseline

Map `citation_change` and linked paragraph `change_type` into a simpler prototype label set.

Suggested first label set:

- `add`
- `remove`
- `revise`

Suggested mapping:

- `citation_change == added` and paragraph change in `paragraph_added`, `citation_added` -> `add`
- `citation_change == removed` and paragraph change in `paragraph_deleted`, `citation_removed` -> `remove`
- `minor_edit`, `citation_updated`, `reformulation`, `section_moved_modified` -> `revise`

This is a prototype mapping, not a final research schema.

### Task 6: Optional Minimal Generation

Only attempt this after the retrieval and location baselines exist.

Restrict to:

- `citation_change == added`
- a single linked paragraph
- non-empty `post_text`
- simple cases where the post paragraph explicitly contains the case name

The goal is not full guide rewriting. The goal is to test whether the agent can rewrite the target paragraph given:

- case metadata
- section info
- `pre_text`

And produce something closer to `post_text`.

## Evaluation Guidance

Separate the pipeline stages in evaluation.

Do not collapse everything into one metric.

Minimum useful evaluation:

- relevance: can the system identify candidate guide updates?
- retrieval: can it find the right section?
- location: can it find the right paragraph?
- edit type: can it classify add/remove/revise correctly?
- generation: on easy rows only, can it approximate the edited text?

For generation, use:

- exact citation inclusion
- case-name inclusion
- section correctness
- text overlap metrics only as secondary signals

Human inspection matters more than BLEU-style numbers here.

## Known Data Limitations

These points matter and should shape the prototype:

- `209` rows remain `no_paragraph_link`
- many unresolved rows are probably citation-list-only or weakly localized
- some generated transitions are approximate because they were reconstructed from PDF body parsing, not original annotation
- older planning docs still mention earlier counts and should not override the current report files

Residual `no_paragraph_link` rows are concentrated in guides such as:

- `Article 3`
- `Article 10`
- `Article 6 Criminal`
- `Prisoners' rights`
- `Article 34/35`

This means the first prototype should optimize for precision, not recall.

## Guardrails

These constraints were explicitly established in prior work and should be preserved:

- Do not perform unnecessary HUDOC passes for documents already enriched.
- Reuse the existing case catalog unless there is a concrete reason to refresh unresolved rows.
- Do not infer fields that are not present in source data.
- Do not use `no_paragraph_link` rows as gold location labels.
- Do not present generated text as authoritative legal content.

## Useful Commands

Rebuild linked dataset if needed:

```bash
python3 scripts/build_case_linked_guide_diffs.py
```

Fill missing transitions if needed:

```bash
python3 scripts/fill_missing_guide_transitions.py
```

Inspect current linked-diff report:

```bash
python3 - <<'PY'
import json
from pathlib import Path
print(Path('outputs/case_linked_guide_diffs/case_linked_guide_diffs_report.json').read_text())
PY
```

Quickly inspect CSV columns:

```bash
python3 - <<'PY'
import csv
from pathlib import Path
reader = csv.DictReader(Path('outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv').open())
print(reader.fieldnames)
PY
```

## Recommended First Move

The next agent should begin by implementing:

- [build_prototype_dataset.py](/Users/jovik/Desktop/lexgenie/scripts/build_prototype_dataset.py)

Then:

1. write the filtered prototype dataset
2. sample an audited dev set
3. build a simple retrieval baseline

If the next agent starts by trying to improve all parsing or all labels, it will lose time. The correct move is to stand up the first narrow pipeline on the existing `805` linked rows and use the dev audit to decide where further parser cleanup is actually worth the effort.
