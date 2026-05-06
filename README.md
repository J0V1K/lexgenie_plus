# LexGenie — ECHR Guide Update Pipeline

Research pipeline for automatically detecting when new European Court of Human Rights (ECHR) judgments require updates to existing doctrinal guides, and generating those updates.

**Target venues**: NLLP @ EMNLP 2026, AI4Law @ ICML 2026, JURIX 2026, ICAIL 2027

---

## Problem

The ECHR Knowledge Sharing Platform (ECHR-KS) publishes ~30 doctrinal guides that summarize case law by Convention article. These guides are updated as new judgments are handed down. Currently, editors must manually monitor new case law, decide which cases are doctrinally significant, locate the relevant guide section, and rewrite it.

This project builds a pipeline that does this automatically:

1. **Detect** whether a new judgment introduces doctrinal novelty significant enough to require a guide update
2. **Locate** which guide and section needs updating
3. **Generate** the updated paragraph

---

## Key Insight

ECHR-KS's editorial decisions are a ground-truth signal. When editors add a case to a guide, that choice reflects a deliberate judgment that the case matters doctrinally. Timestamp versioned guide PDFs scraped from the Wayback Machine provide a temporal supervision signal no prior work has exploited.

---

## Current State

This repository contains a working end-to-end prototype covering all three pipeline stages. The core dataset links 1,004 citation-change events across 38 guides to their HUDOC case records, paragraph-level guide sections, and full judgment texts. The negative set has been audited and supplemented with 303 hard negatives (importance 1–3, article-overlapping), giving 1,922 clean negatives for the trigger task. A random stratified 70/15/15 split is in `outputs/splits/trigger_dataset.csv`.

| Stage | Model | Metric | Test |
|---|---|---|---|
| **Trigger** | Article overlap | AUROC / F1 | 0.979 / 0.723 |
| **Location** | BM25 + law section | hit@1 / MRR | 0.366 / 0.482 |
| Edit type | Rule-based (paragraph-level) | — | — |
| **Pipeline** | Trigger → Location chain | hit@1 | **0.227** |

Trigger evals above use the pre-audit temporal split (see note in Prototype Results). Re-running on the new stratified split is the next step.

Generation evaluation (edit step) requires `ANTHROPIC_API_KEY` — see `scripts/run_generation_pilot.py`.

See `outputs/prototype/prototype_notes.md` for full results.

---

## Repository Structure

