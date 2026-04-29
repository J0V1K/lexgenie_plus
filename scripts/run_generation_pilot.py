from __future__ import annotations

"""
Generation pilot: edit step evaluation.

Given a guide section paragraph (pre_text) and a new case, generate the updated
paragraph (post_text) and compare to the gold editor update.

Edit subtypes drive the prompt strategy:
  citation_insert   — insert one citation into an existing citation list
  new_paragraph     — write an entirely new paragraph about the case
  doctrinal_rewrite — revise existing doctrine to incorporate the case
  paragraph_rewrite — rewrite an existing paragraph
  citation_refresh  — update citation details in existing text
  other             — fallback generic prompt

Evaluation:
  citation_in_output  — case name or app# appears in generated text
  rouge_l             — word-level LCS F1 against gold post_text
  len_ratio           — generated / gold character length

Requires: ANTHROPIC_API_KEY in environment
Model: claude-haiku-4-5-20251001 (fast, cheap, good at constrained edits)
Pilot: 120 rows, stratified by edit_subtype and temporal split

Outputs:
  outputs/generation/generation_pilot.csv
  outputs/generation/generation_pilot_report.json
"""

import csv
import json
import os
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

INPUT_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
EDIT_TYPE_CSV = Path("outputs/prototype/edit_type_predictions.csv")
OUTPUT_DIR = Path("outputs/generation")
OUTPUT_CSV = OUTPUT_DIR / "generation_pilot.csv"
REPORT_JSON = OUTPUT_DIR / "generation_pilot_report.json"

PILOT_N = 120
RANDOM_SEED = 42
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 2048
INTER_REQUEST_SLEEP = 0.3
TEMPORAL_CUTOFF = "2025-11-25"

SYSTEM_PROMPT = """You are a legal editor maintaining a doctrinal guide for the European Court of Human Rights (ECHR). \
Your task is to update a guide section paragraph to incorporate a newly decided case. \
Return only the updated paragraph text — no preamble, no explanation."""


def _case_header(row: dict[str, str]) -> str:
    imp_label = {
        "1": "Grand Chamber (highest importance)",
        "2": "Key case",
        "3": "Standard chamber judgment",
        "4": "Chamber judgment",
    }.get(row.get("hudoc_importance_level", ""), "")
    conclusion = (row.get("hudoc_conclusion") or "").strip()
    lines = [
        f"**Case:** {row.get('case_name', '')}",
        f"**Application number(s):** {row.get('application_numbers', '').replace('|', ', ')}",
        f"**Year:** {row.get('judgment_year', '')}",
        f"**Citation:** {row.get('citation_text', '')}",
    ]
    if imp_label:
        lines.append(f"**Importance:** {imp_label}")
    if conclusion:
        lines.append(f"**Outcome:** {conclusion[:250]}")
    return "\n".join(lines)


def make_prompt(row: dict[str, str], edit_subtype: str, law_excerpt: str = "") -> str:
    header = _case_header(row)
    pre = row.get("pre_text", "").strip()
    excerpt_block = (
        f"\n\n**Relevant excerpt from the judgment (THE LAW section):**\n{law_excerpt[:1500]}"
        if law_excerpt else ""
    )

    if edit_subtype == "citation_insert":
        return (
            f"The paragraph below contains a citation list. "
            f"Insert the new case into the list in the correct position (chronological or by relevance).\n\n"
            f"**Current paragraph:**\n{pre}\n\n"
            f"**New case to insert:**\n{header}{excerpt_block}\n\n"
            f"Return the full updated paragraph with the citation inserted."
        )

    if edit_subtype == "new_paragraph":
        return (
            f"Write a new paragraph for an ECHR doctrinal guide section. "
            f"The paragraph should summarise what this case established and why it matters for the section.\n\n"
            f"**Existing section context (preceding paragraph):**\n{pre}\n\n"
            f"**New case to describe:**\n{header}{excerpt_block}\n\n"
            f"Return only the new paragraph."
        )

    if edit_subtype in ("doctrinal_rewrite", "paragraph_rewrite"):
        return (
            f"Revise the paragraph below to incorporate the new case. "
            f"The new case may refine, confirm, or qualify the existing doctrinal statement.\n\n"
            f"**Current paragraph:**\n{pre}\n\n"
            f"**New case:**\n{header}{excerpt_block}\n\n"
            f"Return the fully revised paragraph."
        )

    if edit_subtype == "citation_refresh":
        return (
            f"Update the citation reference in the paragraph below to reflect the newer case.\n\n"
            f"**Current paragraph:**\n{pre}\n\n"
            f"**Replacement/additional case:**\n{header}\n\n"
            f"Return the updated paragraph."
        )

    # Generic fallback
    return (
        f"Update the paragraph below to incorporate the new case. "
        f"Keep the same academic register and citation style.\n\n"
        f"**Current paragraph:**\n{pre}\n\n"
        f"**New case:**\n{header}{excerpt_block}\n\n"
        f"Return only the updated paragraph."
    )


