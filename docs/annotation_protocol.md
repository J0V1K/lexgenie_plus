# Annotation Protocol

Two tasks, one sitting. Both require reading ECHR guide text and judgment citations.
Expected time: 2–3 hours for both tasks combined.

---

## Task 1 — Paragraph-rewrite novelty classification

**File**: `outputs/annotation/paragraph_rewrite_annotation.csv` (72 rows)

Each row is a case that was added to an ECHR guide in a transition window where the
associated paragraph was rewritten (not newly created, not just a citation insert). Your
job is to decide whether the rewrite reflects genuine doctrinal content introduced by
the cited case, or is a structural/stylistic change with no substantive legal content.

### Decision question

> Does this paragraph change introduce or refine a legal rule, test, or principle that
> is directly traceable to the cited case's holding — or is it a structural, stylistic,
> or citation-formatting change with no new doctrinal content?

### Criteria

**Mark `yes` (novelty) when:**
- A new legal rule, clarification, or test is stated and the cited case is the direct
  trigger for that addition.
- A new example of an established principle is added, expanding the scope or application
  of the doctrine.
- Existing text is revised to reflect a shift in the Court's approach or a refinement of
  a prior principle, attributable to the cited case.

**Mark `no` (non-novelty) when:**
- The only change is formatting: adding years to citations, standardising punctuation,
  correcting a reference number.
- The paragraph is restructured or reordered without any change in legal content.
- The cited case is inserted into an existing enumeration or list with no surrounding
  text change.
- The pre-text and post-text are substantively identical on close reading.

**Mark `uncertain` when:**
- The change is ambiguous — a sentence is added but its doctrinal significance is unclear.
- The cited case is mentioned but the relationship to the rewrite is indirect.

### Worked examples

**YES — Orhan v. Türkiye (dec.), Article 34/35, "Purpose of the rule"**
The paragraph previously ended with a general statement about transitional provisions.
The new version adds: *"If the final decision… was taken before the entry into force of
Protocol No. 15 but notified to the applicant after 1 August 2021, the applicable
time-limit is still that of six months; however, it starts to run from the day following
the notification."* This is a new legal rule about the transitional period, directly
sourced from the cited case's holding.

**NO — Mura v. Poland (dec.), Article 10, "Absence of significant disadvantage"**
The paragraph is identical except that years are added to three existing citations
(`Sylka v. Poland (dec.), §§ 25-39` → `Sylka v. Poland (dec.), 2014, §§ 25-39`). No
substantive content change. The cited case was already in the paragraph before the
transition.

**YES — BCR Banca v. Romania (dec.), Article 1 Protocol 1, "Legitimate expectations"**
One sentence is appended: *"Similarly, no legitimate expectation arises in relation to
State aid unlawfully distributed to private individuals via a building society (BCR
Banca… 2024, §§ 127-139)."* A new doctrinal example is added, traceable to that case.

### Output columns

Fill in these three columns for each row:

| Column | Values | Notes |
|---|---|---|
| `annotator_A_novelty` | `yes` / `no` / `uncertain` | Your individual judgment before discussion |
| `annotator_B_novelty` | `yes` / `no` / `uncertain` | Second annotator's individual judgment |
| `adjudicated_novelty` | `yes` / `no` / `uncertain` | Final agreed label after discussion |
| `notes` | free text | Required for `uncertain`; useful for any disagreement |

Each annotator fills their own column independently before comparing.

---

## Task 2 — Dev audit gold validation

**File**: `outputs/prototype/dev_audit_sample.csv` (120 rows)

Each row is a citation-change event (case added or removed from a guide) drawn from a
stratified sample of the core dataset. The automated pipeline has predicted the linked
guide section, the edit type, and various usability flags. Your job is to validate these
predictions and flag rows that should be excluded from the benchmark.

### Output columns

Fill in these columns for each row:

**`gold_use_row`** — `yes` / `no`

Should this row be included in the final benchmark? Mark `no` if:
- The section link is clearly wrong (the case does not appear in the linked section).
- The pre/post texts are garbled, truncated, or clearly from the wrong paragraph.
- The case identity is mismatched (the citation text names a different case than
  `case_name`).
- The transition pair makes no sense (same snapshot date for before and after).

**`gold_link_correct`** — `yes` / `no` / `partial`

Does `linked_sections` correctly identify where the case appears in the guide?
- `yes`: the section path is correct and the paragraph is the right one.
- `partial`: the section is approximately right but the specific paragraph is off, or
  one of multiple linked sections is correct.
- `no`: the linked section does not contain the case or is from the wrong part of the
  guide.

**`gold_section`** — free text (or blank if `gold_link_correct` is `yes`)

If the link is wrong or partial, write the correct section path or title. Leave blank
if `gold_link_correct` is `yes`.

**`gold_edit_type`** — categorical

What is the actual type of editorial change? Choose the best fit:

| Label | Meaning |
|---|---|
| `new_paragraph` | A new paragraph is written to introduce the case |
| `citation_insert` | The case is inserted into an existing citation list, no rewrite |
| `doctrinal_rewrite` | Existing doctrine is substantively revised |
| `paragraph_rewrite_novelty` | Paragraph rewritten; change is doctrinally substantive |
| `paragraph_rewrite_routine` | Paragraph rewritten; change is stylistic or formatting |
| `citation_refresh` | An existing citation is updated (year, paragraph number, etc.) |
| `citation_remove` | A case is removed from the guide |
| `other` | Does not fit any of the above; explain in `notes` |

**`gold_generation_feasible`** — `yes` / `no`

Could a model realistically generate `post_text` given `pre_text` and the case? Mark
`yes` if: the post_text is non-empty, the change is substantive, and the edit is
self-contained. Mark `no` if: pre and post texts are nearly identical, the change is
purely a citation removal, or the post_text is truncated or garbled.

**`notes`** — free text

Required when `gold_use_row` is `no` or `gold_link_correct` is `no`/`partial`.
Encouraged for any row where judgment was difficult. Record the reason for exclusion,
the correct section if known, or any data quality observation.

---

## Adjudication protocol

1. Both annotators complete their columns **independently** before comparing.
2. After both are done, compare row by row. Agreement requires no further action.
3. For any disagreement: each annotator states their reasoning briefly.
4. Discuss and attempt consensus. Update `adjudicated_novelty` (Task 1) or the gold
   columns (Task 2) with the agreed label.
5. If consensus is not reached after discussion, flag the row with `notes: DISPUTED`
   and bring it to Javokhir for a final call.
6. Record inter-annotator agreement (simple % agreement and Cohen's κ) across both
   tasks before finalising. Target κ ≥ 0.7 for the benchmark to be credible.

---

## Practical notes

- Read `pre_text` and `post_text` carefully — many differences are subtle.
- For Task 1, the key question is always: *would this paragraph have changed if the
  cited case did not exist?* If no, mark `yes`.
- For Task 2, when in doubt about `gold_use_row`, mark `no` and explain in `notes`.
  It is better to exclude a borderline row than to include a noisy label.
- Do not consult each other until both have independently completed their columns.
