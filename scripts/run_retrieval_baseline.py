from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

INPUT_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
DIFF_DIR = Path("anas-diff-dataset")
OUTPUT_DIR = Path("outputs/prototype")
OUTPUT_JSON = OUTPUT_DIR / "retrieval_eval.json"
PREDICTIONS_CSV = OUTPUT_DIR / "retrieval_predictions.csv"

TOP_K = 10
# Hold out transitions whose to_snapshot_date >= this cutoff as test set.
# Chosen as the 80th percentile of unique to_snapshot_dates.
TEMPORAL_CUTOFF = "2025-11-25"

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def parse_section_path(linked_section: str) -> str:
    idx = linked_section.find(": ")
    return linked_section[:idx] if idx >= 0 else linked_section


def parse_linked_sections(value: str) -> list[str]:
    if not value:
        return []
    return [parse_section_path(item) for item in value.split("|") if item]


def load_diff_corpus(diff_path: Path) -> tuple[list[str], list[str]]:
    """Return (section_paths, section_docs) using pre-update text_a content."""
    data = json.loads(diff_path.read_text())
    sections: dict[str, list[str]] = defaultdict(list)
    section_titles: dict[str, str] = {}

    for para in data.get("paragraph_changes", []):
        path = para.get("section_path") or ""
        if not path:
            continue
        title = para.get("section_title") or ""
        if title and path not in section_titles:
            section_titles[path] = title
        text_a = (para.get("text_a") or "").strip()
        if text_a:
            sections[path].append(text_a)

    for ev in data.get("section_events", []):
        path = ev.get("path") or ""
        title_a = ev.get("title_a") or ""
        if path and title_a and path not in section_titles:
            section_titles[path] = title_a

    paths: list[str] = []
    docs: list[str] = []
    for path, texts in sections.items():
        title = section_titles.get(path, "")
        body = " ".join(texts)
        doc = f"{path} {title} {body}".strip()
        paths.append(path)
        docs.append(doc)
    return paths, docs


def build_query(row: dict[str, str]) -> list[str]:
    parts: list[str] = []
    parts.append(row.get("case_name", ""))
    app_nums = row.get("application_numbers", "")
    if app_nums:
        parts.extend(app_nums.split("|"))
    citation_text = row.get("citation_text", "")
    if citation_text:
        parts.append(citation_text)
    return tokenize(" ".join(parts))


def load_case_text_tokens(
    row: dict[str, str], cache: dict[str, list[str]]
) -> list[str]:
    path = row.get("case_text_path", "")
    if not path:
        return []
    if path in cache:
        return cache[path]
    p = Path(path)
    if not p.exists():
        cache[path] = []
        return []
    tokens = tokenize(p.read_text())
    cache[path] = tokens
    return tokens


def load_law_section_tokens(path: str, cache: dict[str, list[str]]) -> list[str]:
    """Tokenize only THE LAW section; ablation shows this beats full-text enrichment."""
    if not path:
        return []
    if path in cache:
        return cache[path]
    p = Path(path)
    if not p.exists():
        cache[path] = []
        return []
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
        cache[path] = []
        return []
    end = op_start if op_start else min(law_start + 250, len(lines))
    tokens = tokenize("\n".join(lines[law_start:end]))
    cache[path] = tokens
    return tokens


def rank_and_score(
    query: list[str], paths: list[str], bm25: BM25Okapi, gold_set: set[str]
) -> dict[str, Any]:
    scores = bm25.get_scores(query)
    ranked_idx = sorted(range(len(paths)), key=lambda i: scores[i], reverse=True)
    ranked_paths = [paths[i] for i in ranked_idx]
    hit_at_1 = 1 if ranked_paths and ranked_paths[0] in gold_set else 0
    hit_at_3 = 1 if any(p in gold_set for p in ranked_paths[:3]) else 0
    hit_at_10 = 1 if any(p in gold_set for p in ranked_paths[:TOP_K]) else 0
    rr = 0.0
    for rank, path in enumerate(ranked_paths, start=1):
        if path in gold_set:
            rr = 1.0 / rank
            break
    return {
        "ranked_paths": ranked_paths,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "hit_at_10": hit_at_10,
        "reciprocal_rank": rr,
    }


def random_score(paths: list[str], gold_set: set[str], seed: int) -> dict[str, Any]:
    shuffled = paths[:]
    random.Random(seed).shuffle(shuffled)
    hit_at_1 = 1 if shuffled and shuffled[0] in gold_set else 0
    hit_at_3 = 1 if any(p in gold_set for p in shuffled[:3]) else 0
    hit_at_10 = 1 if any(p in gold_set for p in shuffled[:TOP_K]) else 0
    rr = 0.0
    for rank, path in enumerate(shuffled, start=1):
        if path in gold_set:
            rr = 1.0 / rank
            break
    return {
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "hit_at_10": hit_at_10,
        "reciprocal_rank": rr,
    }