def rouge_l(pred: str, ref: str) -> float:
    p = pred.lower().split()
    r = ref.lower().split()
    n, m = len(p), len(r)
    if not n or not m:
        return 0.0
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = dp[i-1][j-1] + 1 if p[i-1] == r[j-1] else max(dp[i-1][j], dp[i][j-1])
    lcs = dp[n][m]
    pr, rc = lcs / n, lcs / m
    return round(2 * pr * rc / (pr + rc), 4) if pr + rc else 0.0


def citation_hit(text: str, case_name: str, app_numbers: str) -> dict[str, bool]:
    lo = text.lower()
    parts = [p.strip() for p in re.split(r"[\s,v.]+", case_name) if len(p.strip()) > 3]
    name_hit = any(p.lower() in lo for p in parts[-3:]) if parts else False
    first_app = (app_numbers.split("|")[0]).strip() if app_numbers else ""
    return {"name_hit": name_hit, "appno_hit": bool(first_app and first_app in text)}


def load_law_section(path: str) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(errors="replace")
    lines = text.splitlines()
    law_start = op_start = None
    for i, line in enumerate(lines):
        s = line.strip()
        if law_start is None and re.match(r"^THE LAW$", s, re.I):
            law_start = i
        elif law_start is not None and op_start is None and re.match(
            r"^FOR THESE REASONS|^OPERATIVE PROVISIONS", s, re.I
        ):
            op_start = i
            break
    if law_start is None:
        return ""
    end = op_start if op_start else min(law_start + 200, len(lines))
    return "\n".join(lines[law_start:end])


def build_sample(all_rows: list[dict], edit_map: dict, n: int, seed: int) -> list[dict]:
    """Stratified sample: roughly proportional by edit_subtype, with test set floor."""
    rng = random.Random(seed)
    by_subtype: dict[str, list[dict]] = defaultdict(list)
    for r in all_rows:
        subtype = edit_map.get((r["guide_id"], r["case_key"],
                                r["from_snapshot_date"], r["to_snapshot_date"]), "unknown")
        r = dict(r)
        r["_edit_subtype"] = subtype
        r["_split"] = "test" if r["to_snapshot_date"] >= TEMPORAL_CUTOFF else "dev"
        by_subtype[subtype].append(r)

    total = sum(len(v) for v in by_subtype.values())
    sample: list[dict] = []
    budgets = {st: max(5, round(n * len(rows) / total)) for st, rows in by_subtype.items()}
    # Ensure at least 10 test rows
    test_reserve = []
    for rows in by_subtype.values():
        test_reserve.extend([r for r in rows if r["_split"] == "test"])
    rng.shuffle(test_reserve)
    sample.extend(test_reserve[:10])
    sampled_keys = {(r["guide_id"], r["case_key"]) for r in sample}

    for subtype, budget in sorted(budgets.items()):
        pool = [r for r in by_subtype[subtype]
                if (r["guide_id"], r["case_key"]) not in sampled_keys]
        take = min(budget, len(pool))
        chosen = rng.sample(pool, take)
        sample.extend(chosen)
        sampled_keys.update((r["guide_id"], r["case_key"]) for r in chosen)
        if len(sample) >= n:
            break

    rng.shuffle(sample)
    return sample[:n]


