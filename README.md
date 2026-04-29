# LexGenie — ECHR Guide Update Pipeline

Research pipeline for automatically detecting when new European Court of Human Rights (ECHR) judgments require updates to existing doctrinal guides, and generating those updates.

**Target venues**: NLLP @ EMNLP 2026, JURIX 2026, ICAIL 2027

---

## Problem

The ECHR Knowledge Sharing Platform (ECHR-KS) publishes ~30 doctrinal guides that summarize case law by Convention article. These guides are updated weekly as new judgments are handed down. Currently, editors must manually monitor new case law, decide which cases are doctrinally significant, locate the relevant guide section, and rewrite it.

This project builds a pipeline that does this automatically:

1. **Detect** whether a new judgment introduces doctrinal novelty significant enough to require a guide update
2. **Locate** which guide and section needs updating
3. **Generate** the updated paragraph

---

## Key Insight

ECHR-KS's editorial decisions are a ground-truth signal. When editors add a case to a guide, that choice reflects a deliberate judgment that the case matters doctrinally. Weekly versioned guide PDFs scraped from the Wayback Machine provide a temporal supervision signal no prior work has exploited.

---

## Current State

This repository contains a working prototype pipeline covering steps 1 and 2. The core dataset (`case_linked_guide_diffs.csv`) links 1,014 citation-change events across 38 guides to their HUDOC case records and paragraph-level locations. A BM25 retrieval baseline using full judgment text reaches **hit@1 35.8%** and **MRR 0.485** on the 660 cases with text available — up from 12% / 0.23 without case text.

See `outputs/prototype/prototype_notes.md` for full results and `prototype_handoff.md` for the next-agent handoff document.

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
│   └── run_retrieval_baseline.py             # Step 9: BM25 retrieval evaluation
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
│   │   ├── filtered_case_linked_rows.csv   # 805 usable rows with flags
│   │   ├── dev_audit_sample.csv            # 120-row stratified human-audit sample
│   │   ├── retrieval_eval.json             # BM25 retrieval results
│   │   ├── retrieval_predictions.csv       # Per-row retrieval predictions
│   │   └── prototype_notes.md              # Results summary + next steps
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
| `hudoc_importance_level` | 1 (Grand Chamber) → 4 (low importance) |
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

## Prototype Results: BM25 Retrieval Baseline

Task: given a new case, rank guide sections by likelihood of needing an update.

| Condition | hit@1 | hit@3 | hit@10 | MRR |
|---|---|---|---|---|
| Base query (name + app# + citation text) | 11.9% | 22.6% | 46.3% | 0.226 |
| + full judgment text (660 rows with text) | **35.8%** | **53.9%** | **75.6%** | **0.485** |
| All 805 rows enriched where available | 31.4% | 47.7% | 69.8% | 0.436 |

Full judgment text provides ~3× hit@1 improvement. The ceiling (gold section in corpus) is 99%.

---

## What's Next

The pipeline has three major gaps before the first paper submission:

### 1. Human audit of `dev_audit_sample.csv`
Fill in the `gold_*` annotation columns in `outputs/prototype/dev_audit_sample.csv`. 120 rows, stratified across 38 guides. This is the first blocking dependency.

### 2. Location and edit-type baselines
- **Location baseline** (`run_location_baseline.py`): given a section, rank paragraphs by similarity. Uses `case_linked_guide_diff_paragraphs.csv`.
- **Edit-type baseline** (`run_edit_type_baseline.py`): classify each event as `add`, `remove`, or `revise` from `citation_change` + `linked_change_types`.

### 3. Neural retrieval and update generation
- Replace BM25 with a bi-encoder (e.g., `BAAI/bge-base-en`) for section retrieval
- Implement an LLM-based paragraph rewriter conditioned on: case metadata + pre_text + section context → post_text
- Evaluate generation with citation inclusion rate, case-name inclusion, and human review

See `prototype_handoff.md` for detailed task specifications.

---

## Related Work

| Paper | Relevance |
|---|---|
| LexGenie (ACL 2025) | Direct predecessor — automates guide generation, no temporal awareness |
| WINELL (arXiv 2508.03728) | Closest analogue in Wikipedia domain |
| ChronosLex / LexTempus | Temporal legal NLP, no guide update generation |

---

## Diff Viewer

To browse citation changes interactively:

```bash
streamlit run app.py
```

Requires HuggingFace access to `lexgenie/echr-guide-citation-diffs` (private dataset).

---

