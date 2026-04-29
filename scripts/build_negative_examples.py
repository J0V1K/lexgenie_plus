from __future__ import annotations

"""
For each guide transition window (from_date -> to_date), query HUDOC for
judgments published in that window that were NOT added to that guide.
These become negative examples for the novelty-detection task.

Output: outputs/negatives/negative_examples.csv
"""

import csv
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

INPUT_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
OUTPUT_DIR = Path("outputs/negatives")
OUTPUT_CSV = OUTPUT_DIR / "negative_examples.csv"
REPORT_JSON = OUTPUT_DIR / "negative_examples_report.json"
CACHE_JSON = OUTPUT_DIR / "hudoc_negatives_cache.json"

HUDOC_ENDPOINT = "https://hudoc.echr.coe.int/app/query/results"
HUDOC_SELECT = ",".join([
    "itemid", "appno", "docname", "doctype", "kpdate", "importance",
    "article", "respondent", "kpdateastext", "ecli", "conclusion",
    "languageisocode",
])
HUDOC_RANKING_MODEL_ID = "11111111-0000-0000-0000-000000000000"

# Doctypes to include as potential negatives (actual legal documents)
VALID_DOCTYPES = {"HEJUD", "HEDEC", "HFDEC", "HFJUD"}

# Per-window negative cap to keep the dataset manageable
MAX_NEGATIVES_PER_TRANSITION = 30

REQUEST_TIMEOUT = 30
RETRY_COUNT = 3
RETRY_SLEEP = 1.5
INTER_REQUEST_SLEEP = 0.4


OUTPUT_FIELDS = [
    "guide_id",
    "guide_title",
    "from_snapshot_date",
    "to_snapshot_date",
    "case_key",
    "case_name",
    "application_numbers",
    "hudoc_itemid",
    "hudoc_importance_level",
    "hudoc_doctype",
    "hudoc_conclusion",
    "convention_articles",
    "judgment_year",
    "label",
    "negative_reason",
]


