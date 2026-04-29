from __future__ import annotations

"""
Trigger step evaluation: given a new case, should it cause any guide update at all?

Positives: 805 linked rows from filtered_case_linked_rows.csv (label=1)
Negatives: 3,090 hard negatives from negative_examples.csv (label=0)

Baselines evaluated:
  random           — predict positive at base rate
  importance       — predict positive if importance_level in {1, 2, 3}
  article_overlap  — predict positive if case articles include guide article
  importance+art   — logistic-style combination of above two
  bm25             — max BM25 score of base query against guide sections; threshold sweep

Evaluation: AUROC, F1 at best threshold, precision/recall
Temporal split: to_snapshot_date >= TEMPORAL_CUTOFF as test set
"""

import csv
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

CASE_CATALOG = Path("outputs/case_catalog/cases_catalog.csv")

from rank_bm25 import BM25Okapi

POSITIVE_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
NEGATIVE_CSV = Path("outputs/negatives/negative_examples.csv")
DIFF_BASE = Path("anas-diff-dataset")
OUTPUT_DIR = Path("outputs/trigger")
OUTPUT_JSON = OUTPUT_DIR / "trigger_eval.json"
PREDICTIONS_CSV = OUTPUT_DIR / "trigger_predictions.csv"

TEMPORAL_CUTOFF = "2025-11-25"
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
RANDOM_SEED = 42


def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def base_query(case_name: str, app_numbers: str) -> list[str]:
    return tokenize(case_name + " " + app_numbers.replace("|", " "))


def parse_case_article_roots(article_string: str) -> set[str]:
    roots: set[str] = set()
    for token in (article_string or "").split("|"):
        token = token.strip()
        if not token:
            continue
        for part in token.split("+"):
            part = part.strip()
            if not part:
                continue
            if part.startswith("P"):
                pieces = part.split("-")
                roots.add("-".join(pieces[:2]) if len(pieces) >= 2 else part)
            else:
                roots.add(part.split("-", 1)[0])
    return roots


def parse_guide_article_targets(guide_title: str) -> set[str]:
    title = (guide_title or "").strip()
    if not title.startswith("Article "):
        return set()

    # e.g. "Article 34/35", "Article 6 Civil", "Article 3 Protocol 1"
    if "Protocol" in title:
        match = re.match(r"^Article\s+(\d+)\s+Protocol\s+(\d+)$", title)
        if not match:
            return set()
        article_no, protocol_no = match.groups()
        return {f"P{protocol_no}-{article_no}"}

    match = re.match(r"^Article\s+(\d+(?:/\d+)?)\b", title)
    if not match:
        return set()
    article_token = match.group(1)
    if "/" in article_token:
        return set(article_token.split("/"))
    return {article_token}


def load_case_article_map() -> dict[str, set[str]]:
    with CASE_CATALOG.open() as f:
        rows = list(csv.DictReader(f))
    return {
        row["case_key"]: parse_case_article_roots(row.get("convention_articles", ""))
        for row in rows
    }


def load_diff_index(positive_csv: Path) -> dict[tuple, str]:
    """Map (guide_id, from_date, to_date) -> diff_file path."""
    index: dict[tuple, str] = {}
    with positive_csv.open() as f:
        for row in csv.DictReader(f):
            key = (row["guide_id"], row["from_snapshot_date"], row["to_snapshot_date"])
            if key not in index and row.get("diff_file"):
                index[key] = row["diff_file"]
    return index


def load_diff_corpus(diff_path: Path) -> tuple[list[str], list[str]]:
    data = json.loads(diff_path.read_text())
    sections: dict[str, list[str]] = defaultdict(list)
    titles: dict[str, str] = {}
    for para in data.get("paragraph_changes", []):
        path = para.get("section_path") or ""
        if not path:
            continue
        t = (para.get("section_title") or "").strip()
        if t and path not in titles:
            titles[path] = t
        text_a = (para.get("text_a") or "").strip()
        if text_a:
            sections[path].append(text_a)
    for ev in data.get("section_events", []):
        p = ev.get("path") or ""
        ta = ev.get("title_a") or ""
        if p and ta and p not in titles:
            titles[p] = ta
    paths, docs = [], []
    for p, texts in sections.items():
        body = " ".join(texts)
        doc = f"{p} {titles.get(p, '')} {body}".strip()
        paths.append(p)
        docs.append(doc)
    return paths, docs


