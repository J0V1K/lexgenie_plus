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

All 1,014 rows are scored; the 209 `no_paragraph_link` rows score 0 (real misses, not skips). Temporal split: `to_snapshot_date >= 2025-11-25` is test (n=110 evaluable), earlier is dev (n=695 evaluable).

Query variants:
- **Random**: per-row deterministic shuffle (seed=row index)
- **Base**: case name + application numbers + citation text
- **Enriched**: base + full judgment text (when available)

**Unconditional results (all 1,014 rows; unlinked rows score 0):**

| Model | hit@1 | hit@3 | MRR | n |
|---|---|---|---|---|
| Random | 0.025 | 0.072 | 0.089 | 1014 |
| Base | 0.095 | 0.179 | 0.179 | 1014 |
| Enriched | 0.249 | 0.379 | 0.346 | 1014 |

**Conditional results (linked + evaluable rows only):**

| Model | hit@1 | hit@3 | MRR | n |
|---|---|---|---|---|
| Random | 0.031 | 0.091 | 0.113 | 805 |
| Base | 0.119 | 0.226 | 0.226 | 805 |
| Enriched | 0.314 | 0.477 | 0.436 | 805 |

**Temporal split (conditional, enriched):**

| Split | hit@1 | hit@3 | MRR | n |
|---|---|---|---|---|
| Dev enriched | 0.312 | 0.479 | 0.436 | 695 |
| Test enriched | 0.327 | 0.464 | 0.439 | 110 |
| Dev base | 0.132 | 0.243 | 0.240 | 695 |
| Test base | 0.036 | 0.118 | 0.135 | 110 |

Gold-in-corpus rate: 99.0%.

Key findings:
1. Case text provides ~3× hit@1 improvement over base query. The base-only query has almost no topical signal because for `citation_added` rows (97% of the set) the case doesn't yet appear in the pre-update guide text.
2. **Critical: base query degrades severely on test set** (hit@1 drops from 0.132 dev → 0.036 test). Test cases are from the most recent transitions and are genuinely new — unseen in any pre-update text. The enriched query is stable across splits, confirming judgment text is load-bearing.
3. hit@10 is not a meaningful metric: with median corpus size 64, top-10 ≈ 15.6% ≈ random chance. Use hit@1 and MRR.

### Negative examples (`outputs/negatives/`)

Produced by `scripts/build_negative_examples.py`. For each of 103 guide transition windows, queries HUDOC for judgments published in that window but not added to the guide. These are hard negatives for the novelty-detection task.

- 103 transitions, 86 unique date windows queried
- 3,090 total negatives (cap: 30 per transition)
- Fields: guide_id, from/to_snapshot_date, case_key, case_name, application_numbers, hudoc_itemid, hudoc_importance_level, hudoc_doctype, hudoc_conclusion, convention_articles, judgment_year, label="negative"

### Judgment text section ablation (`retrieval_ablation.json`)

Produced by `scripts/run_retrieval_ablation.py`. Splits judgment text into sections (THE FACTS / THE LAW / FOR THESE REASONS) and ablates each section as the BM25 query.

**Results on rows with case text available (n=660):**

| Query | hit@1 | hit@3 | MRR |
|---|---|---|---|
| base_only | 0.109 | 0.227 | 0.223 |
| facts | 0.217 | 0.402 | 0.355 |
| **law** | **0.320** | **0.529** | **0.464** |
| operative | 0.124 | 0.306 | 0.270 |
| full_text | 0.318 | 0.497 | 0.453 |

Key findings:
- **Law section alone beats full text** (hit@1 0.320 vs 0.318). THE LAW section contains the legal analysis that directly aligns with doctrinal guide sections — the operative provisions and facts sections add noise.
- **Operative provisions are nearly worthless** (0.124 hit@1 ≈ base_only 0.109). The dispositif is formulaic and contains no topical signal for section routing.
- Facts section contributes moderate signal (0.217) — the factual narrative contains enough case-specific vocabulary to retrieve relevant sections.
- The law section is the optimal BM25 query for section retrieval; full text adds marginal noise.

## Known Limitations

1. **Link quality**: 209 rows are `no_paragraph_link` and scored 0 (real misses). These are concentrated in Article 3, Article 10, Article 6 Criminal, Prisoners' rights, Article 34/35 guides. The prototype optimizes for precision, not coverage.

2. **Case text gaps**: 107 cases have no HUDOC DOCX available (consistent HTTP 500). These may benefit from `kpthesaurus` codes as a lightweight topical proxy.

3. **Retrieval framing**: The retrieval task assumes editorial citation signal aligns exactly with "case is relevant to this section." In practice, some cases are added to citation lists but discussed in different sections. The dev audit should check this.

4. **Missing annotation**: The dev audit sample has not been reviewed. No gold labels exist yet. All evaluation is against automatically-derived `linked_sections` labels, which inherit link-quality noise.

5. **No learned model**: The pipeline is entirely rule-based and BM25-based. No fine-tuned retriever, no LLM rewriter.

6. **Query length bottleneck**: BM25 with full judgment text queries (50k+ tokens) is slow (~15 min per run). Production use would need query truncation or a learned dense retriever.

7. **Negative example contamination risk**: HUDOC negatives are defined as "published in window, not added to guide." Some may be false negatives (added later or in different guide versions). Not validated.

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