```
lexgenie/
├── scripts/                          # Main pipeline scripts (run in order below)
│   ├── build_case_catalog_from_guides.py     # Step 1: extract cases from guide PDFs
│   ├── enrich_case_catalog_from_hudoc.py     # Step 2: enrich with HUDOC metadata
│   ├── rebuild_citation_diffs_clean.py       # Step 3: clean citation diff records
│   ├── build_case_linked_guide_diffs.py      # Step 4: link diffs to cases + paragraphs
│   ├── build_prototype_dataset.py            # Step 5: filter to usable modeling rows
│   ├── fetch_linked_case_texts.py            # Step 6: fetch full judgment text from HUDOC
│   ├── sample_prototype_dev_set.py           # Step 7: stratified dev audit sample
│   ├── run_retrieval_baseline.py             # Step 8: BM25 section retrieval (location)
│   ├── run_location_baseline.py              # Step 9: BM25 paragraph-level location
│   ├── build_negative_examples.py            # Step 10: mine negatives from HUDOC
│   ├── audit_negative_examples.py            # Step 11: flag delayed positives + right-censored
│   ├── collect_hard_negatives.py             # Step 12: extract importance 1–3 hard negatives
│   ├── create_dataset_split.py               # Step 13: random stratified 70/15/15 split
│   ├── run_retrieval_ablation.py             # Step 14: section ablation study
│   ├── run_trigger_baseline.py               # Step 15: trigger detection evaluation
│   ├── run_edit_type_baseline.py             # Step 16: edit type classification
│   ├── run_pipeline_eval.py                  # Step 17: end-to-end pipeline accuracy
│   ├── run_generation_pilot.py               # Step 18: LLM paragraph generation (needs API key)
│   ├── pipeline_support.py                   # Shared utilities (manual overrides, date helpers)
│   └── utils/                               # One-off and maintenance scripts
│       ├── fill_missing_guide_transitions.py  # Rebuild anas-diff-dataset entries from PDFs
│       ├── retry_missing_case_texts.py        # Retry failed HUDOC text downloads
│       ├── build_unresolved_case_review_report.py  # Manual review for unlinked rows
│       ├── stage_hf_dataset_subset.py         # HuggingFace dataset staging
│       └── stage_hf_split_repos.py            # HuggingFace split repo staging
│
├── outputs/
│   ├── case_catalog/                 # Cases extracted from guides + HUDOC enrichment
│   │   ├── cases_catalog.csv         # 7,846 cases, 7,763 HUDOC-matched
│   │   ├── case_guides.csv           # Case × guide membership
│   │   └── audit/                    # HUDOC match reports and unmatched cases
│   ├── citation_diff_cleanup/        # Cleaned citation diff records
│   │   ├── cleaned_citation_diffs.csv
│   │   └── cleaned_diffs_grouped.json
│   ├── case_linked_guide_diffs/      # Core linked dataset
│   │   ├── case_linked_guide_diffs.csv          # 1,004 rows: citation × case × location
│   │   ├── case_linked_guide_diff_paragraphs.csv # 1,537 paragraph-level matches
│   │   └── case_linked_guide_diffs_report.json
│   ├── prototype/                    # Modeling artifacts
│   │   ├── filtered_case_linked_rows.csv        # 1,013 rows with usability flags
│   │   ├── dev_audit_sample.csv                 # 120-row stratified human-audit sample
│   │   ├── retrieval_eval.json                  # BM25 location baseline results
│   │   ├── retrieval_predictions.csv            # Per-row location predictions
│   │   ├── retrieval_ablation.json              # Section ablation results
│   │   ├── retrieval_ablation_predictions.csv   # Per-row ablation predictions
│   │   ├── edit_type_eval.json                  # Edit type classification results
│   │   ├── edit_type_predictions.csv            # Per-row edit type labels
│   │   └── prototype_notes.md                   # Results summary + next steps
│   ├── trigger/                      # Trigger detection outputs
│   │   ├── trigger_eval.json                # AUROC, F1 by model
│   │   └── trigger_predictions.csv          # Per-row trigger scores
│   ├── pipeline/                     # End-to-end pipeline outputs
│   │   ├── pipeline_eval.json               # Chained accuracy results
│   │   └── pipeline_predictions.csv         # Per-row pipeline outcomes
│   ├── generation/                   # Generation pilot outputs (after running with API key)
│   │   ├── generation_pilot.csv             # Per-row generated texts + metrics
│   │   └── generation_pilot_report.json     # Aggregate metrics by subtype
│   ├── negatives/                    # Negative examples for trigger task
│   │   ├── negative_examples.csv            # 3,060 raw negatives across 103 transitions
│   │   ├── negative_examples_audited.csv    # 3,060 rows + audit flags (1,619 clean)
│   │   ├── negative_audit_report.json       # Audit summary: delayed positives, censored
│   │   ├── hard_negative_examples.csv       # 784 importance 1–3 hard candidates (303 clean)
│   │   ├── hard_negative_examples_report.json
│   │   └── combined_clean_negatives.csv     # 1,922 clean negatives (easy + hard)
│   ├── splits/                       # Dataset splits for modeling
│   │   ├── trigger_dataset.csv              # 2,841 rows, random stratified 70/15/15
│   │   └── split_report.json                # Counts by split / label / importance
│   ├── annotation/                   # Human annotation tasks
│   │   └── paragraph_rewrite_annotation.csv # 72 rows, novelty annotation pending
│   └── case_texts/                   # Fetched HUDOC judgment texts (not tracked in git)
│       ├── case_texts_index.csv      # Fetch status per case
│       └── case_texts_report.json    # Coverage report
│
├── docs/
│   ├── annotation_protocol.md        # Two-task annotation guide (novelty + dev audit)
│   └── diff_categorization_schema.md # Four-stage annotation schema
│
├── app.py                            # Streamlit diff viewer
├── dataset_audit.md                  # Data quality audit notes
├── CLAUDE.md                         # Agent context (research framing, guardrails, field docs)
└── requirements.txt
```

---

## Data Setup

The pipeline requires two large inputs that are not tracked in git:

### 1. Guide PDF snapshots (`wayback/`)

~150 versioned PDF snapshots of ECHR guides scraped from the Wayback Machine, organized by guide ID. Contact the team for access or re-scrape from:

```
https://web.archive.org/web/*/https://www.echr.coe.int/Documents/Guide_*
```

### 2. Guide diff dataset (`anas-diff-dataset/`)

Paragraph-level guide diffs in JSON, organized as `anas-diff-dataset/<guide_id>/diff_<from>__<to>.json`. These can be downloaded from HuggingFace:

```bash
# Install huggingface_hub first
pip install huggingface_hub

# Download
python3 - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="lexgenie/echr-guide-citation-diffs",
    repo_type="dataset",
    local_dir="anas-diff-dataset"
)
PY
```

Public viewer: https://huggingface.co/spaces/lexgenie/echr-citation-diff-viewer

---

## Running the Pipeline

All scripts run from the repo root. Steps 1–5 rebuild the core dataset from scratch; steps 6–9 build the prototype on top of it.

```bash
pip install -r requirements.txt

# Step 1–2: Build and enrich case catalog (slow; Step 2 makes HUDOC network calls)
python3 scripts/build_case_catalog_from_guides.py
python3 scripts/enrich_case_catalog_from_hudoc.py

# Step 3–4: Build the core linked diff dataset
# Note: anas-diff-dataset/ must be present (download from HuggingFace above).
# If you add new guide snapshots and need to regenerate missing transitions, run:
#   python3 scripts/utils/fill_missing_guide_transitions.py
python3 scripts/rebuild_citation_diffs_clean.py
python3 scripts/build_case_linked_guide_diffs.py

# Step 5–7: Build the prototype modeling dataset
python3 scripts/build_prototype_dataset.py
python3 scripts/fetch_linked_case_texts.py      # ~10 min, 602 HUDOC requests
python3 scripts/build_prototype_dataset.py       # re-run after fetch to populate case_text fields
python3 scripts/sample_prototype_dev_set.py

# Step 8: Section retrieval baseline
python3 scripts/run_retrieval_baseline.py        # ~5 min

# Step 9: Paragraph-level location baseline
python3 scripts/run_location_baseline.py         # ~5 min

# Step 10–13: Build and audit the negative set, then create the split
python3 scripts/build_negative_examples.py       # ~5 min first run (uses cache after)
python3 scripts/audit_negative_examples.py       # flag delayed positives + right-censored
python3 scripts/collect_hard_negatives.py        # extract importance 1–3 hard negatives
python3 scripts/create_dataset_split.py          # random stratified 70/15/15 split

# Step 14: Section ablation study
python3 scripts/run_retrieval_ablation.py        # ~15 min (BM25 with full text queries)

# Step 15: Trigger detection
python3 scripts/run_trigger_baseline.py          # ~60 sec

# Step 16: Edit type classification
python3 scripts/run_edit_type_baseline.py        # <5 sec

# Step 17: End-to-end pipeline
python3 scripts/run_pipeline_eval.py             # <5 sec

# Step 18: Generation pilot (requires ANTHROPIC_API_KEY)
ANTHROPIC_API_KEY=sk-... python3 scripts/run_generation_pilot.py
```

---

## Core Dataset: `case_linked_guide_diffs.csv`

Each row is one citation-change event (a case added or removed from a guide between two snapshot dates), linked to its HUDOC case record and paragraph-level location in the guide.

Key fields:

| Field | Description |
|---|---|
| `guide_id`, `guide_title` | Which guide |
| `from_snapshot_date`, `to_snapshot_date` | Version transition window |
| `case_key`, `case_name`, `application_numbers` | Case identity |
| `citation_change` | `added` or `removed` |
| `hudoc_importance_level` | `key cases`, `1`, `2`, `3` |
| `link_status` | `linked_paragraphs` (usable) or `no_paragraph_link` |
| `linked_sections` | Pipe-separated guide section paths |
| `linked_change_types` | Pipe-separated paragraph change types (see taxonomy below) |
| `linked_match_strategies` | How the case was linked to paragraphs |
| `pre_text`, `post_text` | Paragraph text before and after the update |

**Dataset statistics:**
- 1,004 citation-change rows across 38 guides
- 843 rows linked to paragraph-level locations (`link_status == linked_paragraphs`)
- 7,763 HUDOC-matched out of 7,846 cases
- 856 rows with full judgment text available

### Paragraph change type taxonomy (`linked_change_types`)