def evaluate_row(
    row: dict[str, str],
    diff_cache: dict[Path, tuple[list[str], Any]],
    case_text_cache: dict[str, list[str]],
    law_cache: dict[str, list[str]],
    row_idx: int,
) -> dict[str, Any]:
    split = "test" if row.get("to_snapshot_date", "") >= TEMPORAL_CUTOFF else "dev"
    is_linked = row.get("link_status") == "linked_paragraphs"
    gold_sections = parse_linked_sections(row.get("linked_sections", ""))

    # Rows with no paragraph link score 0 across all models — real misses, not skips.
    if not is_linked or not gold_sections:
        return {
            "guide_id": row["guide_id"],
            "case_key": row.get("case_key", ""),
            "to_snapshot_date": row.get("to_snapshot_date", ""),
            "citation_change": row.get("citation_change", ""),
            "gold_sections": "",
            "gold_in_corpus": False,
            "case_text_available": False,
            "law_text_available": False,
            "corpus_size": 0,
            "split": split,
            "link_status": row.get("link_status", ""),
            "strict_citation_field_match": row.get("strict_citation_field_match", "false"),
            "evaluable": False,
            "hit_at_1_random": 0, "hit_at_3_random": 0,
            "hit_at_10_random": 0, "reciprocal_rank_random": 0.0,
            "hit_at_1_base": 0, "hit_at_3_base": 0,
            "hit_at_10_base": 0, "reciprocal_rank_base": 0.0,
            "hit_at_1_enriched": 0, "hit_at_3_enriched": 0,
            "hit_at_10_enriched": 0, "reciprocal_rank_enriched": 0.0,
            "hit_at_1_law": 0, "hit_at_3_law": 0,
            "hit_at_10_law": 0, "reciprocal_rank_law": 0.0,
            "top_1_base": "", "top_1_enriched": "", "top_1_law": "",
        }

    diff_file = row.get("diff_file", "")
    diff_path = Path(diff_file) if diff_file else None
    if diff_path and not diff_path.is_absolute():
        diff_path = DIFF_DIR.parent / diff_file

    if diff_path and diff_path not in diff_cache:
        paths, docs = load_diff_corpus(diff_path)
        if paths:
            tokenized = [tokenize(d) for d in docs]
            diff_cache[diff_path] = (paths, BM25Okapi(tokenized))
        else:
            diff_cache[diff_path] = ([], None)

    corpus_entry = diff_cache.get(diff_path) if diff_path else None
    paths, bm25 = corpus_entry if corpus_entry else ([], None)

    gold_set = set(gold_sections)
    gold_in_corpus = any(g in paths for g in gold_sections) if paths else False

    base_query = build_query(row)
    case_tokens = load_case_text_tokens(row, case_text_cache)
    law_tokens = load_law_section_tokens(row.get("case_text_path", ""), law_cache)
    has_case_text = bool(case_tokens)
    has_law_text = bool(law_tokens)

    if not paths or bm25 is None or not base_query:
        zeros: dict[str, Any] = {
            "hit_at_1": 0, "hit_at_3": 0, "hit_at_10": 0,
            "reciprocal_rank": 0.0, "ranked_paths": [],
        }
        base_result = enriched_result = law_result = rand_result = zeros
    else:
        base_result = rank_and_score(base_query, paths, bm25, gold_set)
        enriched_result = (
            rank_and_score(base_query + case_tokens, paths, bm25, gold_set)
            if has_case_text else base_result
        )
        law_result = (
            rank_and_score(base_query + law_tokens, paths, bm25, gold_set)
            if has_law_text else base_result
        )
        rand_result = random_score(paths, gold_set, seed=row_idx)

    return {
        "guide_id": row["guide_id"],
        "case_key": row.get("case_key", ""),
        "to_snapshot_date": row.get("to_snapshot_date", ""),
        "citation_change": row.get("citation_change", ""),
        "gold_sections": "|".join(gold_sections),
        "gold_in_corpus": gold_in_corpus,
        "case_text_available": has_case_text,
        "law_text_available": has_law_text,
        "corpus_size": len(paths),
        "split": split,
        "link_status": row.get("link_status", ""),
        "strict_citation_field_match": row.get("strict_citation_field_match", "false"),
        "evaluable": bool(paths and bm25 and base_query),
        "hit_at_1_random": rand_result["hit_at_1"],
        "hit_at_3_random": rand_result["hit_at_3"],
        "hit_at_10_random": rand_result["hit_at_10"],
        "reciprocal_rank_random": rand_result["reciprocal_rank"],
        "hit_at_1_base": base_result["hit_at_1"],
        "hit_at_3_base": base_result["hit_at_3"],
        "hit_at_10_base": base_result["hit_at_10"],
        "reciprocal_rank_base": base_result["reciprocal_rank"],
        "hit_at_1_enriched": enriched_result["hit_at_1"],
        "hit_at_3_enriched": enriched_result["hit_at_3"],
        "hit_at_10_enriched": enriched_result["hit_at_10"],
        "reciprocal_rank_enriched": enriched_result["reciprocal_rank"],
        "hit_at_1_law": law_result["hit_at_1"],
        "hit_at_3_law": law_result["hit_at_3"],
        "hit_at_10_law": law_result["hit_at_10"],
        "reciprocal_rank_law": law_result["reciprocal_rank"],
        "top_1_base": (base_result["ranked_paths"][0]
                       if base_result.get("ranked_paths") else ""),
        "top_1_enriched": (enriched_result["ranked_paths"][0]
                           if enriched_result.get("ranked_paths") else ""),
        "top_1_law": (law_result["ranked_paths"][0]
                      if law_result.get("ranked_paths") else ""),
    }


