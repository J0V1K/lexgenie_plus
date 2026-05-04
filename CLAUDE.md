# LexGenie — Agent Context

For use by coding agents. Human-facing documentation is in `README.md`.

---

## Research Goal

Build a pipeline that automatically detects when new ECHR judgments introduce **doctrinal novelty**
significant enough to require updates to ECHR case law guides, and generates those updates.

Three stages: **Trigger** (does this judgment matter?) → **Location** (which section changes?) →
**Generation** (what does the update say?).

The supervision signal is editorial: when ECHR-KS editors add a case to a guide, that is a ground-truth
label that the case was doctrinally significant. Wayback Machine versioned PDFs provide the temporal
dimension that no prior work has exploited.

---

## Project Framing

**What is doctrinal novelty?** A judgment introduces it if it establishes a new legal test, revises
an existing principle, becomes a leading case, or triggers a visible editorial decision to update a guide.

**Why not all judgments qualify:** The system must distinguish substantive change (new principle, new
leading case) from routine editorial change (superseded case removed, formatting updated).

**Prior work gaps:**
- LexGenie (ACL 2025): automates guide generation, no temporal awareness, no case importance ranking
- ChronosLex / LexTempus / AETAS: temporal legal NLP, do not address guide update generation
- WINELL (arXiv 2508.03728): closest analogue in Wikipedia domain — study its operationalization of
  "knowledge change" and where it fails for the legal domain

---

## Decisions Made

- **Citation list diffs as primary training signal**: The *List of Cited Cases* section is uniformly
  formatted across all guides; editorial additions/removals are the strongest proxy for doctrinal relevance.
- **Application number as canonical case ID**: Case names vary across versions; application numbers are
  the stable ECHR identifier and correct key for deduplication.
- **Wayback Machine as temporal corpus**: ECHR-KS does not maintain a public versioned archive; ~150
  PDFs across ~30 guides from 2022–2025 give reasonable temporal coverage.

## Decisions Pending

- **Annotation protocol for change classification**: How to distinguish substantive from routine changes
  in the 1,028 citation-change events. Options: rule-based heuristics (does the case appear in guide
  body?), manual annotation with IAA, or weak supervision from ECHR press releases. See `docs/diff_categorization_schema.md`.
- **Full-text parsing scope**: Citation-list parsing is done. Body-text parsing (needed to understand
  *why* a case was added and for richer generation) is significantly harder — inconsistent PDF formatting,
  section boundary detection. Required for update generation.
- **Primary research framing**: Novelty detection (classification task, cleaner first paper) vs. update
  generation (harder, higher impact). Recommendation: detection first.

---

## Publication Targets

| Venue | Deadline (est.) | Target |
|---|---|---|
| NLLP @ EMNLP 2026 | ~June 2026 | Dataset paper + preliminary novelty detection results |
| JURIX 2026 | ~July 2026 | Alternate venue |
| ICAIL 2027 | ~Feb 2027 | Full system: detection + update generation |

Critical path to NLLP 2026: dataset validation → annotation → baseline model → evaluation write-up.

---

## Key Data Fields

From `outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv` (1,014 rows, core dataset):

| Field | Description |
|---|---|
| `guide_id`, `guide_title` | Which guide |
| `from_snapshot_date`, `to_snapshot_date` | Version transition window |
| `case_key`, `case_name`, `application_numbers` | Case identity |
| `citation_change` | `added` or `removed` |
| `hudoc_importance_level` | `key cases`, `1`, `2`, `3` |
| `link_status` | `linked_paragraphs` (usable) or `no_paragraph_link` |
| `linked_sections` | Pipe-separated guide section paths |
| `linked_change_types` | Pipe-separated paragraph change types |
| `linked_match_strategies` | How the case was linked to paragraphs |
| `pre_text`, `post_text` | Paragraph text before and after the update |

From `outputs/case_linked_guide_diffs/case_linked_guide_diff_paragraphs.csv` (1,489 rows):

| Field | Description |
|---|---|
| `paragraph_match_strategies` | Match confidence signal |
| `change_type` | Paragraph-level change category |
| `section_path`, `section_title` | Guide section location |
| `para_num_a`, `para_num_b` | Paragraph numbers before/after |
| `citations_added`, `citations_removed` | Per-paragraph citation deltas |
| `text_a`, `text_b` | Paragraph text before and after |

**Usability flags** (in `outputs/prototype/filtered_case_linked_rows.csv`, 805 rows):
- `usable_for_relevance`: `link_status == linked_paragraphs`
- `usable_for_location`: above + `linked_sections` non-empty
- `usable_for_edit_type`: above + `linked_change_types` non-empty
- `usable_for_generation`: above + `citation_change == added` + `post_text` non-empty

---

## Data Limitations

- 209 rows remain `no_paragraph_link` — concentrated in guides: Article 3, Article 10,
  Article 6 Criminal, Prisoners' rights, Article 34/35
- Some transitions were reconstructed from PDF body parsing (not original annotation) — approximate
- Many unresolved rows are citation-list-only or weakly localized
- For first-pass supervision: use only `linked_paragraphs` rows; do not use `no_paragraph_link` rows
  as gold location labels

---

## Guardrails

- Do not perform unnecessary HUDOC passes for documents already enriched.
- Reuse the existing case catalog unless there is a concrete reason to refresh unresolved rows.
- Do not infer fields not present in source data.
- Do not use `no_paragraph_link` rows as gold location labels.
- Do not present generated text as authoritative legal content.
- Manual case overrides live in `data/manual_case_overrides.csv` — apply these before comparing
  case identities, not after.

---

## Resources

- **Guide PDFs**: Wayback Machine (scraped by Anas Belfathi; see README for re-scrape instructions)
- **Citation diff dataset**: https://huggingface.co/datasets/lexgenie/echr-guide-citation-diffs
- **Diff viewer**: https://huggingface.co/spaces/lexgenie/echr-citation-diff-viewer
- **HUDOC** (ECHR judgment database): https://hudoc.echr.coe.int
- **ECHR-OD** (pre-built ML datasets by Article): https://echr-opendata.eu
- **Anchor paper (WINELL)**: https://arxiv.org/pdf/2508.03728
- **LexGenie (ACL 2025)**: Direct predecessor system
