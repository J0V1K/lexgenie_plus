# Prototype Notes

Summary of the first prototype sprint. For detailed task specifications see `prototype_handoff.md`.

## What Was Built

### Filtered prototype dataset (`filtered_case_linked_rows.csv`)

Produced by `scripts/build_prototype_dataset.py`. Reads the 1,014-row `case_linked_guide_diffs.csv` and adds four usability flags plus case-text availability.

| Flag | Count | Description |
|---|---|---|
| `usable_for_relevance` | 805 | `link_status == linked_paragraphs` |
| `usable_for_location` | 805 | same (all linked rows have sections) |
| `usable_for_edit_type` | 805 | same (all linked rows have change types) |
| `usable_for_generation` | 784 | linked + citation_added + non-empty post_text |
| `strict_citation_field_match` | 151 | matched via exact case key in citation field |
| `case_text_available` | 706 | full judgment text fetched from HUDOC |

Citation change breakdown among linked rows: 784 added, 21 removed.

HUDOC importance breakdown (linked rows): 112 level-1 (Grand Chamber), 556 level-3, 122 level-4.

### Dev audit sample (`dev_audit_sample.csv`)

Produced by `scripts/sample_prototype_dev_set.py`. 120-row stratified sample across 38 guides using a round-robin across `(guide_id, citation_change, strict_citation_field_match)` strata. Seed 42.

**Status: annotation columns (`gold_*`) are empty and need human review.**

Annotation columns: `gold_use_row`, `gold_section`, `gold_edit_type`, `gold_link_correct`, `gold_generation_feasible`, `notes`.

### Full judgment text corpus (`outputs/case_texts/`)

Produced by `scripts/fetch_linked_case_texts.py`. Fetches DOCX from HUDOC and extracts plain text via stdlib zipfile/XML parsing.

- 602 unique cases attempted
- 495 fetched successfully (82.2%), 107 HTTP 500 (HUDOC conversion unavailable)
- Total text: 42.5M characters, median 60K chars per case
- 706 linked rows have text (some cases appear in multiple rows)

The 107 failures are predominantly 2023–2025 HEJUD judgments. HUDOC returns 500 on DOCX and 204 on PDF for these — not transient. Fallback options: `kpthesaurus` topical codes (available via JSON API, not yet used) or headless browser rendering.

### BM25 retrieval baseline (`retrieval_eval.json`, `retrieval_predictions.csv`)

Produced by `scripts/run_retrieval_baseline.py`. Evaluates section-level retrieval: for each case, rank guide sections by BM25 score and compare top-k to `linked_sections`.

Corpus: pre-update guide sections reconstructed from `text_a` paragraphs in the diff JSON.

Query variants:
- **Base**: case name + application numbers + citation text
- **Enriched**: base + full judgment text (when available)

Results:

| Condition | n | hit@1 | hit@3 | hit@10 | MRR |
|---|---|---|---|---|---|
| Base (all 805) | 805 | 0.119 | 0.226 | 0.463 | 0.226 |
| Enriched (all 805) | 805 | 0.314 | 0.477 | 0.698 | 0.436 |
| Base (660 with text) | 660 | 0.120 | 0.233 | 0.470 | 0.229 |
| Enriched (660 with text) | 660 | 0.358 | 0.539 | 0.756 | 0.485 |
| Base (145 no text) | 145 | 0.117 | 0.193 | 0.435 | 0.212 |
| Base strict (151) | 151 | 0.020 | 0.086 | 0.272 | 0.108 |
| Enriched strict (151) | 151 | 0.252 | 0.371 | 0.629 | 0.359 |

Gold-in-corpus rate: 99.0% (ceiling is ~99% — the gold section exists in the pre-update guide for almost all rows).

Key finding: case text provides ~3× hit@1 improvement. The base-only query has almost no topical signal because for `citation_added` rows (97% of the set) the case doesn't yet appear in the pre-update guide text.

Strict-match rows perform worse on the base query but recover with enrichment, suggesting they are valid rows where the link quality is high but the case is genuinely topically diverse within the guide.

## Known Limitations

1. **Link quality**: 209 rows are `no_paragraph_link` and unused. These are concentrated in Article 3, Article 10, Article 6 Criminal, Prisoners' rights, Article 34/35 guides. The prototype optimizes for precision, not coverage.

2. **Case text gaps**: 107 cases have no HUDOC DOCX available (consistent HTTP 500). These may benefit from `kpthesaurus` codes as a lightweight topical proxy.

3. **Retrieval framing**: The retrieval task as currently framed assumes the editorial signal (citation added) aligns exactly with "case is relevant to this section." In practice, some cases are added to citation lists but discussed in different sections than where they were previously absent. The dev audit should check this.

4. **Missing annotation**: The dev audit sample has not been reviewed. No gold labels exist yet. All "evaluation" is against automatically-derived `linked_sections` labels, which inherit link-quality noise.

5. **No learned model**: The pipeline is entirely rule-based and BM25-based. No fine-tuned retriever, no LLM rewriter.

## Remaining Deliverables

From the handoff doc:

| Artifact | Status |
|---|---|
| `filtered_case_linked_rows.csv` | Done |
| `dev_audit_sample.csv` (structure) | Done — needs annotation |
| `retrieval_eval.json` (BM25 baseline) | Done |
| `location_eval.json` | Not started |
| `edit_type_eval.json` | Not started |
| `run_location_baseline.py` | Not started |
| `run_edit_type_baseline.py` | Not started |

## Immediate Next Steps

1. **Human annotation**: Fill `gold_*` columns in `dev_audit_sample.csv`. Focus first on `gold_link_correct` and `gold_section` — these validate the dataset before further modeling.

2. **Location baseline** (`run_location_baseline.py`): given gold section, rank paragraphs within it using BM25 + case text. Uses `case_linked_guide_diff_paragraphs.csv`. Evaluate paragraph hit@k against `para_num_b`.

3. **Edit-type baseline** (`run_edit_type_baseline.py`): apply the mapping from `citation_change` + `linked_change_types` to `{add, remove, revise}`. Report class distribution and confusion on any labeled subset.

4. **Neural retrieval**: Replace BM25 with a bi-encoder (e.g., `BAAI/bge-base-en-v1.5`). The BM25 ceiling on the with-text subset is ~75% hit@10; neural retrieval should push this significantly.

5. **Generation pilot**: For rows with `usable_for_generation == true` where the post paragraph explicitly names the case, prompt an LLM with `pre_text + case_metadata → predicted post_text`. Evaluate citation inclusion rate as primary signal.