def summarize(
    rows: list[dict[str, Any]], suffix: str, label: str
) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "label": label}
    n = len(rows)
    return {
        "label": label,
        "n": n,
        "hit_at_1": round(sum(r[f"hit_at_1_{suffix}"] for r in rows) / n, 4),
        "hit_at_3": round(sum(r[f"hit_at_3_{suffix}"] for r in rows) / n, 4),
        "mrr": round(sum(r[f"reciprocal_rank_{suffix}"] for r in rows) / n, 4),
    }


def build_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    all_rows = results
    linked = [r for r in results if r["link_status"] == "linked_paragraphs"]
    evaluable = [r for r in linked if r["evaluable"]]
    with_text = [r for r in evaluable if r["case_text_available"]]
    dev_eval = [r for r in evaluable if r["split"] == "dev"]
    test_eval = [r for r in evaluable if r["split"] == "test"]
    dev_all = [r for r in all_rows if r["split"] == "dev"]
    test_all = [r for r in all_rows if r["split"] == "test"]
    strict = [r for r in evaluable if r["strict_citation_field_match"] == "true"]

    n_unlinked = sum(1 for r in results if r["link_status"] != "linked_paragraphs")
    n_gold_in_corpus = sum(1 for r in evaluable if r["gold_in_corpus"])

    models = ["random", "base", "enriched", "law"]

    def section(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        return {m: summarize(rows, m, label) for m in models}

    return {
        "temporal_cutoff": TEMPORAL_CUTOFF,
        "n_total": len(all_rows),
        "n_linked": len(linked),
        "n_unlinked_scored_zero": n_unlinked,
        "n_evaluable": len(evaluable),
        "n_with_case_text": len(with_text),
        "gold_in_corpus_rate_evaluable": round(n_gold_in_corpus / len(evaluable), 4) if evaluable else 0,
        # Primary comparison: unconditional (all 1014) vs conditional (linked only)
        "unconditional_all": section(all_rows, "all_1014_rows"),
        "conditional_linked": section(evaluable, "linked_evaluable"),
        # With/without case text (conditional)
        "conditional_with_text": section(with_text, "linked_with_text"),
        "conditional_strict": section(strict, "linked_strict"),
        # Temporal split (conditional)
        "dev_conditional": section(dev_eval, "dev_linked"),
        "test_conditional": section(test_eval, "test_linked"),
        "dev_unconditional": section(dev_all, "dev_all"),
        "test_unconditional": section(test_all, "test_all"),
    }


def main() -> None:
    with INPUT_CSV.open() as handle:
        all_rows = list(csv.DictReader(handle))

    diff_cache: dict[Path, Any] = {}
    case_text_cache: dict[str, list[str]] = {}
    law_cache: dict[str, list[str]] = {}
    results: list[dict[str, Any]] = []

    for idx, row in enumerate(all_rows):
        result = evaluate_row(row, diff_cache, case_text_cache, law_cache, idx)
        results.append(result)

    report = build_report(results)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    fieldnames = list(results[0].keys())
    with PREDICTIONS_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # Print the headline numbers
    def fmt(d: dict) -> str:
        return f"hit@1={d['hit_at_1']:.3f} hit@3={d['hit_at_3']:.3f} mrr={d['mrr']:.3f} (n={d['n']})"

    print("\n=== UNCONDITIONAL (all 1,014 rows; unlinked score 0) ===")
    for m in ["random", "base", "enriched", "law"]:
        print(f"  {m:12s}: {fmt(report['unconditional_all'][m])}")

    print("\n=== CONDITIONAL (linked+evaluable rows only) ===")
    for m in ["random", "base", "enriched", "law"]:
        print(f"  {m:12s}: {fmt(report['conditional_linked'][m])}")

    print("\n=== TEMPORAL SPLIT (conditional) ===")
    print(f"  dev  law     : {fmt(report['dev_conditional']['law'])}")
    print(f"  test law     : {fmt(report['test_conditional']['law'])}")
    print(f"  dev  enriched: {fmt(report['dev_conditional']['enriched'])}")
    print(f"  test enriched: {fmt(report['test_conditional']['enriched'])}")
    print(f"  dev  base    : {fmt(report['dev_conditional']['base'])}")
    print(f"  test base    : {fmt(report['test_conditional']['base'])}")

    print(f"\nFull report written to {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
