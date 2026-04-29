from __future__ import annotations

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
OUTPUT_JSON = OUTPUT_DIR / "location_eval.json"
PREDICTIONS_CSV = OUTPUT_DIR / "location_predictions.csv"

TOP_K = 10
SECTION_TOP_K = 5
TEMPORAL_CUTOFF = "2025-11-25"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def normalize_text(text: str) -> str:
    return " ".join(tokenize(text))


def parse_linked_paragraph_refs(value: str) -> list[str]:
    refs: list[str] = []
    if not value:
        return refs
    parts = value.split("|")
    i = 0
    while i + 2 < len(parts):
        section_path = parts[i].strip()
        a_part = parts[i + 1].strip()
        b_part = parts[i + 2].strip()
        if section_path and a_part.startswith("a:") and b_part.startswith("b:"):
            refs.append(f"{section_path}|{a_part}|{b_part}")
            i += 3
            continue
        i += 1
    return refs


def parse_linked_sections(value: str) -> list[str]:
    sections: list[str] = []
    for item in value.split("|"):
        item = item.strip()
        if not item:
            continue
        idx = item.find(": ")
        sections.append(item[:idx] if idx >= 0 else item)
    return sections


def build_query(row: dict[str, str]) -> list[str]:
    parts = [
        row.get("case_name", ""),
        row.get("citation_text", ""),
        row.get("application_numbers", "").replace("|", " "),
    ]
    return tokenize(" ".join(parts))


def parse_application_numbers(value: str) -> list[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def load_law_section_tokens(path_value: str, cache: dict[str, list[str]]) -> list[str]:
    if not path_value:
        return []
    if path_value in cache:
        return cache[path_value]
    path = Path(path_value)
    if not path.exists():
        cache[path_value] = []
        return []
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    law_start = operative_start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if law_start is None and re.match(r"^THE LAW$", stripped, re.IGNORECASE):
            law_start = i
        elif law_start is not None and operative_start is None and re.match(
            r"^FOR THESE REASONS|^OPERATIVE PROVISIONS", stripped, re.IGNORECASE
        ):
            operative_start = i
            break
    if law_start is None:
        excerpt = text[:15000]
    else:
        end = operative_start if operative_start else min(law_start + 250, len(lines))
        excerpt = "\n".join(lines[law_start:end])
    tokens = tokenize(excerpt)
    cache[path_value] = tokens
    return tokens


def para_ref(para: dict[str, Any]) -> str:
    return (
        f"{para.get('section_path', '')}"
        f"|a:{para.get('para_num_a') or ''}"
        f"|b:{para.get('para_num_b') or ''}"
    )


def para_text(para: dict[str, Any]) -> str:
    text_a = (para.get("text_a") or "").strip()
    text_b = (para.get("text_b") or "").strip()
    return text_a or text_b


def load_diff_corpus(diff_path: Path) -> dict[str, Any]:
    data = json.loads(diff_path.read_text())
    paragraphs: list[dict[str, Any]] = []
    section_to_paras: dict[str, list[int]] = defaultdict(list)
    section_docs: list[str] = []
    section_paths: list[str] = []

    for para in data.get("paragraph_changes", []):
        section_path = para.get("section_path") or ""
        if not section_path:
            continue
        text = para_text(para)
        title = (para.get("section_title") or "").strip()
        doc = f"{section_path} {title} {text}".strip()
        paragraphs.append(
            {
                "ref": para_ref(para),
                "section_path": section_path,
                "section_title": title,
                "doc": doc,
                "normalized_doc": normalize_text(doc),
            }
        )
        section_to_paras[section_path].append(len(paragraphs) - 1)

    for section_path, idxs in section_to_paras.items():
        title = paragraphs[idxs[0]]["section_title"] if idxs else ""
        joined = " ".join(paragraphs[i]["doc"] for i in idxs)
        section_paths.append(section_path)
        section_docs.append(f"{section_path} {title} {joined}".strip())

    para_bm25 = BM25Okapi([tokenize(p["doc"]) for p in paragraphs]) if paragraphs else None
    section_bm25 = BM25Okapi([tokenize(d) for d in section_docs]) if section_docs else None
    return {
        "paragraphs": paragraphs,
        "section_to_paras": dict(section_to_paras),
        "section_paths": section_paths,
        "section_docs": section_docs,
        "para_bm25": para_bm25,
        "section_bm25": section_bm25,
    }


def score_candidates(query_tokens: list[str], bm25: BM25Okapi | None) -> list[float]:
    if not query_tokens or bm25 is None:
        return []
    vocab_q = [q for q in query_tokens if bm25.idf.get(q)]
    if not vocab_q:
        return []
    return list(bm25.get_scores(vocab_q))


def rank_scores(scores: list[float]) -> list[int]:
    return sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)