These values are detected automatically by the diff pipeline and appear in the `linked_change_types` field:

| Type | Count (added rows) | Meaning |
|---|---|---|
| `paragraph_added` | 395 | A new paragraph was written to introduce the case |
| `citation_added` | 321 | The case was inserted into an existing citation list; surrounding text unchanged |
| `minor_edit` | 120 | Small text change in the paragraph (punctuation, year, word) |
| `citation_updated` | 79 | An existing citation to this case was refreshed (paragraph number, year, etc.) |
| `reformulation` | 59 | Paragraph was substantively rewritten |
| `section_moved_modified` | 38 | Section reorganised and text changed |
| `citation_removed` | 21 | A citation was removed in the same transition |
| `unchanged` | 11 | Paragraph text did not change — matching artefact |

These roll up into the higher-level edit subtypes used by `run_edit_type_baseline.py` (see Edit Type section below).

---

## Prototype Results

### Trigger: Should this case cause a guide update?

Binary classification. Positives: 919 `added` rows. Negatives: 1,922 clean negatives (1,619 easy level-4 + 303 hard importance 1–3). Split: random stratified 70/15/15 by guide × importance. The results below are from the pre-audit temporal-split eval (`trigger_eval.json`); re-running on the new split is pending.

| Model | AUROC | F1 | Precision | Recall |
|---|---|---|---|---|
| Random | 0.501 | 0.335 | 0.201 | 1.000 |
| Importance level | 0.942 | 0.600 | 0.460 | 0.859 |
| Article overlap | **0.979** | **0.723** | 0.662 | 0.797 |
| Importance + Article overlap | 0.946 | 0.746 | 0.785 | 0.711 |
| BM25 | 0.648 | 0.397 | 0.256 | 0.875 |

Results on the test split (dev-selected threshold, n=638): `article_overlap` AUROC=0.979, F1=0.723; `importance+art` AUROC=0.946, F1=0.746 (prec=0.785, rec=0.711).

Two free metadata signals — whether the case is important and whether it involves the guide's Convention article — nearly solve the trigger problem on the current dataset. However, both signals require post-publication metadata that is only available after human annotation. A model that predicts doctrinal novelty from case text alone, without metadata shortcuts, is the real target.

### Location: BM25 Retrieval Baseline

Task: given a new case, rank guide sections by likelihood of needing an update.

**Unconditional (all 1,004 rows; unlinked rows score 0):**

