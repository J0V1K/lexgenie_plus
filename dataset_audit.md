# ECHR Citation Diff Dataset Audit

Date: 2026-03-10
Dataset snapshot: `ca6b6a0eb67aa4c75bc2630f2ccda1cee97652a1`
Space snapshot: `14812484413e71f61cf6ffeca707eaa8fbcbcadd`

## Scope

This audit covered:

- full-dataset integrity checks on `citation_diffs.csv` and `diffs_grouped.json`
- targeted PDF validation against source snapshots from the private Hugging Face dataset
- focused inspection of likely failure modes: old citations without application numbers, wrapped lines, and malformed citation strings

## High-level result

The dataset is structurally sound and largely usable, but it is not clean enough to treat every citation change as a real doctrinal event without a normalization pass.

Good news:

- `citation_diffs.csv` and `diffs_grouped.json` match exactly
- row counts match the project context document: `102` grouped diffs and `1,028` flat citation-change rows
- guide ID/title mapping is consistent
- no duplicate flat rows were found

Main issue:

- at least `21` rows are suspicious based on a combination of PDF validation and heuristic checks
- the confirmed problems are concentrated in old citations without application numbers and in wrapped/truncated lines

## Automated integrity checks

- grouped diffs: `102`
- flat rows: `1,028`
- unique guides: `38`
- change breakdown: `925` added, `103` removed
- exact flat/grouped match: yes
- duplicate flat rows: `0`
- empty citation rows: `0`
- non-increasing snapshot pairs: `0`
- grouped diffs with empty added+removed lists: `0`

Citation-format notes:

- `989` rows contain an explicit application-number pattern
- `39` rows do not contain an explicit application-number pattern but do contain a year
- the dataset generation code falls back to normalized full-string matching when no application number is present

## Manual PDF validation

### Confirmed genuine diffs

These were checked against the source PDFs with `pdftotext -layout`:

- `Article 8`, `28 February 2023 -> 31 August 2023`
  - `A and B v. France`
  - `Nepomnyashchiy and Others v. Russia`
  - `O.H. and G.H. v. Germany`
  - `Semenya v. Switzerland`
  - all absent from the earlier cited-cases list and present in the later one

- `Rights of LGBTI persons`, `28 February 2023 -> 31 August 2023`
  - `A.H. and Others v. Germany`
  - `Buhuceanu and Others v. Romania`
  - `Maymulakhin and Markiv v. Ukraine`
  - `Semenya v. Switzerland`
  - all absent from the earlier cited-cases list and present in the later one

- `Article 34/35`, `28 February 2025 -> 31 August 2025`
  - `Darko Spehar v. Croatia and Danijel Gojkovic v. Croatia`
  - `Mamaladze v. Georgia`
  - `Masse v. France`
  - `Semenya v. Switzerland [GC]`
  - all absent from the earlier cited-cases list and present in the later one

### Confirmed false positives or malformed rows

#### 1. Article 6 Criminal: typography-only churn

For `28 February 2023 -> 29 February 2024` and again for `29 February 2024 -> 31 August 2024`, the dataset records add/remove pairs for:

- `Botten v. Norway`
- `Pelladoah v. the Netherlands`
- `Schenk v. Switzerland`

PDF inspection shows these cases are present in both source and target cited-cases lists. The apparent changes are due to typography and extraction differences such as:

- `1996-I` vs `1996 I`
- trailing hanging dash characters

These are false positives, not substantive citation changes.

Affected rows in this cluster: `12`

#### 2. Rights of the child: wrapped-line truncation

For `31 August 2024 -> 28 February 2025`, the dataset records:

- added: `linguistic case") (merits), 23 July 1968, Series A no. 6`
- removed: `Belgium"("Belgian linguistic case") (merits), 23 July 1968, Series A no. 6`

PDF inspection shows the same citation is present in both versions. In the newer PDF, the case name is wrapped across two lines and the extracted citation kept only the trailing fragment.

This is a false positive caused by truncation.

Affected rows in this cluster: `2`

#### 3. Environment: normalization and truncation artifacts

Confirmed issues:

- `Pine Valley Developments Ltd and Others v. Ireland` was treated as added while
  `Pine Valley Developments Ltd and Others v. Irlande` was treated as removed
  - this is a language/normalization issue, not a new doctrinal event

- `Steel and Others v. United Kingdom, 23 September 1998, Reports of Judgments and Decisions 1998-VII`
  was treated as added while the earlier citation was stored as the truncated
  `Steel and Others v. United Kingdom, 23 September 1998, Reports of Judgments and Decisions`
  - this is a wrapped-line extraction issue

- one added row is stored only as:
  `v. Romania, no. 46201/16, 28 November 2023`
  - PDF inspection shows the full case name is present but wrapped onto the previous line

Affected rows in this cluster: at least `5`

#### 4. Article 1 Protocol 1: wrapped-line truncation

One added row is stored only as:

- `v. Romania, nos. 46201/16 and 47379/18, 28 November 2023`

PDF inspection shows the full citation is:

- `Associations of Communally-owned Forestry Proprietors Porceni Plesa and Piciorul Batran Banciu v. Romania, nos. 46201/16 and 47379/18, 28 November 2023`

This is a malformed citation row caused by wrapping.

Affected rows in this cluster: `1`

#### 5. Additional malformed formatting

At least one row contains an internal spacing artifact:

- `Tsulukidze and Rusulashvili v. G eorgia`

This does not necessarily create a false diff by itself, but it is evidence that the extraction output still needs normalization before annotation/modeling.

Affected rows in this cluster: `1`

## Minimum suspicious footprint

Using a conservative heuristic that flags:

- citations starting with lowercase text
- citations starting with `v.`
- hanging trailing dashes
- internal spacing artifacts like `G eorgia`
- highly similar add/remove pairs without application numbers in the same diff

the dataset contains at least `21` suspicious rows.

This should be treated as a lower bound, not a complete error count.

## Recommendation

The dataset is good enough to keep as the core artifact, but not good enough to use raw.

Recommended next action:

1. Add a cleanup pass before validation/annotation:
   - normalize hyphen variants
   - join wrapped lines in cited-case extraction
   - collapse internal OCR/extraction spacing artifacts
   - normalize obvious language variants where the underlying case is unchanged

2. Re-run the diff generation on the cleaned citation lists.

3. Re-audit the rows without application numbers and the rows with truncated starts like `v. Romania` or lowercase fragments.

4. Only then begin substantive-vs-routine annotation.

## Bottom line

The overall dataset structure is reliable.
The main risk is not missing files or broken JSON/CSV alignment.
The main risk is noisy citation-change rows created by PDF extraction artifacts, especially for older citations without application numbers and for lines that wrap across PDF boundaries.
