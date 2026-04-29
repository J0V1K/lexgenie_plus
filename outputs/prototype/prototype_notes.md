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

### Trigger detection (`outputs/trigger/`)

Produced by `scripts/run_trigger_baseline.py`. Binary classification: should this case cause any guide update? 805 positives + 3,090 hard negatives = 3,895 total rows.

**⚠️ Threshold selection note**: All F1 figures below use threshold selected on the evaluated split (oracle). For honest test-set evaluation use `test_at_dev_threshold` from `trigger_eval.json`. The test oracle figure previously quoted (F1=0.854) is an upper bound, not a reportable result — it is threshold-optimized on test data. Run `scripts/run_trigger_baseline.py` to regenerate with both oracle and dev-tuned test results.

| Model | AUROC | F1 (oracle thr) | Precision | Recall |
|---|---|---|---|---|
| Random | 0.501 | 0.343 | 0.207 | 1.000 |
| Importance | 0.961 | 0.705 | 0.603 | 0.848 |
| Article overlap | 0.978 | 0.714 | 0.702 | 0.726 |
| **Importance + Article overlap** | **0.956** | **0.738** | **0.837** | **0.661** |

Test set (n=530): dev-tuned F1 for `importance+art` is reported in `trigger_eval.json["test_at_dev_threshold"]` after re-running the script. The oracle test F1=0.854 is retained for comparison only.

**Interpretation caveat**: The `article_overlap` feature detects whether the case's convention articles match the guide's article title — this is guide-to-article routing via metadata, not content-based doctrinal novelty detection. These baselines establish that the *routing signal* is very strong; they do not yet test whether the case introduces genuinely new doctrine.

### Edit type classification (`outputs/prototype/edit_type_eval.json`)

Produced by `scripts/run_edit_type_baseline.py`. Rule-based classifier using paragraph-level diffs with similarity scores.

| Edit type | n | % | Median len ratio |
|---|---|---|---|
| add_citation | 572 | 71.1% | 1.37× |
| revise_text | 203 | 25.2% | 1.19× |
| remove_citation | 30 | 3.7% | 1.00× |

Subtypes: `new_paragraph` (377), `citation_insert` (189), `doctrinal_rewrite` (88), `paragraph_rewrite` (72), `citation_refresh` (43). Legacy agreement: 617/805 (76.6%).

### Paragraph-level location (`outputs/prototype/location_eval.json`)

Produced by `scripts/run_location_baseline.py`. Ranks individual diff paragraphs (not sections). Corpus size ~159 paragraphs per diff on average.

| Model | hit@1 | hit@3 | MRR | n |
|---|---|---|---|---|
| global_base | 0.068 | 0.107 | 0.109 | 805 |
| global_enriched | 0.108 | 0.149 | 0.152 | 805 |
| mention_boosted | 0.123 | 0.168 | 0.168 | 805 |
| section_boosted | 0.099 | 0.143 | 0.144 | 805 |
| **oracle_section** | **0.242** | **0.411** | **0.358** | 805 |

Oracle section ceiling (27.3% test hit@1) is lower than section-level hit@1 (32.7%), because paragraph indexing requires matching exact `(section, para_num_a, para_num_b)` triples. The ~48% `new_paragraph` rows have no pre-text, making BM25 paragraph matching structurally difficult.

### End-to-end pipeline (`outputs/pipeline/`)

Produced by `scripts/run_pipeline_eval.py`. Chains trigger → section retrieval.

**⚠️ Metric scope**: `pipeline_hit_at_1` measures trigger correct AND section retrieval hit@1. **Edit-type correctness is NOT included** — the description "end-to-end" was misleading. Edit type is predicted but not evaluated in the joint metric.

**⚠️ Threshold note**: Previous results used a fixed threshold of 0.5 for trigger. The script now reads the dev-optimal threshold from `trigger_eval.json` (requires running `run_trigger_baseline.py` first). Pipeline results below are from the prior 0.5 threshold run.

| Split | Trigger F1 | Section hit@1 | Pipeline hit@1 (trigger+location only) |
|---|---|---|---|
| Dev | 0.719 | 0.282 | 0.184 |
| **Test** | **~oracle** | **0.327** | **0.255** |

Pipeline hit@1 = fraction of evaluable positive rows where trigger fires correctly AND section retrieval ranks the correct section at rank 1. Random baseline ≈ 0.3%. Stale — re-run after regenerating trigger and retrieval predictions.

## Methodological Corrections (Applied)

These issues were identified in external review and have been fixed in the codebase.

### 1. Trigger threshold selection (fixed in `run_trigger_baseline.py`)