def bm25_max_score(query: list[str], bm25: BM25Okapi) -> float:
    if not query or bm25 is None:
        return 0.0
    vocab_q = [q for q in query if bm25.idf.get(q)]
    if not vocab_q:
        return 0.0
    scores = bm25.get_scores(vocab_q)
    return float(max(scores)) if len(scores) > 0 else 0.0


def auroc(labels: list[int], scores: list[float]) -> float:
    """Compute AUROC via trapezoidal rule."""
    pairs = sorted(zip(scores, labels), reverse=True)
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    tp = fp = 0
    auc = 0.0
    prev_fp = 0
    for _, label in pairs:
        if label == 1:
            tp += 1
        else:
            fp += 1
            auc += tp * (fp - prev_fp)
            prev_fp = fp
    return auc / (n_pos * n_neg)


def best_f1(labels: list[int], scores: list[float]) -> dict[str, float]:
    """Find threshold maximizing F1."""
    thresholds = sorted(set(scores))
    best = {"f1": 0.0, "threshold": 0.0, "precision": 0.0, "recall": 0.0}
    for thr in thresholds:
        preds = [1 if s >= thr else 0 for s in scores]
        tp = sum(p == 1 and l == 1 for p, l in zip(preds, labels))
        fp = sum(p == 1 and l == 0 for p, l in zip(preds, labels))
        fn = sum(p == 0 and l == 1 for p, l in zip(preds, labels))
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        if f1 > best["f1"]:
            best = {"f1": round(f1, 4), "threshold": thr,
                    "precision": round(prec, 4), "recall": round(rec, 4)}
    return best