| Model | hit@1 | hit@3 | MRR |
|---|---|---|---|
| Random baseline | 2.1% | 6.0% | 0.080 |
| Base query (name + app# + citation) | 9.4% | 17.9% | 0.179 |
| **Enriched (+ full judgment text)** | **26.7%** | **41.5%** | **0.374** |

**Conditional (843 linked rows only):**

| Model | hit@1 | hit@3 | MRR |
|---|---|---|---|
| Random | 2.6% | 7.6% | 0.101 |
| Base | 11.8% | 22.5% | 0.225 |
| **Enriched** | **33.6%** | **52.2%** | **0.471** |

**Test split (conditional, n=131):** enriched hit@1 = 33.6%, law hit@1 = **36.6%** / MRR = 0.482. Base query degrades severely on test (5.3% vs 13.1% dev) — new cases have no lexical overlap with pre-update guide text, making full judgment text load-bearing.

**Section ablation (805 rows with case text available):**

| Query | hit@1 | hit@3 | MRR |
|---|---|---|---|
| base_only | 10.9% | 22.4% | 0.221 |
| facts | 19.8% | 36.7% | 0.330 |
| **law** | **28.2%** | **47.1%** | **0.419** |
| operative | 12.2% | 28.8% | 0.260 |
| full_text | 28.1% | 44.5% | 0.410 |

The **law section alone matches full text**. The LAW section heading structure is often labeled directly with the relevant Convention article. Operative provisions add near-zero signal. Gold section in corpus rate: 99%.

### Edit Type: What Kind of Update Is Needed?

Rule-based classifier over 804 linked rows using paragraph-level diff analysis.

| Edit type | n | % |
|---|---|---|
| add_citation | 572 | 71.1% |
| revise_text | 202 | 25.1% |
| remove_citation | 30 | 3.7% |

Subtypes: `new_paragraph` (377), `citation_insert` (189), `doctrinal_rewrite` (88), `paragraph_rewrite` (72), `citation_refresh` (42). The dominant action (47% of all rows) is writing an entirely new paragraph to introduce a case; 23% are surgical citation inserts into existing lists.

The 72 `paragraph_rewrite` rows are heterogeneous — some reflect genuine doctrinal change, others are stylistic reformulations. These are being annotated in `outputs/annotation/paragraph_rewrite_annotation.csv` (see `docs/annotation_protocol.md`).

### End-to-End Pipeline

Chaining `importance+art` trigger → BM25+law-section location → rule-based edit type.

| Split | Trigger F1 | Location hit@1 | Pipeline hit@1 |
|---|---|---|---|
| Dev | 0.736 | 0.336 | 0.210 |
| **Test** | **0.746** | **0.336** | **0.227** |

Pipeline hit@1 = fraction of positive test cases where the system correctly fires the trigger AND ranks the correct section first. A random baseline would achieve ~0.3%.

### Generation: What Update Is Needed? (Pending)

`scripts/run_generation_pilot.py` samples 120 rows across 5 edit subtypes and calls Claude to generate the updated paragraph. Requires `ANTHROPIC_API_KEY`.

```bash
ANTHROPIC_API_KEY=sk-... python3 scripts/run_generation_pilot.py
```

---

## Negative Set

The trigger task requires negative examples — cases published in the same transition window as a guide update but not added to the guide. Three layers of filtering are applied:

1. **Raw negatives** (`negative_examples.csv`): 3,060 rows, sampled from HUDOC (cap 30 per window × 103 windows).
2. **Audited** (`negative_examples_audited.csv`): adds `audit_flag` per row. 1,619 clean; 1,440 right-censored (transition ends ≥ 2025-01-01, outcome unknown); 1 pre-existing; 0 delayed positives.
3. **Hard negatives** (`hard_negative_examples.csv`): 784 importance 1–3, article-overlapping candidates extracted from the existing HUDOC cache. 303 clean; 58 delayed positives (7.4% — confirming that high-importance cases do eventually get added); 447 right-censored.
4. **Combined clean** (`combined_clean_negatives.csv`): 1,619 easy + 303 hard = **1,922 total**. Used for trigger modeling.

The absence of delayed positives in the easy negative set and the 7.4% rate in the hard set together indicate that editorial decisions are temporally decisive at lower importance levels, but that high-importance cases are more likely to be incorporated with a lag.

---

## What's Next

The BM25/rule-based baselines are complete for all three stages. Key remaining work:

### 1. Re-run trigger eval on new split (immediate)
`run_trigger_baseline.py` currently uses the old temporal split and pre-audit negatives. Update it to read `outputs/splits/trigger_dataset.csv` directly.

### 2. Human annotation (blocking for dataset paper)
Two tasks in `docs/annotation_protocol.md`:
- **Task 1**: 72 `paragraph_rewrite` rows in `outputs/annotation/paragraph_rewrite_annotation.csv` — classify novelty yes/no/uncertain. Target κ ≥ 0.7.
- **Task 2**: 120-row dev audit in `outputs/prototype/dev_audit_sample.csv` — validate section links, edit types, and generation feasibility.

### 3. Generation evaluation
Run `scripts/run_generation_pilot.py` with an API key. This is the most novel contribution — no prior work evaluates LLM-generated ECHR guide paragraph updates against editor-written gold.

### 4. Neural retrieval for location
Replace BM25 with a bi-encoder (e.g., `BAAI/bge-base-en`). The BM25 ceiling is ~37% hit@1 (test, law section); neural retrieval should push this higher and would lift pipeline hit@1 proportionally.

### 5. Trigger model with case text
The metadata baselines (importance + article overlap) are near-ceiling for available metadata signals. The next step is a text-based trigger that engages with guide content: contrastive BM25 or a fine-tuned classifier over (case text, guide section) pairs.

### 6. Error analysis by edit subtype
Break trigger and location results down by `new_paragraph` vs. `citation_insert` vs. `doctrinal_rewrite` to characterize where the editorial proxy breaks down.

---

## Related Work

| Paper | Relevance |
|---|---|
| LexGenie (ACL 2025) | Direct predecessor — automates guide generation, no temporal awareness |
| WINELL (arXiv 2508.03728) | Closest analogue in Wikipedia domain |
| ChronosLex / LexTempus | Temporal legal NLP, no guide update generation |
