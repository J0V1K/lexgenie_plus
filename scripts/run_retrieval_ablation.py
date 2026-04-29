from __future__ import annotations

"""
Ablation study: compare BM25 retrieval performance using different
slices of the full judgment text as the query.

Sections parsed from ECHR judgment text:
  facts        — THE FACTS through THE LAW (factual narrative)
  law          — THE LAW through FOR THESE REASONS (legal analysis)
  operative    — FOR THESE REASONS onwards (operative provisions)
  full_text    — entire judgment
  base_only    — case name + app# + citation text (no judgment text)
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

INPUT_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
DIFF_DIR = Path("anas-diff-dataset")
OUTPUT_DIR = Path("outputs/prototype")
OUTPUT_JSON = OUTPUT_DIR / "retrieval_ablation.json"
ABLATION_CSV = OUTPUT_DIR / "retrieval_ablation_predictions.csv"

TOP_K = 10
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

FACTS_MARKERS = re.compile(r"^THE FACTS$", re.IGNORECASE)
LAW_MARKERS = re.compile(r"^THE LAW$", re.IGNORECASE)
OPERATIVE_MARKERS = re.compile(
    r"^FOR THESE REASONS|^OPERATIVE PROVISIONS|^FOR THESE REASONS, THE COURT",
    re.IGNORECASE,
)


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def query_tokens(text: str) -> list[str]:
    """Deduplicated token set for BM25 queries — same ranking, much faster on long texts."""
    return list(dict.fromkeys(tok.lower() for tok in TOKEN_RE.findall(text or "")))


def parse_section_path(linked_section: str) -> str:
    idx = linked_section.find(": ")
    return linked_section[:idx] if idx >= 0 else linked_section


def parse_linked_sections(value: str) -> list[str]:
    if not value:
        return []
    return [parse_section_path(item) for item in value.split("|") if item]


def split_judgment_sections(text: str) -> dict[str, str]:
    """Split text into facts / law / operative / full."""
    lines = text.splitlines()
    facts_start = law_start = operative_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if facts_start is None and FACTS_MARKERS.match(stripped):
            facts_start = i
        elif facts_start is not None and law_start is None and LAW_MARKERS.match(stripped):
            law_start = i
        elif law_start is not None and operative_start is None and OPERATIVE_MARKERS.match(stripped):
            operative_start = i
            break

    def span(start: int | None, end: int | None) -> str:
        if start is None:
            return ""
        return "\n".join(lines[start: end])

    return {
        "full_text": text,
        "facts": span(facts_start, law_start),
        "law": span(law_start, operative_start),
        "operative": span(operative_start, None),
        # Fallback when section markers absent
        "first_half": "\n".join(lines[: len(lines) // 2]),
        "second_half": "\n".join(lines[len(lines) // 2 :]),
    }


def load_diff_corpus(diff_path: Path) -> tuple[list[str], list[str]]:
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
    paths, docs = [], []
    for path, texts in sections.items():
        title = section_titles.get(path, "")
        body = " ".join(texts)
        paths.append(path)
        docs.append(f"{path} {title} {body}".strip())
    return paths, docs


def score(query: list[str], paths: list[str], bm25: BM25Okapi, gold_set: set[str]) -> dict[str, float]:
    if not query:
        return {"hit_at_1": 0.0, "hit_at_3": 0.0, "hit_at_10": 0.0, "mrr": 0.0}
    # Tokens not in corpus vocabulary score 0 — filter before BM25 loop (major speedup for long queries)
    vocab_query = [q for q in query if bm25.idf.get(q)]
    if not vocab_query:
        vocab_query = query[:50]  # fallback to first 50 tokens if nothing in vocab
    scores = bm25.get_scores(vocab_query)
    ranked = [paths[i] for i in sorted(range(len(paths)), key=lambda i: scores[i], reverse=True)]
    rr = next((1.0 / (r + 1) for r, p in enumerate(ranked) if p in gold_set), 0.0)
    return {
        "hit_at_1": float(ranked[0] in gold_set) if ranked else 0.0,
        "hit_at_3": float(any(p in gold_set for p in ranked[:3])),
        "hit_at_10": float(any(p in gold_set for p in ranked[:TOP_K])),
        "mrr": rr,
    }


def main() -> None:
    with INPUT_CSV.open() as f:
        rows = [r for r in csv.DictReader(f) if r.get("usable_for_location") == "true"]

    SECTION_NAMES = ["base_only", "facts", "law", "operative", "full_text"]
    diff_cache: dict[Path, tuple[list[str], Any]] = {}
    text_section_cache: dict[str, dict[str, list[str]]] = {}

    results: list[dict[str, Any]] = []

    for row in rows:
        diff_file = row.get("diff_file", "")
        diff_path = Path(diff_file) if diff_file else None
        if diff_path and not diff_path.is_absolute():
            diff_path = DIFF_DIR.parent / diff_file

        if diff_path and diff_path not in diff_cache:
            paths, docs = load_diff_corpus(diff_path)
            diff_cache[diff_path] = (paths, BM25Okapi([tokenize(d) for d in docs])) if paths else ([], None)

        paths, bm25 = diff_cache.get(diff_path) if diff_path else ([], None)
        if not paths or bm25 is None:
            continue

        gold_sections = parse_linked_sections(row.get("linked_sections", ""))
        if not gold_sections:
            continue
        gold_set = set(gold_sections)

        base_tokens = query_tokens(
            " ".join(filter(None, [
                row.get("case_name", ""),
                row.get("application_numbers", "").replace("|", " "),
                row.get("citation_text", ""),
            ]))
        )

        text_path = row.get("case_text_path", "")
        if text_path and text_path not in text_section_cache:
            p = Path(text_path)
            if p.exists():
                sections = split_judgment_sections(p.read_text())
                # Store deduplicated query tokens per section
                text_section_cache[text_path] = {
                    k: list(dict.fromkeys(tokenize(v))) for k, v in sections.items()
                }
            else:
                text_section_cache[text_path] = {}

        sec_tokens = text_section_cache.get(text_path, {})
        has_text = bool(sec_tokens.get("full_text"))

        row_result: dict[str, Any] = {
            "guide_id": row["guide_id"],
            "case_key": row.get("case_key", ""),
            "to_snapshot_date": row.get("to_snapshot_date", ""),
            "corpus_size": len(paths),
            "has_case_text": has_text,
            "gold_sections": "|".join(gold_sections),
        }

        # base_only
        row_result.update({f"hit_at_1_base_only": 0, f"hit_at_3_base_only": 0,
                            f"hit_at_10_base_only": 0, f"mrr_base_only": 0.0})
        s = score(base_tokens, paths, bm25, gold_set)
        for k, v in s.items():
            row_result[f"{k}_base_only"] = v

        for section in ["facts", "law", "operative", "full_text"]:
            sec_toks = sec_tokens.get(section, [])
            # Merge base + section tokens, preserving order, deduplicating
            combined = list(dict.fromkeys(base_tokens + sec_toks))
            s = score(combined, paths, bm25, gold_set)
            for k, v in s.items():
                row_result[f"{k}_{section}"] = v

        results.append(row_result)

    # Aggregate
    def agg(rows: list[dict], suffix: str) -> dict:
        n = len(rows)
        if not n:
            return {}
        return {
            "n": n,
            "hit_at_1": round(sum(r[f"hit_at_1_{suffix}"] for r in rows) / n, 4),
            "hit_at_3": round(sum(r[f"hit_at_3_{suffix}"] for r in rows) / n, 4),
            "mrr": round(sum(r[f"mrr_{suffix}"] for r in rows) / n, 4),
        }

    with_text = [r for r in results if r["has_case_text"]]
    without_text = [r for r in results if not r["has_case_text"]]

    sections_with_markers = {
        "facts": sum(1 for r in with_text if r.get("hit_at_1_facts", 0) != r.get("hit_at_1_base_only", 0)),
    }
    has_facts = [r for r in with_text if r.get("mrr_facts", 0) > 0 or True]

    report: dict[str, Any] = {
        "n_evaluable": len(results),
        "n_with_case_text": len(with_text),
        "n_without_case_text": len(without_text),
        "all_rows": {s: agg(results, s) for s in SECTION_NAMES},
        "with_case_text": {s: agg(with_text, s) for s in SECTION_NAMES},
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    fieldnames = list(results[0].keys()) if results else []
    with ABLATION_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print("\n=== ABLATION: rows WITH case text available ===")
    print(f"{'section':<14} hit@1   hit@3   mrr")
    for s in SECTION_NAMES:
        d = report["with_case_text"].get(s, {})
        if d:
            print(f"  {s:<12} {d['hit_at_1']:.3f}   {d['hit_at_3']:.3f}   {d['mrr']:.3f}  (n={d['n']})")

    print("\n=== ABLATION: all 805 evaluable rows ===")
    print(f"{'section':<14} hit@1   hit@3   mrr")
    for s in SECTION_NAMES:
        d = report["all_rows"].get(s, {})
        if d:
            print(f"  {s:<12} {d['hit_at_1']:.3f}   {d['hit_at_3']:.3f}   {d['mrr']:.3f}  (n={d['n']})")

    print(f"\nFull report: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
