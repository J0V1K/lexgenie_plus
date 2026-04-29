# ECHR Guide Diff Categorization Schema

This schema instantiates the project's earlier three-way distinction:

- `substantive_doctrinal_change`
- `routine_editorial_change`
- `extraction_or_normalization_noise`

The three labels are still useful, but they should be treated as a final salience judgment, not as the whole annotation schema. Applying the WINELL Appendix A method to ECHR guide diffs shows that annotators need a validity gate and a surface-edit label before deciding doctrinal salience.

## Why The Schema Changes

WINELL reduces Wikipedia edits to insertions and removals after filtering superficial edits. That maps well to our citation-list diffs, but not to the full legal meaning of ECHR guide updates.

Our edits differ from Wikipedia edits in four important ways:

- Wikipedia uses explicit revision metadata and source URLs; ECHR guides use PDF snapshots, case citations, and application numbers.
- Wikipedia edits are usually sentence or paragraph changes; our current data is mostly citation-list changes, with body context still partially unparsed.
- Wikipedia URL additions are a factual-update signal; our equivalent signal is a case citation keyed by application number plus judgment date and guide context.
- ECHR removals are legally ambiguous; they may indicate pruning, consolidation, replacement by newer authority, or extraction noise, not necessarily that a proposition became false.

## Annotation Unit

Annotate one `change_event` at a time:

- `guide_id`
- `guide_title`
- `from_snapshot`
- `to_snapshot`
- `section_path`, if available
- `surface_operation`
- `citation`
- `application_numbers`
- `judgment_date`, if available
- `body_context_before`, if available
- `body_context_after`, if available

For the current citation-list-only dataset, `section_path` defaults to `List of cited cases`.

## Stage 1: Validity Gate

Annotators must first decide whether the diff is a real guide change.

Use exactly one:

- `valid_change`: the cited case or text is genuinely added, removed, or materially changed between snapshots.
- `normalization_equivalent`: the same underlying citation appears in both snapshots, but typography, language, app-number formatting, citation metadata, or abbreviation differences created a false diff.
- `extraction_artifact`: the row is caused by PDF parsing, wrapping, truncation, page headers, OCR spacing, or detached fragments.
- `needs_pdf_check`: the event cannot be classified from extracted text and needs source-PDF review.

Only `valid_change` events proceed to doctrinal salience labeling.

Examples:

- `Botten v. Norway, 19 February 1996, Reports of Judgments and Decisions 1996 I` vs `1996-I`: `normalization_equivalent`.
- `v. Romania, nos. 46201/16 and 47379/18, 28 November 2023`: `extraction_artifact` unless the full wrapped case name is recovered.
- `National & Provincial Building Society, Leeds Permanent Building Society and Yorkshire Building`: `extraction_artifact`.
- `Side by Side International Film Festival and Others v. Russia, no. 32678/18 and 2 others, 17 December` vs `17 December 2024`: `normalization_equivalent` when comparing parser outputs, but the completed citation should be retained in the cleaned dataset.

## Stage 2: Surface Operation

Use exactly one for `valid_change` events:

- `citation_added`: a case citation appears in the later guide version but not the earlier one.
- `citation_removed`: a case citation appears in the earlier guide version but not the later one.
- `citation_metadata_modified`: the same case remains but citation metadata changes, such as date, report series, application-number expansion, or asterisk marker.
- `citation_replaced`: one cited authority is removed and a related authority is added in the same legal context.
- `body_text_added`: doctrinal guide body text is added.
- `body_text_removed`: doctrinal guide body text is removed.
- `body_text_modified`: doctrinal guide body text is materially revised.
- `section_move_or_reorganization`: content is moved across sections without clear doctrinal change.

For the current dataset, most valid events will be `citation_added` or `citation_removed`.

## Stage 3: Editorial Function

Use one primary label, plus optional secondary labels if needed:

- `new_authority_added`: a newly decided or newly relevant case is added to the guide.
- `authority_pruned`: an older or less central case is removed without a clear replacement.
- `authority_replaced_or_consolidated`: one authority is removed while another related authority is added, likely to update or consolidate coverage.
- `metadata_cleanup`: the change completes, corrects, or normalizes citation metadata.
- `cross_guide_harmonization`: the same case is added across several guides in the same update cycle.
- `body_context_expansion`: the guide adds discussion around a case or legal principle.
- `body_context_narrowing`: the guide removes or narrows discussion around a case or legal principle.
- `unclear_editorial_function`: the extracted evidence is valid but insufficient to determine the function.

## Stage 4: Doctrinal Salience

Use exactly one:

- `substantive_doctrinal_change`: the event likely reflects a new, clarified, narrowed, or leading legal principle, or a case newly treated as important by ECHR-KS editors.
- `routine_editorial_change`: the event is real but mainly reflects maintenance, metadata cleanup, pruning, cross-guide synchronization, or non-doctrinal citation management.
- `uncertain_doctrinal_salience`: the event is valid, but the citation-list diff alone does not support a confident salience judgment.

Decision rules:

- Prefer `substantive_doctrinal_change` when a recent judgment is added and is discussed in the guide body, cited in a relevant doctrinal section, or appears across legally related guide sections.
- Prefer `routine_editorial_change` for metadata changes, old-citation pruning, formatting changes, and cases that appear only as list maintenance.
- Prefer `uncertain_doctrinal_salience` for valid citation additions where we only know that a case entered the cited-cases list, but have no body-context evidence.
- Do not label extraction or normalization failures as `routine_editorial_change`; keep them in the Stage 1 validity labels.

## Pilot Application To Current Diff Types

| Example | Stage 1 | Stage 2 | Stage 3 | Stage 4 |
|---|---|---|---|---|
| `Associations of Communally-owned Forestry Proprietors ... v. Romania` recovered from a prior `v. Romania` fragment | `valid_change` after cleanup | `citation_added` | `new_authority_added` | `uncertain_doctrinal_salience` until body context is checked |
| `Botten v. Norway` add/remove pairs caused by `1996 I` vs `1996-I` | `normalization_equivalent` | not applicable | not applicable | not applicable |
| `National & Provincial Building Society, Leeds Permanent Building Society and Yorkshire Building` | `extraction_artifact` | not applicable | not applicable | not applicable |
| `Side by Side International Film Festival ... 17 December 2024` added to LGBTI guide | `valid_change` | `citation_added` | `new_authority_added` or `cross_guide_harmonization` if seen in multiple guides | `uncertain_doctrinal_salience` until section/body context is checked |
| `Bradshaw ... 22 July 2025*` vs same citation without `*` | `normalization_equivalent` for parser comparison | `citation_metadata_modified` if the asterisk is an actual guide marker change | `metadata_cleanup` | `routine_editorial_change` |
| `Kubát ... nos. 61721/19 and 5 others` vs fully enumerated application-number list | `normalization_equivalent` if underlying applications match | `citation_metadata_modified` | `metadata_cleanup` | `routine_editorial_change` |
| `Farhad Mehdiyev c. Azerbaïdjan, no 36057/18, 18 March 2025` local-only addition | `needs_pdf_check` until verified | likely `citation_added` | likely `new_authority_added` | `uncertain_doctrinal_salience` |

## Applicability Assessment

The old three-way schema is about 60-70% applicable for final labels, but it is too coarse for annotation. It collapses three distinct decisions:

- Is the diff real?
- What surface edit occurred?
- Is the real edit doctrinally meaningful?

The revised schema keeps the original labels but moves them to Stage 4. This matches WINELL's lesson that superficial edits should be filtered before modeling, while respecting the legal-domain difference that a real ECHR citation change is not automatically a doctrinal novelty label.

## Minimum Pilot Protocol

For the first 100-150 events, annotators should record:

- `validity_label`
- `surface_operation`
- `editorial_function`
- `doctrinal_salience`
- `confidence`: `high`, `medium`, or `low`
- `requires_body_context`: `yes` or `no`
- `notes`

Rows with `validity_label != valid_change` should be excluded from novelty-detection training labels, but retained for reporting dataset-cleanup decisions.