def rank_candidates(query_tokens: list[str], corpus_size: int, bm25: BM25Okapi | None) -> list[int]:
    if not query_tokens or corpus_size == 0 or bm25 is None:
        return []
    return rank_scores(score_candidates(query_tokens, bm25))


def summarize_hits(ranked_refs: list[str], gold_refs: set[str]) -> dict[str, Any]:
    hit_at_1 = int(bool(ranked_refs) and ranked_refs[0] in gold_refs)
    hit_at_3 = int(any(ref in gold_refs for ref in ranked_refs[:3]))
    hit_at_10 = int(any(ref in gold_refs for ref in ranked_refs[:TOP_K]))
    rr = 0.0
    for rank, ref in enumerate(ranked_refs, start=1):
        if ref in gold_refs:
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
    diff_cache: dict[Path, dict[str, Any]],
    case_text_cache: dict[str, list[str]],
) -> dict[str, Any]:
    split = "test" if row.get("to_snapshot_date", "") >= TEMPORAL_CUTOFF else "dev"
    if row.get("link_status") != "linked_paragraphs":
        return {
            "guide_id": row["guide_id"],
            "case_key": row["case_key"],
            "to_snapshot_date": row["to_snapshot_date"],
            "split": split,
            "gold_paragraph_refs": "",
            "gold_sections": "",
            "case_text_available": False,
            "evaluable": False,
            "paragraph_corpus_size": 0,
            "gold_paragraph_count": 0,
            "top_1_global_base": "",
            "top_1_global_enriched": "",
            "top_1_mention_boosted": "",
            "top_1_section_boosted": "",
            "top_1_two_stage_enriched": "",
            "hit_at_1_global_base": 0,
            "hit_at_3_global_base": 0,
            "hit_at_10_global_base": 0,
            "reciprocal_rank_global_base": 0.0,
            "hit_at_1_global_enriched": 0,
            "hit_at_3_global_enriched": 0,
            "hit_at_10_global_enriched": 0,
            "reciprocal_rank_global_enriched": 0.0,
            "hit_at_1_mention_boosted": 0,
            "hit_at_3_mention_boosted": 0,
            "hit_at_10_mention_boosted": 0,
            "reciprocal_rank_mention_boosted": 0.0,
            "hit_at_1_section_boosted": 0,
            "hit_at_3_section_boosted": 0,
            "hit_at_10_section_boosted": 0,
            "reciprocal_rank_section_boosted": 0.0,
            "hit_at_1_two_stage_enriched": 0,
            "hit_at_3_two_stage_enriched": 0,
            "hit_at_10_two_stage_enriched": 0,
            "reciprocal_rank_two_stage_enriched": 0.0,
            "hit_at_1_oracle_section": 0,
            "hit_at_3_oracle_section": 0,
            "hit_at_10_oracle_section": 0,
            "reciprocal_rank_oracle_section": 0.0,
        }

    diff_file = row.get("diff_file", "")
    diff_path = Path(diff_file)
    if not diff_path.is_absolute():
        diff_path = DIFF_DIR.parent / diff_file
    if diff_path not in diff_cache:
        diff_cache[diff_path] = load_diff_corpus(diff_path)
    corpus = diff_cache[diff_path]
    paragraphs = corpus["paragraphs"]
    para_bm25 = corpus["para_bm25"]
    section_paths = corpus["section_paths"]
    section_docs = corpus["section_docs"]
    section_bm25 = corpus["section_bm25"]
    section_to_paras = corpus["section_to_paras"]

    gold_refs = set(parse_linked_paragraph_refs(row.get("linked_paragraph_refs", "")))
    gold_sections = set(parse_linked_sections(row.get("linked_sections", "")))
    base_query = build_query(row)
    law_tokens = load_law_section_tokens(row.get("case_text_path", ""), case_text_cache)
    enriched_query = base_query + law_tokens
    case_name_norm = normalize_text(row.get("case_name", ""))
    citation_text_norm = normalize_text(row.get("citation_text", ""))
    app_numbers = parse_application_numbers(row.get("application_numbers", ""))

    if not paragraphs or not para_bm25 or not base_query or not gold_refs:
        evaluable = False
        global_base_refs: list[str] = []
        global_enriched_refs = []
        mention_boosted_refs = []
        section_boosted_refs = []
        two_stage_refs = []
        oracle_refs = []
    else:
        evaluable = True
        ranking_query = enriched_query if law_tokens else base_query
        global_base_scores = score_candidates(base_query, para_bm25)
        global_enriched_scores = score_candidates(ranking_query, para_bm25)
        global_base_idxs = rank_scores(global_base_scores)
        global_enriched_idxs = rank_scores(global_enriched_scores)
        global_base_refs = [paragraphs[i]["ref"] for i in global_base_idxs]
        global_enriched_refs = [paragraphs[i]["ref"] for i in global_enriched_idxs]

        mention_boosted_refs = [
            paragraphs[i]["ref"]
            for i in sorted(
                range(len(paragraphs)),
                key=lambda i: (
                    int(bool(case_name_norm) and case_name_norm in paragraphs[i]["normalized_doc"]),
                    int(bool(citation_text_norm) and citation_text_norm in paragraphs[i]["normalized_doc"]),
                    sum(app in paragraphs[i]["doc"] for app in app_numbers),
                    global_enriched_scores[i],
                ),
                reverse=True,
            )
        ]

        section_scores = score_candidates(ranking_query, section_bm25)
        section_ranked_idxs = rank_scores(section_scores)
        section_rank_lookup = {
            section_paths[idx]: rank for rank, idx in enumerate(section_ranked_idxs)
        }
        section_boosted_refs = [
            paragraphs[i]["ref"]
            for i in sorted(
                range(len(paragraphs)),
                key=lambda i: (
                    section_rank_lookup.get(paragraphs[i]["section_path"], len(section_paths)),
                    -global_enriched_scores[i],
                ),
            )
        ]

        # Two-stage: retrieve top sections, then rank paragraphs within them.
        top_section_idxs = rank_candidates(ranking_query, len(section_docs), section_bm25)[
            :SECTION_TOP_K
        ]
        candidate_para_idxs: list[int] = []
        seen: set[int] = set()
        for idx in top_section_idxs:
            section = section_paths[idx]
            for para_idx in section_to_paras.get(section, []):
                if para_idx not in seen:
                    seen.add(para_idx)
                    candidate_para_idxs.append(para_idx)
        if candidate_para_idxs:
            ranked_local = sorted(
                candidate_para_idxs,
                key=lambda i: global_enriched_scores[i],
                reverse=True,
            )
            two_stage_refs = [paragraphs[i]["ref"] for i in ranked_local]
        else:
            two_stage_refs = []

        # Oracle section: upper bound if the right section is already known.
        oracle_para_idxs: list[int] = []
        for section in gold_sections:
            oracle_para_idxs.extend(section_to_paras.get(section, []))
        oracle_para_idxs = list(dict.fromkeys(oracle_para_idxs))
        if oracle_para_idxs:
            oracle_ranked = sorted(
                oracle_para_idxs,
                key=lambda i: global_enriched_scores[i],
                reverse=True,
            )
            oracle_refs = [paragraphs[i]["ref"] for i in oracle_ranked]
        else:
            oracle_refs = []

    base_stats = summarize_hits(global_base_refs, gold_refs)
    enriched_stats = summarize_hits(global_enriched_refs, gold_refs)
    mention_boosted_stats = summarize_hits(mention_boosted_refs, gold_refs)
    section_boosted_stats = summarize_hits(section_boosted_refs, gold_refs)
    two_stage_stats = summarize_hits(two_stage_refs, gold_refs)
    oracle_stats = summarize_hits(oracle_refs, gold_refs)

    return {
        "guide_id": row["guide_id"],
        "case_key": row["case_key"],
        "to_snapshot_date": row["to_snapshot_date"],
        "split": split,
        "gold_paragraph_refs": "|".join(sorted(gold_refs)),
        "gold_sections": "|".join(sorted(gold_sections)),
        "case_text_available": bool(law_tokens),
        "evaluable": evaluable,
        "paragraph_corpus_size": len(paragraphs),
        "gold_paragraph_count": len(gold_refs),
        "top_1_global_base": global_base_refs[0] if global_base_refs else "",
        "top_1_global_enriched": global_enriched_refs[0] if global_enriched_refs else "",
        "top_1_mention_boosted": mention_boosted_refs[0] if mention_boosted_refs else "",
        "top_1_section_boosted": section_boosted_refs[0] if section_boosted_refs else "",
        "top_1_two_stage_enriched": two_stage_refs[0] if two_stage_refs else "",
        **{f"{k}_global_base": v for k, v in base_stats.items()},
        **{f"{k}_global_enriched": v for k, v in enriched_stats.items()},
        **{f"{k}_mention_boosted": v for k, v in mention_boosted_stats.items()},
        **{f"{k}_section_boosted": v for k, v in section_boosted_stats.items()},
        **{f"{k}_two_stage_enriched": v for k, v in two_stage_stats.items()},
        **{f"{k}_oracle_section": v for k, v in oracle_stats.items()},
    }