def load_transitions(
    csv_path: Path,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Return transition_key -> {guide metadata, positive_appnos set}."""
    transitions: dict[tuple[str, str, str], dict[str, Any]] = {}
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            key = (
                row["guide_id"],
                row["from_snapshot_date"],
                row["to_snapshot_date"],
            )
            if key not in transitions:
                transitions[key] = {
                    "guide_id": row["guide_id"],
                    "guide_title": row["guide_title"],
                    "from_snapshot_date": row["from_snapshot_date"],
                    "to_snapshot_date": row["to_snapshot_date"],
                    "positive_appnos": set(),
                }
            for appno in row.get("application_numbers", "").split("|"):
                appno = appno.strip()
                if appno:
                    transitions[key]["positive_appnos"].add(appno.lower())
    return transitions


def load_cache() -> dict[str, Any]:
    if CACHE_JSON.exists():
        return json.loads(CACHE_JSON.read_text())
    return {}


def save_cache(cache: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_JSON.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def hudoc_date_query(from_date: str, to_date: str, length: int) -> str:
    # HUDOC kpdate range query (Solr range syntax)
    date_filter = (
        f"(kpdate:[{from_date}T00:00:00.000Z TO {to_date}T23:59:59.999Z])"
    )
    doctype_filter = "(doctype:HEJUD OR doctype:HEDEC OR doctype:HFDEC OR doctype:HFJUD)"
    lang_filter = "(languageisocode:ENG)"
    q = f"(contentsitename=ECHR) AND {date_filter} AND {doctype_filter} AND {lang_filter}"
    encoded = quote(q, safe="():/[] ").replace(" ", "%20")
    return (
        f"{HUDOC_ENDPOINT}?query={encoded}"
        f"&select={HUDOC_SELECT}"
        f"&sort=kpdate+Descending"
        f"&start=0&length={length}"
        f"&rankingModelId={HUDOC_RANKING_MODEL_ID}"
    )


def fetch_window(
    from_date: str,
    to_date: str,
    length: int,
    cache: dict[str, Any],
) -> list[dict[str, Any]]:
    cache_key = f"{from_date}|{to_date}|{length}"
    if cache_key in cache:
        return cache[cache_key]

    url = hudoc_date_query(from_date, to_date, length)
    last_exc: Exception | None = None
    for attempt in range(RETRY_COUNT):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": "lexgenie-negatives/1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            results = payload.get("results", [])
            cache[cache_key] = results
            return results
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(RETRY_SLEEP * (attempt + 1))
    raise RuntimeError(f"HUDOC fetch failed {from_date}→{to_date}") from last_exc


def parse_result(result: dict[str, Any]) -> dict[str, str]:
    cols = result.get("columns", result)
    appno_raw = cols.get("appno", "") or ""
    appnos = [a.strip() for a in appno_raw.split(";") if a.strip()]
    itemid = cols.get("itemid", "") or ""
    kpdate = cols.get("kpdate", "") or ""
    year = kpdate[:4] if kpdate else ""
    docname = cols.get("docname", "") or ""
    docname = docname.replace("CASE OF ", "").strip()
    return {
        "case_name": docname,
        "application_numbers": "|".join(appnos),
        "hudoc_itemid": itemid,
        "hudoc_importance_level": str(cols.get("importance", "") or ""),
        "hudoc_doctype": cols.get("doctype", "") or "",
        "hudoc_conclusion": cols.get("conclusion", "") or "",
        "convention_articles": "|".join(
            a.strip() for a in (cols.get("article", "") or "").split(";") if a.strip()
        ),
        "judgment_year": year,
        "_appnos_set": {a.lower() for a in appnos},
    }


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    transitions = load_transitions(INPUT_CSV)
    cache = load_cache()

    # De-duplicate fetch requests: same date window queried once
    unique_windows: dict[tuple[str, str], list[tuple[str, str, str]]] = defaultdict(list)
    for key in transitions:
        _, from_d, to_d = key
        unique_windows[(from_d, to_d)].append(key)

    print(f"Transitions: {len(transitions)}, unique date windows: {len(unique_windows)}")

    negative_rows: list[dict[str, str]] = []
    window_stats: list[dict[str, Any]] = []
    fresh_fetches = 0

    for (from_d, to_d), trans_keys in sorted(unique_windows.items()):
        cache_key = f"{from_d}|{to_d}|200"
        was_cached = cache_key in cache
        try:
            results = fetch_window(from_d, to_d, 200, cache)
        except Exception as exc:
            print(f"  SKIP {from_d}→{to_d}: {exc}")
            continue

        if not was_cached:
            fresh_fetches += 1
            save_cache(cache)
            time.sleep(INTER_REQUEST_SLEEP)

        parsed = [parse_result(r) for r in results]
        parsed = [p for p in parsed if p["hudoc_doctype"] in VALID_DOCTYPES]

        for trans_key in trans_keys:
            meta = transitions[trans_key]
            guide_id, from_date, to_date = trans_key
            positive_appnos = meta["positive_appnos"]

            negatives_for_trans: list[dict[str, str]] = []
            for p in parsed:
                appnos_set: set[str] = p["_appnos_set"]
                if appnos_set & positive_appnos:
                    continue
                if not p["application_numbers"]:
                    continue
                row = {
                    "guide_id": guide_id,
                    "guide_title": meta["guide_title"],
                    "from_snapshot_date": from_date,
                    "to_snapshot_date": to_date,
                    "case_key": f"apps:{p['application_numbers'].split('|')[0]}",
                    "case_name": p["case_name"],
                    "application_numbers": p["application_numbers"],
                    "hudoc_itemid": p["hudoc_itemid"],
                    "hudoc_importance_level": p["hudoc_importance_level"],
                    "hudoc_doctype": p["hudoc_doctype"],
                    "hudoc_conclusion": p["hudoc_conclusion"],
                    "convention_articles": p["convention_articles"],
                    "judgment_year": p["judgment_year"],
                    "label": "negative",
                    "negative_reason": "published_in_window_not_added_to_guide",
                }
                negatives_for_trans.append(row)
                if len(negatives_for_trans) >= MAX_NEGATIVES_PER_TRANSITION:
                    break

            negative_rows.extend(negatives_for_trans)
            window_stats.append({
                "guide_id": guide_id,
                "from_date": from_date,
                "to_date": to_date,
                "candidates_in_window": len(parsed),
                "negatives_added": len(negatives_for_trans),
                "positives_in_window": len(positive_appnos),
            })

        completed = sum(1 for s in window_stats)
        if completed % 10 == 0 or completed == len(transitions):
            print(f"  [{completed}/{len(transitions)}] negatives so far: {len(negative_rows)}")

    with OUTPUT_CSV.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(negative_rows)

    report = {
        "transitions_processed": len(transitions),
        "unique_windows_fetched": len(unique_windows),
        "fresh_fetches": fresh_fetches,
        "total_negatives": len(negative_rows),
        "negatives_per_transition_cap": MAX_NEGATIVES_PER_TRANSITION,
        "window_stats": window_stats,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "window_stats"}, indent=2))


if __name__ == "__main__":
    main()