**Problem**: `best_f1()` was called on each evaluated split independently. When called on the test set, this finds the oracle threshold on test data — effectively tuning on test. The pipeline then used a completely different fixed threshold (0.5). These are contradictory.

**Fix**: The script now:
- Computes dev-optimal thresholds for each model on the dev split
- Reports `test_at_dev_threshold` (honest: dev threshold applied to test) as the primary test result
- Retains `test_oracle` (upper bound, threshold tuned on test) for comparison, clearly labeled

**Impact**: The previously quoted test F1=0.854 for `importance+art` is an oracle upper bound. The honest figure (dev-tuned threshold applied to test) is in `trigger_eval.json["test_at_dev_threshold"]` after re-running the script.

### 2. Pipeline metric scope (fixed in `run_pipeline_eval.py`)

**Problem**: The pipeline metric was described as "end-to-end" but only measures trigger + section retrieval. Edit-type correctness was never evaluated.

**Fix**: The `pipeline_hit_at_1` description now explicitly states: *trigger fires correctly AND section retrieval hits at rank 1. Edit-type correctness NOT included.*

### 3. Pipeline trigger threshold (fixed in `run_pipeline_eval.py`)

**Problem**: Pipeline used hardcoded threshold 0.5, inconsistent with the threshold-tuned trigger results.

**Fix**: Pipeline now reads the dev-optimal threshold from `trigger_eval.json` (falls back to 0.5 if file absent). Re-run trigger baseline first to populate the threshold.

### 4. `law_only` retrieval added to main baseline (new in `run_retrieval_baseline.py`)

**Context**: The ablation showed THE LAW section alone (hit@1=0.320) beats full-text enrichment (0.318). This model was not exposed in the main retrieval script.

**Fix**: Added `law` model to `run_retrieval_baseline.py` — uses `base_query + law_section_tokens` instead of full case text. Now appears in `retrieval_eval.json` and `retrieval_predictions.csv` alongside random/base/enriched.

## Remaining Deliverables

| Artifact | Status |
|---|---|
| `filtered_case_linked_rows.csv` | Done |
| `dev_audit_sample.csv` (structure) | Done — needs annotation |
| `retrieval_eval.json` (section BM25 baseline) | Done |
| `retrieval_ablation.json` (law section ablation) | Done |
| `trigger_eval.json` | Done |
| `edit_type_eval.json` | Done |
| `location_eval.json` (paragraph-level) | Done |
| `pipeline_eval.json` | Done |
| `generation_pilot.csv` | Needs `ANTHROPIC_API_KEY` |
| dev_audit_sample.csv gold columns | Not started |

## Immediate Next Steps

**Prerequisite**: Regenerate evaluation artifacts after code fixes:
```
python3 scripts/run_trigger_baseline.py    # writes honest test_at_dev_threshold
python3 scripts/run_retrieval_baseline.py  # adds law model to retrieval_eval.json
python3 scripts/run_pipeline_eval.py       # uses dev-optimal threshold
```

1. **Human annotation** (blocking before any publishable claims): Fill `gold_*` columns in `dev_audit_sample.csv`. All current metrics are against auto-derived labels. Until audited gold exists, results are engineering diagnostics, not evidence. See `dataset_audit.md` for known issues (at least 21 suspicious rows needing normalization).

2. **Generation pilot**: `ANTHROPIC_API_KEY=sk-... python3 scripts/run_generation_pilot.py`. Note: citation_hit and ROUGE-L are surface metrics — they do not verify legal correctness or faithfulness. Treat as a starting point, not a result. Only evaluate generation on rows with verified-valid location links (requires step 1 first for full credibility).

3. **Neural retrieval for section location**: BM25 law-section ceiling is ~32% hit@1 (conditional). A bi-encoder (e.g., `BAAI/bge-base-en` or `intfloat/e5-base-v2`) fine-tuned on (law_section, guide_section) pairs should push substantially higher.

4. **Trigger framing**: The current trigger task is article-routing (does this case touch article X?) — not content-based doctrinal novelty. This is a useful baseline but should not be described as novelty detection. A next step is training on THE LAW section text for cases where article overlap = 1, to distinguish substantive from routine additions within the correct guide.

5. **Paragraph-level routing**: Oracle section ceiling (24.2% hit@1) is lower than section-level (32.7%) because paragraph matching requires exact `(section, para_num_a, para_num_b)` triples. The ~48% `new_paragraph` rows have no pre-text. A dedicated within-section paragraph ranker (conditioned on section prediction) would be more effective than global BM25.
