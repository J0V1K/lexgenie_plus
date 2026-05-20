"""
retrieval_eval_v2.py — Improved retrieval benchmark for LexGenie+
=================================================================
Drop-in replacement for retrieval_eval.py with 3 additional retrieval models:

  bm25_plus   — BM25 with enriched query (law section + extracted Convention articles)
  dense       — OpenAI text-embedding-3-small bi-encoder (cosine similarity)
  hybrid      — Reciprocal Rank Fusion of bm25_plus + dense

All original models (random, base, enriched, law) are preserved for comparison.

New outputs:
  outputs/prototype/retrieval_eval_v2.json        — full report
  outputs/prototype/retrieval_predictions_v2.csv  — per-row predictions
  outputs/prototype/embed_cache/                  — embedding cache (.npz per diff file)

Requirements:
  pip install rank-bm25 openai numpy scipy

Usage:
  export OPENAI_API_KEY=sk-...
  python retrieval_eval_v2.py

  # Skip embedding (BM25+ only, no API cost):
  python retrieval_eval_v2.py --no-embed

  # Limit rows for quick smoke test:
  python retrieval_eval_v2.py --max-rows 50
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from rank_bm25 import BM25Okapi

# ── Optional OpenAI import ────────────────────────────────────────────────────
try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

# ── Re-use helpers from the original pipeline ─────────────────────────────────
from pipeline_support import semantic_to_date, temporal_split

# ── Paths ─────────────────────────────────────────────────────────────────────
INPUT_CSV     = Path("outputs/prototype/filtered_case_linked_rows.csv")
DIFF_DIR      = Path("anas-diff-dataset")
OUTPUT_DIR    = Path("outputs/prototype")
OUTPUT_JSON   = OUTPUT_DIR / "retrieval_eval_v2.json"
PREDICTIONS_CSV = OUTPUT_DIR / "retrieval_predictions_v2.csv"
EMBED_CACHE_DIR = OUTPUT_DIR / "embed_cache"

# ── Constants ─────────────────────────────────────────────────────────────────
TOP_K              = 10
TEMPORAL_CUTOFF    = "2025-08-31"
EMBED_MODEL        = "text-embedding-3-small"
EMBED_BATCH_SIZE   = 64          # passages per OpenAI call
EMBED_QUERY_BATCH  = 32          # queries per OpenAI call
RRF_K              = 60          # standard RRF constant
OPENAI_RETRY_WAIT  = 2.0         # seconds between retries on rate limit

# ── Regex ─────────────────────────────────────────────────────────────────────
TOKEN_RE    = re.compile(r"[A-Za-z0-9]+")

# Matches "Article 3", "Art. 8", "Article 1 of Protocol No. 1", etc.
ARTICLE_RE  = re.compile(
    r"\b(?:Article|Art\.?)\s+(\d+(?:\s+of\s+Protocol\s+No\.?\s*\d+)?)",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# TEXT UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in TOKEN_RE.findall(text or "")]


def extract_convention_articles(text: str) -> list[str]:
    """
    Pull out all Convention article references from a text.
    Returns tokens like ['article3', 'article8', 'article1ofprotocolno1'].
    These are added to BM25 queries/docs to boost structural signal.
    """
    found = []
    for m in ARTICLE_RE.finditer(text):
        normalized = re.sub(r"\s+", "", m.group(0)).lower()  # "article3"
        found.append(normalized)
    return found


def parse_section_path(linked_section: str) -> str:
    idx = linked_section.find(": ")
    return linked_section[:idx] if idx >= 0 else linked_section


def parse_linked_sections(value: str) -> list[str]:
    if not value:
        return []
    return [parse_section_path(item) for item in value.split("|") if item]


# ══════════════════════════════════════════════════════════════════════════════
# CORPUS LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_diff_corpus(diff_path: Path) -> tuple[list[str], list[str]]:
    """
    Return (section_paths, section_docs).
    Docs include both text_a AND text_b so that post-update language is also
    searchable (helps when a citation was added with the new version).
    Also injects extracted Convention article tokens into each doc.
    """
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
        # text_a only (pre-update) — text_b risks data leakage since the
        # updated guide may already cite the case explicitly.
        text = (para.get("text_a") or "").strip()
        if text:
            sections[path].append(text)

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
        # Inject article tokens directly into the doc string
        article_tokens = " ".join(extract_convention_articles(body))
        doc = f"{path} {title} {body} {article_tokens}".strip()
        paths.append(path)
        docs.append(doc)
    return paths, docs


# ══════════════════════════════════════════════════════════════════════════════
# QUERY BUILDING
# ══════════════════════════════════════════════════════════════════════════════

def build_base_query(row: dict[str, str]) -> list[str]:
    """Original base query: case name + app numbers + citation text."""
    parts: list[str] = []
    parts.append(row.get("case_name", ""))
    app_nums = row.get("application_numbers", "")
    if app_nums:
        parts.extend(app_nums.split("|"))
    citation_text = row.get("citation_text", "")
    if citation_text:
        parts.append(citation_text)
    return tokenize(" ".join(parts))


def load_law_section_text(path: str, cache: dict[str, str]) -> str:
    """
    Extract the THE LAW section as raw text (not tokenized).
    Used both for BM25 and for embedding.
    Smarter extraction: stops at FOR THESE REASONS / OPERATIVE PROVISIONS,
    but also caps at 400 lines (up from 250) to avoid cutting long sections.
    """
    if not path:
        return ""
    if path in cache:
        return cache[path]
    p = Path(path)
    if not p.exists():
        cache[path] = ""
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
        cache[path] = ""
        return ""
    end = op_start if op_start else min(law_start + 400, len(lines))
    result = "\n".join(lines[law_start:end])
    cache[path] = result
    return result


def build_bm25_plus_query(row: dict[str, str], law_text: str) -> list[str]:
    """
    Enhanced query = base tokens + law tokens + extracted article tokens.
    The article tokens are added explicitly (they're also in the docs)
    so BM25 can match on "article3" even if surface forms differ.
    """
    base = build_base_query(row)
    law_tokens = tokenize(law_text)
    article_tokens = extract_convention_articles(law_text)
    return base + law_tokens + article_tokens


# ══════════════════════════════════════════════════════════════════════════════
# OPENAI EMBEDDING
# ══════════════════════════════════════════════════════════════════════════════

EMBED_MAX_CHARS = 24000   # ~8000 tokens at ~3 chars/token — safe limit for text-embedding-3-small


def _truncate(text: str) -> str:
    """Truncate text to stay within the OpenAI 8192-token limit."""
    return text[:EMBED_MAX_CHARS] if len(text) > EMBED_MAX_CHARS else text


def _embed_batch(client: "OpenAI", texts: list[str]) -> np.ndarray:
    """Call OpenAI embeddings API with retry on rate limit."""
    texts = [_truncate(t) for t in texts]   # ensure no text exceeds token limit
    for attempt in range(5):
        try:
            resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
            vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
            return vecs
        except Exception as e:
            if "rate" in str(e).lower() and attempt < 4:
                wait = OPENAI_RETRY_WAIT * (2 ** attempt)
                print(f"    Rate limit, waiting {wait:.0f}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Embedding failed after 5 retries")


def embed_texts(client: "OpenAI", texts: list[str], batch_size: int) -> np.ndarray:
    """Embed a list of texts in batches, return (n, dim) float32 array."""
    all_vecs = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        vecs = _embed_batch(client, batch)
        all_vecs.append(vecs)
    return np.vstack(all_vecs)


def cosine_scores(query_vec: np.ndarray, corpus_vecs: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a single query and all corpus vectors."""
    q = query_vec / (np.linalg.norm(query_vec) + 1e-10)
    norms = np.linalg.norm(corpus_vecs, axis=1, keepdims=True) + 1e-10
    normed = corpus_vecs / norms
    return normed @ q


# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDING CACHE (per diff file)
# ══════════════════════════════════════════════════════════════════════════════

def cache_path_for_diff(diff_path: Path) -> Path:
    """Each diff file gets its own .npz cache."""
    return EMBED_CACHE_DIR / (diff_path.stem + ".npz")


def load_embed_cache(diff_path: Path) -> tuple[list[str], np.ndarray] | None:
    """Load cached embeddings for a diff file. Returns (paths, vecs) or None."""
    cp = cache_path_for_diff(diff_path)
    if not cp.exists():
        return None
    data = np.load(cp, allow_pickle=True)
    paths = data["paths"].tolist()
    vecs = data["vecs"]
    return paths, vecs


def save_embed_cache(diff_path: Path, paths: list[str], vecs: np.ndarray) -> None:
    EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = cache_path_for_diff(diff_path)
    np.savez_compressed(cp, paths=np.array(paths), vecs=vecs)


# ══════════════════════════════════════════════════════════════════════════════
# SCORING
# ══════════════════════════════════════════════════════════════════════════════

def hits_and_rr(ranked_paths: list[str], gold_set: set[str]) -> dict[str, Any]:
    hit1  = 1 if ranked_paths and ranked_paths[0] in gold_set else 0
    hit3  = 1 if any(p in gold_set for p in ranked_paths[:3]) else 0
    hit10 = 1 if any(p in gold_set for p in ranked_paths[:TOP_K]) else 0
    rr = 0.0
    for rank, path in enumerate(ranked_paths, start=1):
        if path in gold_set:
            rr = 1.0 / rank
            break
    return {"hit_at_1": hit1, "hit_at_3": hit3, "hit_at_10": hit10,
            "reciprocal_rank": rr, "ranked_paths": ranked_paths}


def rank_bm25(query: list[str], paths: list[str], bm25: BM25Okapi,
              gold_set: set[str]) -> dict[str, Any]:
    scores = bm25.get_scores(query)
    ranked_idx = sorted(range(len(paths)), key=lambda i: scores[i], reverse=True)
    ranked = [paths[i] for i in ranked_idx]
    return hits_and_rr(ranked, gold_set)


def rank_dense(query_vec: np.ndarray, corpus_paths: list[str],
               corpus_vecs: np.ndarray, gold_set: set[str]) -> dict[str, Any]:
    scores = cosine_scores(query_vec, corpus_vecs)
    ranked_idx = np.argsort(-scores)
    ranked = [corpus_paths[i] for i in ranked_idx]
    return hits_and_rr(ranked, gold_set)


def rank_hybrid_rrf(bm25_paths: list[str], dense_paths: list[str],
                    gold_set: set[str], k: int = RRF_K) -> dict[str, Any]:
    """
    Reciprocal Rank Fusion of two ranked lists.
    score(d) = 1/(k + rank_bm25(d)) + 1/(k + rank_dense(d))
    """
    scores: dict[str, float] = defaultdict(float)
    for rank, path in enumerate(bm25_paths, start=1):
        scores[path] += 1.0 / (k + rank)
    for rank, path in enumerate(dense_paths, start=1):
        scores[path] += 1.0 / (k + rank)
    ranked = sorted(scores, key=lambda p: scores[p], reverse=True)
    return hits_and_rr(ranked, gold_set)


def random_score(paths: list[str], gold_set: set[str], seed: int) -> dict[str, Any]:
    shuffled = paths[:]
    random.Random(seed).shuffle(shuffled)
    return hits_and_rr(shuffled, gold_set)


# ══════════════════════════════════════════════════════════════════════════════
# ZERO RESULT (unevaluable rows)
# ══════════════════════════════════════════════════════════════════════════════

_MODELS = ["random", "base", "enriched", "law", "bm25_plus", "dense", "hybrid"]

def _zero_row(row: dict[str, str], split: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "guide_id": row["guide_id"],
        "case_key": row.get("case_key", ""),
        "to_guide_version_date": semantic_to_date(row),
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
    }
    for m in _MODELS:
        result[f"hit_at_1_{m}"]        = 0
        result[f"hit_at_3_{m}"]        = 0
        result[f"hit_at_10_{m}"]       = 0
        result[f"reciprocal_rank_{m}"] = 0.0
    result["top_1_base"]     = ""
    result["top_1_law"]      = ""
    result["top_1_bm25_plus"] = ""
    result["top_1_dense"]    = ""
    result["top_1_hybrid"]   = ""
    return result


# ══════════════════════════════════════════════════════════════════════════════
# PER-ROW EVALUATION
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_row(
    row: dict[str, str],
    diff_cache: dict[Path, tuple[list[str], Any]],       # path → (paths, bm25)
    embed_corpus_cache: dict[Path, tuple[list[str], np.ndarray]],  # path → (paths, vecs)
    law_text_cache: dict[str, str],
    row_idx: int,
    openai_client: "OpenAI | None",
    use_embed: bool,
) -> dict[str, Any]:

    split = temporal_split(row, TEMPORAL_CUTOFF)
    is_linked = row.get("link_status") == "linked_paragraphs"
    gold_sections = parse_linked_sections(row.get("linked_sections", ""))

    if not is_linked or not gold_sections:
        return _zero_row(row, split)

    # ── Load diff corpus ──────────────────────────────────────────────────────
    diff_file = row.get("diff_file", "")
    diff_path = Path(diff_file) if diff_file else None
    if diff_path and not diff_path.is_absolute():
        diff_path = DIFF_DIR.parent / diff_file

    if diff_path and diff_path not in diff_cache:
        paths, docs = load_diff_corpus(diff_path)
        if paths:
            tokenized = [tokenize(d) for d in docs]
            diff_cache[diff_path] = (paths, BM25Okapi(tokenized), docs)
        else:
            diff_cache[diff_path] = ([], None, [])

    corpus_entry = diff_cache.get(diff_path) if diff_path else None
    if corpus_entry:
        paths, bm25, docs = corpus_entry
    else:
        paths, bm25, docs = [], None, []

    gold_set = set(gold_sections)
    gold_in_corpus = any(g in paths for g in gold_sections) if paths else False

    # ── Queries ───────────────────────────────────────────────────────────────
    base_query  = build_base_query(row)
    law_text    = load_law_section_text(row.get("case_text_path", ""), law_text_cache)
    law_tokens  = tokenize(law_text)
    bm25_plus_query = build_bm25_plus_query(row, law_text)

    has_law_text = bool(law_text)

    # Original enriched = base + full case text (kept for backward compat)
    case_text_path = row.get("case_text_path", "")
    case_tokens: list[str] = []
    if case_text_path:
        p = Path(case_text_path)
        if p.exists():
            case_tokens = tokenize(p.read_text(errors="replace"))

    if not paths or bm25 is None or not base_query:
        result = _zero_row(row, split)
        result["gold_in_corpus"]     = gold_in_corpus
        result["corpus_size"]        = len(paths)
        result["case_text_available"] = bool(case_tokens)
        result["law_text_available"]  = has_law_text
        return result

    # ── BM25 models ──────────────────────────────────────────────────────────
    rand_result     = random_score(paths, gold_set, seed=row_idx)
    base_result     = rank_bm25(base_query, paths, bm25, gold_set)
    enriched_result = (rank_bm25(base_query + case_tokens, paths, bm25, gold_set)
                       if case_tokens else base_result)
    law_result      = (rank_bm25(base_query + law_tokens, paths, bm25, gold_set)
                       if has_law_text else base_result)
    bm25_plus_result = (rank_bm25(bm25_plus_query, paths, bm25, gold_set)
                        if has_law_text else base_result)

    # ── Dense + Hybrid ────────────────────────────────────────────────────────
    dense_result  = {"hit_at_1": 0, "hit_at_3": 0, "hit_at_10": 0,
                     "reciprocal_rank": 0.0, "ranked_paths": []}
    hybrid_result = dense_result.copy()

    if use_embed and openai_client and diff_path:
        # Load or build corpus embeddings
        if diff_path not in embed_corpus_cache:
            cached = load_embed_cache(diff_path)
            if cached and len(cached[0]) == len(paths):
                embed_corpus_cache[diff_path] = cached
            else:
                print(f"  [embed] Building corpus for {diff_path.name} "
                      f"({len(docs)} sections)...")
                vecs = embed_texts(openai_client, docs, EMBED_BATCH_SIZE)
                save_embed_cache(diff_path, paths, vecs)
                embed_corpus_cache[diff_path] = (paths, vecs)

        emb_paths, corpus_vecs = embed_corpus_cache[diff_path]

        # Query embedding: use law_text if available, else base query text
        query_text = (law_text if has_law_text
                      else " ".join([row.get("case_name", ""),
                                     row.get("citation_text", "")]))
        query_vec = _embed_batch(openai_client, [query_text])[0]

        dense_result  = rank_dense(query_vec, emb_paths, corpus_vecs, gold_set)
        hybrid_result = rank_hybrid_rrf(
            bm25_plus_result["ranked_paths"],
            dense_result["ranked_paths"],
            gold_set,
        )

    # ── Assemble output row ───────────────────────────────────────────────────
    def _fill(result: dict, suffix: str, out: dict) -> None:
        out[f"hit_at_1_{suffix}"]        = result["hit_at_1"]
        out[f"hit_at_3_{suffix}"]        = result["hit_at_3"]
        out[f"hit_at_10_{suffix}"]       = result["hit_at_10"]
        out[f"reciprocal_rank_{suffix}"] = result["reciprocal_rank"]

    out: dict[str, Any] = {
        "guide_id":                   row["guide_id"],
        "case_key":                   row.get("case_key", ""),
        "to_guide_version_date":      semantic_to_date(row),
        "to_snapshot_date":           row.get("to_snapshot_date", ""),
        "citation_change":            row.get("citation_change", ""),
        "gold_sections":              "|".join(gold_sections),
        "gold_in_corpus":             gold_in_corpus,
        "case_text_available":        bool(case_tokens),
        "law_text_available":         has_law_text,
        "corpus_size":                len(paths),
        "split":                      split,
        "link_status":                row.get("link_status", ""),
        "strict_citation_field_match": row.get("strict_citation_field_match", "false"),
        "evaluable":                  True,
    }

    for suffix, res in [
        ("random",    rand_result),
        ("base",      base_result),
        ("enriched",  enriched_result),
        ("law",       law_result),
        ("bm25_plus", bm25_plus_result),
        ("dense",     dense_result),
        ("hybrid",    hybrid_result),
    ]:
        _fill(res, suffix, out)

    out["top_1_base"]      = (base_result["ranked_paths"][0]
                              if base_result.get("ranked_paths") else "")
    out["top_1_law"]       = (law_result["ranked_paths"][0]
                              if law_result.get("ranked_paths") else "")
    out["top_1_bm25_plus"] = (bm25_plus_result["ranked_paths"][0]
                              if bm25_plus_result.get("ranked_paths") else "")
    out["top_1_dense"]     = (dense_result["ranked_paths"][0]
                              if dense_result.get("ranked_paths") else "")
    out["top_1_hybrid"]    = (hybrid_result["ranked_paths"][0]
                              if hybrid_result.get("ranked_paths") else "")
    return out


# ══════════════════════════════════════════════════════════════════════════════
# REPORTING
# ══════════════════════════════════════════════════════════════════════════════

def summarize(rows: list[dict[str, Any]], suffix: str, label: str) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "label": label}
    n = len(rows)
    return {
        "label": label,
        "n": n,
        "hit_at_1": round(sum(r[f"hit_at_1_{suffix}"] for r in rows) / n, 4),
        "hit_at_3": round(sum(r[f"hit_at_3_{suffix}"] for r in rows) / n, 4),
        "mrr":      round(sum(r[f"reciprocal_rank_{suffix}"] for r in rows) / n, 4),
    }