def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    dry_run = not api_key
    if dry_run:
        print("WARNING: ANTHROPIC_API_KEY not set — running in dry-run mode.")
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

    # Load edit type predictions
    edit_map: dict[tuple, str] = {}
    if EDIT_TYPE_CSV.exists():
        with EDIT_TYPE_CSV.open() as f:
            for r in csv.DictReader(f):
                key = (r["guide_id"], r["case_key"],
                       r.get("from_snapshot_date", ""), r.get("to_snapshot_date", ""))
                edit_map[key] = r.get("edit_subtype", "unknown")

    with INPUT_CSV.open() as f:
        all_rows = [
            r for r in csv.DictReader(f)
            if r.get("usable_for_generation") == "true"
            and (r.get("pre_text") or "").strip()
            and (r.get("post_text") or "").strip()
        ]

    sample = build_sample(all_rows, edit_map, PILOT_N, RANDOM_SEED)
    print(f"Pilot sample: {len(sample)} rows")
    from collections import Counter
    print("Subtype breakdown:", dict(Counter(r["_edit_subtype"] for r in sample)))
    print("Split breakdown:", dict(Counter(r["_split"] for r in sample)))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for i, row in enumerate(sample):
        subtype = row["_edit_subtype"]
        law = load_law_section(row.get("case_text_path", ""))
        prompt = make_prompt(row, subtype, law)
        gold = row["post_text"].strip()

        if dry_run:
            generated = ""
            in_tok = out_tok = 0
        else:
            try:
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                generated = resp.content[0].text.strip()
                in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
                time.sleep(INTER_REQUEST_SLEEP)
            except Exception as exc:
                print(f"  [{i+1}] ERROR: {exc}")
                generated = ""
                in_tok = out_tok = 0

        ch = citation_hit(generated, row.get("case_name", ""), row.get("application_numbers", ""))
        rl = rouge_l(generated, gold)

        results.append({
            "guide_id": row["guide_id"],
            "case_key": row["case_key"],
            "to_snapshot_date": row["to_snapshot_date"],
            "split": row["_split"],
            "edit_subtype": subtype,
            "case_name": row.get("case_name", ""),
            "hudoc_importance_level": row.get("hudoc_importance_level", ""),
            "has_law_excerpt": bool(law),
            "pre_len": len(row.get("pre_text", "")),
            "post_len": len(gold),
            "gen_len": len(generated),
            "len_ratio": round(len(generated) / len(gold), 3) if gold else None,
            "name_hit": ch["name_hit"],
            "appno_hit": ch["appno_hit"],
            "citation_hit": int(ch["name_hit"] or ch["appno_hit"]),
            "rouge_l": rl,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "generated_text": generated,
            "gold_text": gold,
        })

        if not dry_run and (i + 1) % 10 == 0:
            ne = [r for r in results if r["generated_text"]]
            avg_hit = sum(r["citation_hit"] for r in ne) / len(ne) if ne else 0
            avg_rl = sum(r["rouge_l"] for r in ne) / len(ne) if ne else 0
            print(f"  [{i+1}/{len(sample)}]  citation_hit={avg_hit:.2f}  rouge_l={avg_rl:.3f}")

    non_empty = [r for r in results if r["generated_text"]]
    ne = len(non_empty) or 1

    def avg(key: str, subset=None) -> float:
        s = subset if subset is not None else non_empty
        vals = [r[key] for r in s if r.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    subtype_metrics: dict[str, Any] = {}
    for st in set(r["edit_subtype"] for r in non_empty):
        sub = [r for r in non_empty if r["edit_subtype"] == st]
        subtype_metrics[st] = {
            "n": len(sub),
            "citation_hit": avg("citation_hit", sub),
            "rouge_l": avg("rouge_l", sub),
            "avg_len_ratio": avg("len_ratio", sub),
        }

    report: dict[str, Any] = {
        "model": MODEL,
        "pilot_n": len(sample),
        "n_with_output": len(non_empty),
        "dry_run": dry_run,
        "overall": {
            "citation_hit": avg("citation_hit"),
            "name_hit": avg("name_hit"),
            "appno_hit": avg("appno_hit"),
            "rouge_l": avg("rouge_l"),
            "avg_len_ratio": avg("len_ratio"),
        },
        "by_split": {
            sp: {
                "n": len([r for r in non_empty if r["split"] == sp]),
                "citation_hit": avg("citation_hit", [r for r in non_empty if r["split"] == sp]),
                "rouge_l": avg("rouge_l", [r for r in non_empty if r["split"] == sp]),
            }
            for sp in ("dev", "test")
        },
        "by_subtype": subtype_metrics,
        "total_input_tokens": sum(r["input_tokens"] for r in results),
        "total_output_tokens": sum(r["output_tokens"] for r in results),
    }

    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    fieldnames = list(results[0].keys())
    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\n=== GENERATION PILOT ({MODEL}, n={len(non_empty)}) ===")
    if not dry_run:
        o = report["overall"]
        print(f"  citation_hit : {o['citation_hit']:.3f}  (name={o['name_hit']:.3f}  appno={o['appno_hit']:.3f})")
        print(f"  rouge_l      : {o['rouge_l']:.3f}")
        print(f"  len_ratio    : {o['avg_len_ratio']:.3f}  (generated/gold)")
        print(f"  tokens       : {report['total_input_tokens']} in / {report['total_output_tokens']} out")
        print("\n  by subtype:")
        for st, m in sorted(subtype_metrics.items(), key=lambda x: -x[1]["n"]):
            print(f"    {st:<22} n={m['n']:3d}  hit={m['citation_hit']:.2f}  rl={m['rouge_l']:.3f}")
    print(f"\nFull report: {REPORT_JSON}")
    if dry_run:
        print("\nTo run with API calls: ANTHROPIC_API_KEY=sk-... python3 scripts/run_generation_pilot.py")


if __name__ == "__main__":
    main()
