from __future__ import annotations

import csv
import json
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
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def parse_section_path(linked_section: str) -> str:
    # linked_section format: "II.B.1.b: Gender recognition (...)"
    # section_path is everything before the first ": "
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

    # Include section events for section_a titles on removed/modified sections
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


def evaluate_row(
    row: dict[str, str],
    diff_cache: dict[Path, tuple[list[str], BM25Okapi]],
    case_text_cache: dict[str, list[str]],
) -> dict[str, Any] | None:
    gold_sections = parse_linked_sections(row.get("linked_sections", ""))
    if not gold_sections:
        return None

    diff_file = row.get("diff_file", "")
    if not diff_file:
        return None
    diff_path = Path(diff_file)
    if not diff_path.is_absolute():
        diff_path = DIFF_DIR.parent / diff_file
    if not diff_path.exists():
        return None

    if diff_path not in diff_cache:
        paths, docs = load_diff_corpus(diff_path)
        if not paths:
            diff_cache[diff_path] = ([], None)  # type: ignore
        else:
            tokenized = [tokenize(d) for d in docs]
            diff_cache[diff_path] = (paths, BM25Okapi(tokenized))

    paths, bm25 = diff_cache[diff_path]
    if not paths or bm25 is None:
        return None

    base_query = build_query(row)
    if not base_query:
        return None

    gold_set = set(gold_sections)
    gold_in_corpus = any(g in paths for g in gold_sections)

    base_result = rank_and_score(base_query, paths, bm25, gold_set)

    case_tokens = load_case_text_tokens(row, case_text_cache)
    has_case_text = bool(case_tokens)
    if has_case_text:
        enriched_result = rank_and_score(
            base_query + case_tokens, paths, bm25, gold_set
        )
    else:
        enriched_result = base_result

    return {
        "guide_id": row["guide_id"],
        "case_key": row.get("case_key", ""),
        "citation_text": row.get("citation_text", ""),
        "gold_sections": "|".join(gold_sections),
        "gold_in_corpus": gold_in_corpus,
        "case_text_available": has_case_text,
        "corpus_size": len(paths),
        "top_1_base": base_result["ranked_paths"][0] if base_result["ranked_paths"] else "",
        "top_1_enriched": enriched_result["ranked_paths"][0] if enriched_result["ranked_paths"] else "",
        "hit_at_1_base": base_result["hit_at_1"],
        "hit_at_3_base": base_result["hit_at_3"],
        "hit_at_10_base": base_result["hit_at_10"],
        "reciprocal_rank_base": base_result["reciprocal_rank"],
        "hit_at_1_enriched": enriched_result["hit_at_1"],
        "hit_at_3_enriched": enriched_result["hit_at_3"],
        "hit_at_10_enriched": enriched_result["hit_at_10"],
        "reciprocal_rank_enriched": enriched_result["reciprocal_rank"],
        "strict_citation_field_match": row.get("strict_citation_field_match", "false"),
    }


def summarize(subset: list[dict[str, Any]], suffix: str) -> dict[str, float] | None:
    if not subset:
        return None
    n = len(subset)
    return {
        "n": n,
        "hit_at_1": round(sum(r[f"hit_at_1_{suffix}"] for r in subset) / n, 4),
        "hit_at_3": round(sum(r[f"hit_at_3_{suffix}"] for r in subset) / n, 4),
        "hit_at_10": round(sum(r[f"hit_at_10_{suffix}"] for r in subset) / n, 4),
        "mrr": round(sum(r[f"reciprocal_rank_{suffix}"] for r in subset) / n, 4),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"n": 0}
    n = len(results)
    n_gold_in_corpus = sum(1 for r in results if r["gold_in_corpus"])
    with_text = [r for r in results if r["case_text_available"]]
    without_text = [r for r in results if not r["case_text_available"]]
    strict = [r for r in results if r["strict_citation_field_match"] == "true"]

    by_guide: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        buckets[r["guide_id"]].append(r)
    for gid, rows in buckets.items():
        m = len(rows)
        by_guide[gid] = {
            "n": m,
            "hit_at_1_base": round(sum(r["hit_at_1_base"] for r in rows) / m, 4),
            "hit_at_1_enriched": round(
                sum(r["hit_at_1_enriched"] for r in rows) / m, 4
            ),
            "mrr_base": round(sum(r["reciprocal_rank_base"] for r in rows) / m, 4),
            "mrr_enriched": round(
                sum(r["reciprocal_rank_enriched"] for r in rows) / m, 4
            ),
        }

    return {
        "n": n,
        "gold_in_corpus_rate": round(n_gold_in_corpus / n, 4),
        "n_with_case_text": len(with_text),
        "n_without_case_text": len(without_text),
        "base_all": summarize(results, "base"),
        "enriched_all": summarize(results, "enriched"),
        "base_with_case_text": summarize(with_text, "base"),
        "enriched_with_case_text": summarize(with_text, "enriched"),
        "base_without_case_text": summarize(without_text, "base"),
        "base_strict": summarize(strict, "base"),
        "enriched_strict": summarize(strict, "enriched"),
        "by_guide": by_guide,
    }


def write_predictions(results: list[dict[str, Any]]) -> None:
    if not results:
        return
    fieldnames = list(results[0].keys())
    with PREDICTIONS_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)


def main() -> None:
    with INPUT_CSV.open() as handle:
        rows = [
            r for r in csv.DictReader(handle) if r.get("usable_for_location") == "true"
        ]

    diff_cache: dict[Path, tuple[list[str], BM25Okapi]] = {}
    case_text_cache: dict[str, list[str]] = {}
    results: list[dict[str, Any]] = []
    skipped: Counter = Counter()
    for row in rows:
        evaluated = evaluate_row(row, diff_cache, case_text_cache)
        if evaluated is None:
            skipped["skipped"] += 1
            continue
        results.append(evaluated)

    report = aggregate(results)
    report["input_rows_usable_for_location"] = len(rows)
    report["evaluated_rows"] = len(results)
    report["skipped_rows"] = skipped["skipped"]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    write_predictions(results)

    summary = {k: v for k, v in report.items() if k != "by_guide"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