def build_report(results: list[dict[str, Any]], use_embed: bool) -> dict[str, Any]:
    all_rows  = results
    linked    = [r for r in results if r["link_status"] == "linked_paragraphs"]
    evaluable = [r for r in linked if r["evaluable"]]
    strict    = [r for r in evaluable
                 if r["strict_citation_field_match"] == "true"]
    dev_eval  = [r for r in evaluable if r["split"] == "dev"]
    test_eval = [r for r in evaluable if r["split"] == "test"]
    dev_all   = [r for r in all_rows  if r["split"] == "dev"]
    test_all  = [r for r in all_rows  if r["split"] == "test"]

    n_gold_in_corpus = sum(1 for r in evaluable if r["gold_in_corpus"])

    models = ["random", "base", "enriched", "law", "bm25_plus"]
    if use_embed:
        models += ["dense", "hybrid"]

    def section(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
        return {m: summarize(rows, m, label) for m in models}

    return {
        "temporal_cutoff":   TEMPORAL_CUTOFF,
        "embed_model":       EMBED_MODEL if use_embed else None,
        "n_total":           len(all_rows),
        "n_linked":          len(linked),
        "n_evaluable":       len(evaluable),
        "gold_in_corpus_rate_evaluable": (
            round(n_gold_in_corpus / len(evaluable), 4) if evaluable else 0
        ),
        "unconditional_all":     section(all_rows,  "all rows"),
        "conditional_linked":    section(evaluable, "linked+evaluable"),
        "conditional_strict":    section(strict,    "linked+strict"),
        "dev_conditional":       section(dev_eval,  "dev linked"),
        "test_conditional":      section(test_eval, "test linked"),
        "dev_unconditional":     section(dev_all,   "dev all"),
        "test_unconditional":    section(test_all,  "test all"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def fmt(d: dict) -> str:
    if "hit_at_1" not in d:
        return f"n={d.get('n', 0)}"
    return (f"hit@1={d['hit_at_1']:.3f}  hit@3={d['hit_at_3']:.3f}  "
            f"mrr={d['mrr']:.3f}  (n={d['n']})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-embed",  action="store_true",
                        help="Skip dense/hybrid models (no OpenAI calls)")
    parser.add_argument("--max-rows",  type=int, default=None,
                        help="Limit number of input rows (for quick testing)")
    args = parser.parse_args()

    use_embed = not args.no_embed and _OPENAI_AVAILABLE

    # ── OpenAI client ─────────────────────────────────────────────────────────
    openai_client = None
    if use_embed:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print("WARNING: OPENAI_API_KEY not set — skipping dense/hybrid models.")
            use_embed = False
        else:
            openai_client = OpenAI(api_key=api_key)
            print(f"OpenAI client ready (model: {EMBED_MODEL})")

    # ── Load input ────────────────────────────────────────────────────────────
    with INPUT_CSV.open() as fh:
        all_rows = list(csv.DictReader(fh))
    if args.max_rows:
        all_rows = all_rows[:args.max_rows]

    print(f"Loaded {len(all_rows)} rows from {INPUT_CSV}")

    # ── Caches ────────────────────────────────────────────────────────────────
    diff_cache:         dict[Path, Any]                    = {}
    embed_corpus_cache: dict[Path, tuple[list[str], Any]]  = {}
    law_text_cache:     dict[str, str]                     = {}

    # ── Evaluate ──────────────────────────────────────────────────────────────
    results: list[dict[str, Any]] = []
    for idx, row in enumerate(all_rows):
        if idx % 100 == 0:
            print(f"  row {idx}/{len(all_rows)}...")
        result = evaluate_row(
            row, diff_cache, embed_corpus_cache, law_text_cache,
            idx, openai_client, use_embed,
        )
        results.append(result)

    # ── Report ────────────────────────────────────────────────────────────────
    report = build_report(results, use_embed)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    fieldnames = list(results[0].keys())
    with PREDICTIONS_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # ── Print headline numbers ────────────────────────────────────────────────
    models_to_print = ["random", "base", "enriched", "law", "bm25_plus"]
    if use_embed:
        models_to_print += ["dense", "hybrid"]

    print(f"\n{'='*65}")
    print(f"UNCONDITIONAL (all {len(results)} rows; unlinked score 0)")
    print(f"{'='*65}")
    for m in models_to_print:
        print(f"  {m:12s}: {fmt(report['unconditional_all'][m])}")

    print(f"\n{'='*65}")
    print("CONDITIONAL (linked+evaluable rows only)")
    print(f"{'='*65}")
    for m in models_to_print:
        print(f"  {m:12s}: {fmt(report['conditional_linked'][m])}")

    print(f"\n{'='*65}")
    print("TEMPORAL SPLIT (conditional)")
    print(f"{'='*65}")
    for m in ["law", "bm25_plus"] + (["dense", "hybrid"] if use_embed else []):
        d = report["dev_conditional"][m]
        t = report["test_conditional"][m]
        print(f"  dev  {m:10s}: {fmt(d)}")
        print(f"  test {m:10s}: {fmt(t)}")

    print(f"\nFull report → {OUTPUT_JSON}")
    print(f"Predictions → {PREDICTIONS_CSV}")
    if use_embed:
        print(f"Embed cache → {EMBED_CACHE_DIR}/")


if __name__ == "__main__":
    main()