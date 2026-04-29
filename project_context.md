# ECHR Doctrinal Novelty Detection — Project Context Document
*For use as LLM context. Last updated: March 2026.*

---

## 1. Project Goal

The project aims to build a system that automatically detects when new European Court of Human Rights (ECHR) judgments introduce **doctrinal novelty** significant enough to require updates to existing ECHR case law guides, and then triggers or generates those updates.

This is a novel research problem. No existing system does this end-to-end. Adjacent work (ChronosLex, LexTempus, AETAS) addresses temporal legal reasoning but not the full pipeline of: new judgment → novelty signal → guide update.

The project builds directly on **LexGenie** (Grabmair's group, TU Munich; ACL 2025 Industry Track), which automates legal guide generation but has no temporal awareness, no case importance ranking, and no incremental update capability. Closing these gaps is the core contribution.

---

## 2. Theoretical Framing

### What is "doctrinal novelty"?
A judgment introduces doctrinal novelty if it:
- Establishes a new legal test or standard not previously present in the guides
- Revises or qualifies an existing principle (e.g., narrowing the scope of a right)
- Becomes a new leading case that displaces or supplements prior ones
- Triggers a visible editorial decision by the ECHR Knowledge Sharing Platform (ECHR-KS) to update a guide

Crucially, not all new judgments are doctrinally novel. The system must distinguish:
- **Substantive change**: a new principle, standard, or leading case
- **Routine editorial change**: a superseded case removed, a procedural citation added, formatting updated

### Why is the ECHR setting ideal?
ECHR-KS publishes versioned guide PDFs on a weekly update cadence. This provides a **temporal ground-truth signal** that prior work has not exploited: when the ECHR's own editors update a guide, that editorial decision serves as a proxy label for "this judgment mattered doctrinally."

---

## 3. Data Infrastructure (Current State)

### 3.1 Raw Data
- **~150 PDFs** scraped from the Wayback Machine across **~30 ECHR guides**, spanning **2022–2025**
- Uploaded to Google Drive by team member Anas Belfathi
- Corrupt and duplicate snapshots removed prior to processing

### 3.2 Citation Diff Dataset ✅ COMPLETE
The most important completed artifact. Pipeline:
1. Extract the *List of Cited Cases* section from each guide PDF (consistently formatted across all guides)
2. Diff consecutive guide versions using **application number** as the canonical case identifier
3. Result: **102 diffs**, **1,028 citation change events** (added or removed cases)

Where to access:
- Dataset: https://huggingface.co/datasets/lexgenie/echr-guide-citation-diffs
- Viewer: https://huggingface.co/spaces/lexgenie/echr-citation-diff-viewer
- Code: https://huggingface.co/datasets/lexgenie/echr-guide-citation-diffs/tree/main/code

### 3.3 What the citation diffs capture
Each diff record represents a single version transition for a single guide and contains:
- Which cases were **added** to the cited cases list
- Which cases were **removed** from the cited cases list
- Links to the actual PDFs for source verification

### 3.4 What is NOT yet done
- **Full text parsing** of guide body sections (beyond the citation list)
- **Alignment of citation changes to specific new judgments** published in the intervening period
- **Classification labels** distinguishing substantive from routine changes
- **Annotation protocol** for labeling change events

---

## 4. Key Related Work

| Paper | Relevance | Gap |
|---|---|---|
| **WINELL** (arXiv 2508.03728) | Closest analogue — Wikipedia-based knowledge update detection | Not legal-domain; different update mechanism |
| **LexGenie** (ACL 2025) | Direct predecessor — automates ECHR guide generation | No temporal awareness, no incremental updates |
| **ChronosLex / LexTempus / AETAS** | Temporal legal NLP | Don't address guide update generation |
| **ROME / MEMIT / GRACE / WISE** | Knowledge editing for LLMs | Model-level edits, not document-level guide updates |
| **Continual Learning (ACM CSUR 2025)** | Plasticity-stability tradeoffs | Broader than needed; model editing is one component |

**Critical note on WINELL**: This paper (arXiv 2508.03728) is working toward a very similar objective in the Wikipedia domain. The team should closely examine: (a) how WINELL defines and operationalizes "knowledge change," (b) its update trigger mechanism, and (c) where it falls short for the ECHR domain. Its framing should directly inform how doctrinal novelty is operationalized here.

---

## 5. Decisions Made

### ✅ Use citation list diffs as the primary training signal
**Reasoning**: The *List of Cited Cases* section is consistently formatted across all guides, making automated extraction reliable. When the ECHR's own editors add or remove a case citation, this reflects a deliberate editorial judgment — a strong proxy for doctrinal relevance. This is the most tractable entry point into the novelty signal problem.

### ✅ Use Wayback Machine snapshots as the temporal corpus
**Reasoning**: ECHR-KS publishes guide updates weekly, but does not maintain a public versioned archive. The Wayback Machine provides the best available approximation of historical guide states. ~150 PDFs across ~30 guides gives reasonable temporal coverage from 2022–2025.

### ✅ Use application number as canonical case identifier
**Reasoning**: Case names are inconsistent across guide versions (formatting variation, abbreviation). Application numbers are the stable, canonical ECHR identifier and the correct key for deduplication and diffing.

---

## 6. Decisions Pending

### ❓ Primary research framing: novelty detection vs. update generation
**The choice**:
- **(A) Novelty detection first**: Given a new ECHR judgment, does it introduce doctrinal novelty? This is a classification/ranking task directly supported by the citation diff dataset.
- **(B) Update generation first**: Given detected novelty, can the guide text be automatically updated? This is a generation task requiring full-text parsing and is harder to evaluate.

**Recommendation**: Option A is the cleaner first paper. The citation diff dataset provides natural supervision, the evaluation is more tractable (precision/recall on citation change prediction), and it maps cleanly to a single NLLP or JURIX submission. Option B can follow as a second paper.

### ❓ Annotation protocol for change classification
**The choice**: How to distinguish substantive doctrinal changes from routine editorial ones in the 1,028 citation change events?

**Current schema draft**: `docs/diff_categorization_schema.md`

**Options under consideration**:
- Rule-based heuristics (e.g., is the added case cited in the guide body? does it appear in the holdings section?)
- Manual annotation by team members with legal background
- Weak supervision using ECHR press releases or case importance scores

**Blocking dependency**: This decision must be made before annotation begins. The team should align on labeling criteria explicitly.

### ❓ Full-text parsing scope
**The choice**: Should the system parse only citation lists (done), or also the body text of guide sections?

**Why it matters**: Citation diffs tell you *that* something changed but not *why*. To understand doctrinal significance, the system needs to parse the surrounding text — which sections were modified, which cases are now discussed in the body vs. merely listed.

**Trade-off**: Full-text parsing is significantly harder (inconsistent PDF formatting, section boundary detection) but is likely necessary for the update generation task (Option B above) and for richer novelty characterization.

---

## 7. Immediate Next Steps (Prioritized)

### Step 1 — Validate the citation diff dataset [URGENT, blocking]
Before any modeling work, the team must spot-check the HuggingFace viewer for:
- Cases that appear incorrectly in added/removed lists
- Obvious cases missing from a diff
- Guides where the extracted data looks incomplete

The viewer links directly to source PDFs for verification. This step is blocking because the entire downstream pipeline depends on data quality.

### Step 2 — Classify citation change events [SHORT TERM]
Once the dataset is validated, label the 1,028 change events as substantive vs. routine. This requires:
- Finalizing the annotation protocol (see pending decision above)
- Dividing annotation work across the team
- Computing inter-annotator agreement on a pilot batch before full annotation

### Step 3 — Align citation changes to new judgments [SHORT TERM, parallel]
For each diff (version A → version B of a guide), identify which ECHR judgments were published in the intervening period. This alignment is required to frame the problem as: "given new judgment J, predict whether it will cause a citation change in guide G."

Data source: ECHR-OD and HUDOC provide judgment metadata with publication dates, enabling this alignment.

### Step 4 — Full-text PDF parsing [MEDIUM TERM]
Extend parsing beyond citation lists to guide body sections. Required for:
- Understanding *why* a case was added/removed (which principle it relates to)
- Eventual update generation
- Richer feature extraction for novelty detection

### Step 5 — Baseline novelty detection model [MEDIUM TERM]
Once Steps 1–3 are complete, implement a baseline model that, given a new judgment, predicts whether it will cause a citation change in any guide. Evaluation: precision/recall on held-out time periods.

---

## 8. Publication Targets and Timeline

| Venue | Deadline (est.) | Target contribution |
|---|---|---|
| **NLLP @ EMNLP 2026** | ~June 2026 | Dataset paper + preliminary novelty detection results |
| **JURIX 2026** | ~July 2026 | Alternate venue for preliminary results |
| **ICAIL 2027** | ~Feb 2027 | Full system: detection + update generation |
| **CS&Law 2027** | TBD | If stronger legal theory contributions develop |

**Critical path to NLLP 2026**: Dataset validation → annotation → baseline model → evaluation write-up. This is achievable by June 2026 if annotation begins in April.

---

## 9. Resources

- **Guide PDFs**: Google Drive (scraped by Anas Belfathi)
- **Citation diff dataset**: https://huggingface.co/datasets/lexgenie/echr-guide-citation-diffs
- **Diff viewer**: https://huggingface.co/spaces/lexgenie/echr-citation-diff-viewer
- **HUDOC** (ECHR judgment database): https://hudoc.echr.coe.int
- **ECHR-OD** (pre-built ML datasets by Article): https://echr-opendata.eu
- **ECHR-KS** (weekly guide updates): ECHR Knowledge Sharing Platform
- **echr-extractor** library: citation network generation from HUDOC
- **Anchor paper (WINELL)**: https://arxiv.org/pdf/2508.03728
- **LexGenie (ACL 2025)**: Direct predecessor system

---

*End of context document.*
