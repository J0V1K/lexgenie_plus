# Citation Diff Cleanup Re-Audit

## Pair-Level Comparison
- published grouped diffs: `102`
- local grouped diffs: `103`
- shared snapshot pairs: `101`
- exact matches on shared pairs: `65`
- changed shared pairs: `36`
- published-only pairs: `1`
- local-only pairs: `2`
- published-only rows across changed shared pairs: `63`
- cleaned-only rows across changed shared pairs: `49`

## Flat-Row Summary
- published flat rows: `1028`
- local flat rows: `1014`
- published suspicious rows: `44`
- local suspicious rows: `25`

## Suspicious Heuristics
- published similar add/remove no-app pairs: `8`
- local similar add/remove no-app pairs: `0`
- published suspicious reason counts: `{'no_app_number': 31, 'lowercase_start': 3, 'starts_with_v': 2, 'spacing_artifact': 12}`
- local suspicious reason counts: `{'no_app_number': 14, 'spacing_artifact': 12}`

## Notes
- This re-audit is heuristic. It is intended to prioritize rows for manual review, not to replace PDF verification.
- Detailed examples are written to `outputs/citation_diff_cleanup/comparison_details.json`.