def eval_model(labels: list[int], scores: list[float], name: str) -> dict[str, Any]:
    auc = auroc(labels, scores)
    bf1 = best_f1(labels, scores)
    return {
        "model": name,
        "n": len(labels),
        "n_pos": sum(labels),
        "auroc": round(auc, 4),
        **bf1,
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    case_article_map = load_case_article_map()
    diff_index = load_diff_index(POSITIVE_CSV)
    diff_cache: dict[Path, tuple[list[str], Any]] = {}

    def get_bm25(guide_id: str, from_d: str, to_d: str):
        key = (guide_id, from_d, to_d)
        diff_file = diff_index.get(key)
        if not diff_file:
            return None
        diff_path = Path(diff_file)
        if not diff_path.is_absolute():
            diff_path = DIFF_BASE.parent / diff_file
        if diff_path not in diff_cache:
            try:
                paths, docs = load_diff_corpus(diff_path)
                diff_cache[diff_path] = (
                    paths, BM25Okapi([tokenize(d) for d in docs])
                ) if paths else ([], None)
            except Exception:
                diff_cache[diff_path] = ([], None)
        _, bm25 = diff_cache[diff_path]
        return bm25

    rows: list[dict[str, Any]] = []

    # Positives
    with POSITIVE_CSV.open() as f:
        for r in csv.DictReader(f):
            if r.get("link_status") != "linked_paragraphs":
                continue
            bm25 = get_bm25(r["guide_id"], r["from_snapshot_date"], r["to_snapshot_date"])
            q = base_query(r.get("case_name", ""), r.get("application_numbers", ""))
            case_articles = case_article_map.get(r["case_key"], set())
            guide_targets = parse_guide_article_targets(r.get("guide_title", ""))
            rows.append({
                "guide_id": r["guide_id"],
                "guide_title": r.get("guide_title", ""),
                "case_key": r["case_key"],
                "to_snapshot_date": r["to_snapshot_date"],
                "label": 1,
                "importance_level": r.get("hudoc_importance_level", ""),
                "case_articles": "|".join(sorted(case_articles)),
                "guide_article_targets": "|".join(sorted(guide_targets)),
                "article_overlap": int(bool(case_articles & guide_targets)),
                "bm25_max_score": bm25_max_score(q, bm25),
                "split": "test" if r["to_snapshot_date"] >= TEMPORAL_CUTOFF else "dev",
            })

    # Negatives
    with NEGATIVE_CSV.open() as f:
        for r in csv.DictReader(f):
            bm25 = get_bm25(r["guide_id"], r["from_snapshot_date"], r["to_snapshot_date"])
            q = base_query(r.get("case_name", ""), r.get("application_numbers", ""))
            case_articles = parse_case_article_roots(r.get("convention_articles", ""))
            guide_targets = parse_guide_article_targets(r.get("guide_title", ""))
            rows.append({
                "guide_id": r["guide_id"],
                "guide_title": r.get("guide_title", ""),
                "case_key": r.get("case_key", ""),
                "to_snapshot_date": r["to_snapshot_date"],
                "label": 0,
                "importance_level": r.get("hudoc_importance_level", ""),
                "case_articles": "|".join(sorted(case_articles)),
                "guide_article_targets": "|".join(sorted(guide_targets)),
                "article_overlap": int(bool(case_articles & guide_targets)),
                "bm25_max_score": bm25_max_score(q, bm25),
                "split": "test" if r["to_snapshot_date"] >= TEMPORAL_CUTOFF else "dev",
            })

    rng = random.Random(RANDOM_SEED)
    base_rate = sum(r["label"] for r in rows) / len(rows)

    # Model scores
    def random_scores() -> list[float]:
        return [rng.random() for _ in rows]

    def importance_scores() -> list[float]:
        # Level 1=Grand Chamber, 2=key case → strong signal; 3,4,empty → weak
        priority = {"1": 1.0, "2": 0.8, "3": 0.3, "4": 0.1, "": 0.2}
        return [priority.get(r["importance_level"], 0.2) for r in rows]

    def article_overlap_scores() -> list[float]:
        return [float(r["article_overlap"]) for r in rows]

    def bm25_scores() -> list[float]:
        return [r["bm25_max_score"] for r in rows]

    labels = [r["label"] for r in rows]

    models = {
        "random": random_scores(),
        "importance": importance_scores(),
        "article_overlap": article_overlap_scores(),
        "bm25": bm25_scores(),
    }

    # Add combined features.
    imp_s = importance_scores()
    art_s = article_overlap_scores()
    bm25_s = bm25_scores()
    # Normalize bm25 to [0,1] for combination
    bm25_max = max(bm25_s) if bm25_s else 1.0
    bm25_norm = [s / bm25_max if bm25_max > 0 else 0.0 for s in bm25_s]
    models["importance+art"] = [
        0.65 * i + 0.35 * a for i, a in zip(imp_s, art_s)
    ]
    models["importance+bm25"] = [
        0.5 * i + 0.5 * b for i, b in zip(imp_s, bm25_norm)
    ]

    def split_eval(split: str | None) -> list[dict[str, Any]]:
        if split:
            idx = [i for i, r in enumerate(rows) if r["split"] == split]
        else:
            idx = list(range(len(rows)))
        sub_labels = [labels[i] for i in idx]
        results = []
        for name, scores in models.items():
            sub_scores = [scores[i] for i in idx]
            results.append(eval_model(sub_labels, sub_scores, name))
        return results

    report: dict[str, Any] = {
        "n_total": len(rows),
        "n_positive": sum(labels),
        "n_negative": len(rows) - sum(labels),
        "base_rate": round(base_rate, 4),
        "temporal_cutoff": TEMPORAL_CUTOFF,
        "article_feature_coverage": {
            "positive_with_case_articles": sum(
                1 for r in rows if r["label"] == 1 and r["case_articles"]
            ),
            "negative_with_case_articles": sum(
                1 for r in rows if r["label"] == 0 and r["case_articles"]
            ),
            "rows_with_guide_article_targets": sum(
                1 for r in rows if r["guide_article_targets"]
            ),
            "positive_article_overlap": sum(
                1 for r in rows if r["label"] == 1 and r["article_overlap"] == 1
            ),
            "negative_article_overlap": sum(
                1 for r in rows if r["label"] == 0 and r["article_overlap"] == 1
            ),
        },
        "all": split_eval(None),
        "dev": split_eval("dev"),
        "test": split_eval("test"),
    }

    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    # Add per-row model scores for predictions CSV
    for i, row in enumerate(rows):
        for name, scores in models.items():
            row[f"score_{name}"] = round(scores[i], 6)

    fieldnames = list(rows[0].keys())
    with PREDICTIONS_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n=== TRIGGER: all {len(rows)} rows (base rate {base_rate:.3f}) ===")
    print(f"{'model':<18} auroc   F1      prec    rec")
    for m in report["all"]:
        print(f"  {m['model']:<16} {m['auroc']:.3f}   {m['f1']:.3f}   "
              f"{m['precision']:.3f}   {m['recall']:.3f}")

    print(f"\n=== TRIGGER: dev split ===")
    for m in report["dev"]:
        print(f"  {m['model']:<16} auroc={m['auroc']:.3f}  F1={m['f1']:.3f}  (n={m['n']})")

    print(f"\n=== TRIGGER: test split ===")
    for m in report["test"]:
        print(f"  {m['model']:<16} auroc={m['auroc']:.3f}  F1={m['f1']:.3f}  (n={m['n']})")

    print(f"\nFull report: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
