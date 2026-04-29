# LexGenie — ECHR Guide Update Pipeline

Research pipeline for automatically detecting when new European Court of Human Rights (ECHR) judgments require updates to existing doctrinal guides, and generating those updates.

**Target venues**: NLLP @ EMNLP 2026, JURIX 2026, ICAIL 2027

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

This repository contains a working end-to-end prototype covering all three pipeline stages. The core dataset links 1,014 citation-change events across 38 guides to their HUDOC case records, paragraph-level guide sections, and full judgment texts. The pipeline is also augmented with 3,090 hard negatives (judgments published in each transition window but not added to any guide).

| Stage | Model | Metric | All | Test |
|---|---|---|---|---|
| **Trigger** | Importance + Article overlap | F1 | 0.738 | **0.854** |
| **Location** | BM25 + law section | hit@1 / MRR | 0.282 / 0.419 | 0.327 / 0.439 |
| **Pipeline** | Trigger → Location chain | hit@1 | 0.194 | **0.255** |
| Edit type | Rule-based (paragraph-level) | distribution | — | — |

Generation evaluation (edit step) requires `ANTHROPIC_API_KEY` — see `scripts/run_generation_pilot.py`.

See `outputs/prototype/prototype_notes.md` for full results.

---

## Repository Structure

```
lexgenie/
├── scripts/                          # All pipeline scripts (run in order below)
│   ├── build_case_catalog_from_guides.py     # Step 1: extract cases from guide PDFs
│   ├── enrich_case_catalog_from_hudoc.py     # Step 2: enrich with HUDOC metadata
│   ├── rebuild_citation_diffs_clean.py       # Step 3: clean citation diff records
│   ├── fill_missing_guide_transitions.py     # Step 4: fill gaps in diff JSON files
│   ├── build_case_linked_guide_diffs.py      # Step 5: link diffs to cases + paragraphs
│   ├── build_prototype_dataset.py            # Step 6: filter to usable modeling rows
│   ├── fetch_linked_case_texts.py            # Step 7: fetch full judgment text from HUDOC
│   ├── sample_prototype_dev_set.py           # Step 8: stratified dev audit sample
│   ├── run_retrieval_baseline.py             # Step 9: BM25 section retrieval (location)
│   ├── build_negative_examples.py            # Step 10: mine hard negatives from HUDOC
│   ├── run_retrieval_ablation.py             # Step 11: section ablation study
│   ├── run_trigger_baseline.py               # Step 12: trigger detection evaluation
│   ├── run_edit_type_baseline.py             # Step 13: edit type classification
│   ├── run_pipeline_eval.py                  # Step 14: end-to-end pipeline accuracy
│   └── run_generation_pilot.py               # Step 15: LLM paragraph generation (needs API key)
│
├── outputs/
│   ├── case_catalog/                 # Cases extracted from guides + HUDOC enrichment
│   │   ├── cases_catalog.csv         # 7,846 cases, 7,759 HUDOC-matched
│   │   ├── case_guides.csv           # Case × guide membership
│   │   └── audit/                    # HUDOC match reports and unmatched cases
│   ├── citation_diff_cleanup/        # Cleaned citation diff records
│   │   ├── cleaned_citation_diffs.csv
│   │   └── cleaned_diffs_grouped.json
│   ├── case_linked_guide_diffs/      # Core linked dataset
│   │   ├── case_linked_guide_diffs.csv          # 1,014 rows: citation × case × location
│   │   ├── case_linked_guide_diff_paragraphs.csv # 1,489 paragraph-level matches
│   │   └── case_linked_guide_diffs_report.json
│   ├── prototype/                    # Modeling artifacts
│   │   ├── filtered_case_linked_rows.csv        # 805 usable rows with flags
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
│   ├── negatives/                    # Hard negative examples for novelty detection
│   │   ├── negative_examples.csv            # 3,090 negatives across 103 transitions
│   │   └── negative_examples_report.json    # Coverage stats
│   └── case_texts/                   # Fetched HUDOC judgment texts (not tracked in git)
│       ├── case_texts_index.csv      # Fetch status per case
│       └── case_texts_report.json    # Coverage report
│
├── docs/
│   └── diff_categorization_schema.md # Four-stage annotation schema
│
├── app.py                            # Streamlit diff viewer
├── project_context.md                # Full research framing document
├── prototype_handoff.md              # Handoff doc for the next agent / collaborator
├── dataset_audit.md                  # Data quality audit notes
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

# Step 3–5: Build the core linked diff dataset
python3 scripts/rebuild_citation_diffs_clean.py
python3 scripts/fill_missing_guide_transitions.py
python3 scripts/build_case_linked_guide_diffs.py

# Step 6–8: Build the prototype modeling dataset
python3 scripts/build_prototype_dataset.py
python3 scripts/fetch_linked_case_texts.py      # ~10 min, 602 HUDOC requests
python3 scripts/build_prototype_dataset.py       # re-run after fetch to populate case_text fields
python3 scripts/sample_prototype_dev_set.py

# Step 9: Evaluate retrieval baseline
python3 scripts/run_retrieval_baseline.py        # ~5 min

# Step 10: Mine hard negatives (HUDOC API calls, uses cache after first run)
python3 scripts/build_negative_examples.py       # ~5 min first run

# Step 11: Section ablation study
python3 scripts/run_retrieval_ablation.py        # ~15 min (BM25 with full text queries)

# Step 12: Trigger detection
python3 scripts/run_trigger_baseline.py          # ~60 sec

# Step 13: Edit type classification
python3 scripts/run_edit_type_baseline.py        # <5 sec

# Step 14: End-to-end pipeline
python3 scripts/run_pipeline_eval.py             # <5 sec

# Step 15: Generation pilot (requires ANTHROPIC_API_KEY)
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
| `hudoc_importance_level` | (key cases, 1, 2, 3) |
| `link_status` | `linked_paragraphs` (usable) or `no_paragraph_link` |
| `linked_sections` | Pipe-separated guide section paths |
| `linked_change_types` | Pipe-separated paragraph change types |
| `linked_match_strategies` | How the case was linked to paragraphs |
| `pre_text`, `post_text` | Paragraph text before and after the update |

**Dataset statistics:**
- 1,014 citation-change rows across 38 guides
- 805 rows linked to paragraph-level locations (`link_status == linked_paragraphs`)
- 617 unique cases, 7,759 HUDOC-matched out of 7,846
- 706 rows with full judgment text available

---

## Prototype Results

### Trigger: Should this case cause a guide update?

Binary classification over 3,895 rows (805 positives + 3,090 hard negatives).

| Model | AUROC | F1 | Precision | Recall |
|---|---|---|---|---|
| Random | 0.501 | 0.343 | 0.207 | 1.000 |
| Importance level | 0.961 | 0.705 | 0.603 | 0.848 |
| Article overlap | 0.978 | 0.714 | 0.702 | 0.726 |
| **Importance + Article overlap** | **0.956** | **0.738** | **0.837** | **0.661** |

On the test set (n=530), `importance+art` reaches F1=**0.854** (prec=0.917, rec=0.800). Two free metadata signals — whether the case is important (Grand Chamber or key case) and whether it involves the guide's Convention article — nearly solve the trigger problem.

### Location: BM25 Retrieval Baseline

Task: given a new case, rank guide sections by likelihood of needing an update.

**Unconditional (all 1,014 rows; 209 unlinked rows score 0):**

| Model | hit@1 | hit@3 | MRR |
|---|---|---|---|
| Random baseline | 2.5% | 7.2% | 0.089 |
| Base query (name + app# + citation) | 9.5% | 17.9% | 0.179 |
| **Enriched (+ full judgment text)** | **24.9%** | **37.9%** | **0.346** |

**Conditional (805 linked+evaluable rows only):**

| Model | hit@1 | hit@3 | MRR |
|---|---|---|---|
| Random | 3.1% | 9.1% | 0.113 |
| Base | 11.9% | 22.6% | 0.226 |
| **Enriched** | **31.4%** | **47.7%** | **0.436** |

**Temporal split (conditional, enriched):** dev hit@1 = 31.2%, test hit@1 = 32.7%. Base query degrades severely on test (3.6% vs 13.2% dev) — new cases have no lexical overlap with pre-update guide text, making full judgment text load-bearing.

**Section ablation (660 rows with case text):**

| Query | hit@1 | hit@3 | MRR |
|---|---|---|---|
| base_only | 10.9% | 22.7% | 0.223 |
| facts | 21.7% | 40.2% | 0.355 |
| **law** | **32.0%** | **52.9%** | **0.464** |
| operative | 12.4% | 30.6% | 0.270 |
| full_text | 31.8% | 49.7% | 0.453 |

The **law section alone beats full text** — THE LAW section's legal analysis aligns directly with doctrinal guide sections. Operative provisions add near-zero signal. Gold section in corpus rate: 99%.

### Edit Type: What Kind of Update Is Needed?

Rule-based classifier over 805 linked rows using paragraph-level diff analysis.

| Edit type | n | % | Median len ratio |
|---|---|---|---|
| add_citation | 572 | 71.1% | 1.37× |
| revise_text | 203 | 25.2% | 1.19× |
| remove_citation | 30 | 3.7% | 1.00× |

Subtypes: `new_paragraph` (377), `citation_insert` (189), `doctrinal_rewrite` (88), `paragraph_rewrite` (72), `citation_refresh` (43). The dominant action (47% of all rows) is writing an entirely new paragraph to introduce a case; 23% are surgical citation inserts into existing lists.

### End-to-End Pipeline

Chaining `importance+art` trigger → BM25+law-section location → rule-based edit type.

| Split | Trigger F1 | Location hit@1 | Pipeline hit@1 |
|---|---|---|---|
| Dev | 0.719 | 0.282 | 0.184 |
| **Test** | **0.854** | **0.327** | **0.255** |

Pipeline hit@1 = fraction of positive test cases where the system correctly fires the trigger AND ranks the correct section first. A random baseline would achieve ~0.3%.

### Generation: What Update Is Needed? (Pending)

`scripts/run_generation_pilot.py` samples 120 rows across 5 edit subtypes and calls Claude to generate the updated paragraph. Requires `ANTHROPIC_API_KEY`.

```bash
ANTHROPIC_API_KEY=sk-... python3 scripts/run_generation_pilot.py
```

---

## What's Next

The BM25/rule-based baselines are complete for all three stages. Key remaining work:

### 1. Generation evaluation (blocking)
Run `scripts/run_generation_pilot.py` with an API key. This is the most novel contribution — no prior work evaluates LLM-generated ECHR guide paragraph updates against editor-written gold.

### 2. Human audit of `dev_audit_sample.csv`
Fill the `gold_*` columns in `outputs/prototype/dev_audit_sample.csv` (120 rows). Needed to validate the dataset labels before claiming evaluation validity.

### 3. Neural retrieval for location
Replace BM25 with a bi-encoder (e.g., `BAAI/bge-base-en`). The BM25 ceiling is 32% hit@1; neural retrieval should push this meaningfully higher and would make the pipeline hit@1 follow.

### 4. Paragraph-level location (sub-section retrieval)
Given the correct guide section, rank paragraphs within it. Uses `case_linked_guide_diff_paragraphs.csv` (1,489 paragraph-level matches). This is the missing granularity between section retrieval and generation.

### 5. Trigger model with case text
The BM25 trigger baseline is weak (AUROC=0.547) because new cases have no textual overlap with pre-update guide text. With case text available for 660/805 positives and ~0 negatives, a contrastive BM25 approach or a fine-tuned classifier would close this gap.

---

## Related Work

| Paper | Relevance |
|---|---|
| LexGenie (ACL 2025) | Direct predecessor — automates guide generation, no temporal awareness |
| WINELL (arXiv 2508.03728) | Closest analogue in Wikipedia domain |
| ChronosLex / LexTempus | Temporal legal NLP, no guide update generation |