def summarize(rows: list[dict[str, Any]], suffix: str, label: str) -> dict[str, Any]:
    if not rows:
        return {"label": label, "n": 0}
    n = len(rows)
    return {
        "label": label,
        "n": n,
        "hit_at_1": round(sum(r[f"hit_at_1_{suffix}"] for r in rows) / n, 4),
        "hit_at_3": round(sum(r[f"hit_at_3_{suffix}"] for r in rows) / n, 4),
        "mrr": round(sum(r[f"reciprocal_rank_{suffix}"] for r in rows) / n, 4),
    }


def main() -> None:
    with INPUT_CSV.open() as f:
        rows = list(csv.DictReader(f))

    diff_cache: dict[Path, dict[str, Any]] = {}
    case_text_cache: dict[str, list[str]] = {}
    results = [evaluate_row(row, diff_cache, case_text_cache) for row in rows]

    evaluable = [r for r in results if r["evaluable"]]
    with_text = [r for r in evaluable if r["case_text_available"]]
    dev = [r for r in evaluable if r["split"] == "dev"]
    test = [r for r in evaluable if r["split"] == "test"]

    report = {
        "temporal_cutoff": TEMPORAL_CUTOFF,
        "n_total": len(results),
        "n_evaluable": len(evaluable),
        "n_with_case_text": len(with_text),
        "all": {
            "global_base": summarize(evaluable, "global_base", "all_evaluable"),
            "global_enriched": summarize(evaluable, "global_enriched", "all_evaluable"),
            "mention_boosted": summarize(evaluable, "mention_boosted", "all_evaluable"),
            "section_boosted": summarize(evaluable, "section_boosted", "all_evaluable"),
            "two_stage_enriched": summarize(evaluable, "two_stage_enriched", "all_evaluable"),
            "oracle_section": summarize(evaluable, "oracle_section", "all_evaluable"),
        },
        "dev": {
            "global_base": summarize(dev, "global_base", "dev_evaluable"),
            "global_enriched": summarize(dev, "global_enriched", "dev_evaluable"),
            "mention_boosted": summarize(dev, "mention_boosted", "dev_evaluable"),
            "section_boosted": summarize(dev, "section_boosted", "dev_evaluable"),
            "two_stage_enriched": summarize(dev, "two_stage_enriched", "dev_evaluable"),
            "oracle_section": summarize(dev, "oracle_section", "dev_evaluable"),
        },
        "test": {
            "global_base": summarize(test, "global_base", "test_evaluable"),
            "global_enriched": summarize(test, "global_enriched", "test_evaluable"),
            "mention_boosted": summarize(test, "mention_boosted", "test_evaluable"),
            "section_boosted": summarize(test, "section_boosted", "test_evaluable"),
            "two_stage_enriched": summarize(test, "two_stage_enriched", "test_evaluable"),
            "oracle_section": summarize(test, "oracle_section", "test_evaluable"),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    with PREDICTIONS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print("\n=== LOCATION: paragraph-level linked rows ===")
    for name, metrics in report["all"].items():
        print(
            f"  {name:<20} hit@1={metrics['hit_at_1']:.3f} "
            f"hit@3={metrics['hit_at_3']:.3f} mrr={metrics['mrr']:.3f} (n={metrics['n']})"
        )
    print(f"\nFull report: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
