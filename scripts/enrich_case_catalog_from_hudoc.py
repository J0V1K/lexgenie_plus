from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from rebuild_citation_diffs_clean import normalize_case_name, normalize_display_text


INPUT_CSV = Path("outputs/case_catalog/audit/cases_catalog_raw.csv")
OUTPUT_DIR = Path("outputs/case_catalog")
AUDIT_DIR = OUTPUT_DIR / "audit"
OUTPUT_CSV = OUTPUT_DIR / "cases_catalog.csv"
OUTPUT_JSON = OUTPUT_DIR / "cases_catalog.json"
UNMATCHED_CSV = AUDIT_DIR / "cases_catalog_hudoc_unmatched.csv"
REPORT_JSON = AUDIT_DIR / "cases_catalog_hudoc_report.json"
CACHE_JSON = AUDIT_DIR / "hudoc_query_cache.json"
HUDOC_ENRICHED_FIELDS = [
    "hudoc_itemid",
    "hudoc_importance_level",
    "convention_articles",
    "respondent_states",
    "hudoc_match_status",
    "hudoc_match_method",
    "hudoc_query_value",
    "hudoc_query_result_count",
    "hudoc_docname",
    "hudoc_appno",
    "hudoc_doctype",
    "hudoc_languageisocode",
    "hudoc_kpdate",
    "hudoc_kpdateastext",
    "hudoc_originatingbody",
    "hudoc_ecli",
    "hudoc_conclusion",
]

HUDOC_ENDPOINT = "https://hudoc.echr.coe.int/app/query/results"
HUDOC_SELECT = ",".join(
    [
        "itemid",
        "appno",
        "docname",
        "doctype",
        "kpdate",
        "importance",
        "article",
        "respondent",
        "originatingbody",
        "kpdateastext",
        "ecli",
        "conclusion",
        "languageisocode",
    ]
)
HUDOC_RANKING_MODEL_ID = "11111111-0000-0000-0000-000000000000"

APP_BATCH_SIZE = 25
NO_APP_QUERY_LENGTH = 50
APP_QUERY_LENGTH = 250
REQUEST_TIMEOUT_SECONDS = 30
RETRY_COUNT = 3
RETRY_SLEEP_SECONDS = 1.0

CASE_PREFIX_RE = re.compile(r"^(?:CASE OF|AFFAIRE)\s+", re.IGNORECASE)
TRANSLATION_SUFFIX_RE = re.compile(r"\s+-\s+\[.*$")
BRACKET_TAG_RE = re.compile(r"\[(?:GC|Grand Chamber|Committee|Comm(?:ittee)?|Plenary)\]")
DECISION_TAG_RE = re.compile(r"\((?:dec\.?|decision)\)", re.IGNORECASE)
NO_VARIANT_RE = re.compile(r"\((?:n|no\.?|number)\s*([0-9]+)\)", re.IGNORECASE)
QUOTED_ALIAS_PAREN_RE = re.compile(r'\(\s*["“][^)]*["”]\s*\)')
TRAILING_PROCEDURAL_RE = re.compile(
    r",?\s*(?:commission decision|commission report|judgment of|decision of)\s*$",
    re.IGNORECASE,
)
THE_AFTER_V_RE = re.compile(r"\bv\.\s+the\s+", re.IGNORECASE)
REQUEST_NO_RE = re.compile(r"\bP16-\d{4}-\d{3}\b", re.IGNORECASE)
LEGACY_CHAIN_RE = re.compile(r"\b\d+/\d+/\d+/\d+\b")
TRAILING_SLASH_ID_RE = re.compile(r"\b\d+/\b")
WHITESPACE_RE = re.compile(r"\s+")
UPPER_A_V_UPPER_RE = re.compile(r"\b([A-Z])\s+v\.\s+([A-Z])\b")
MERGED_MULTI_CASE_RE = re.compile(r"\b\d+/\d+\b.+\b\d+/\d+\b.+\b[A-ZÀ-ÖØ-Ý][^,]*\bv\.\b", re.UNICODE)


def load_rows() -> list[dict[str, str]]:
    with INPUT_CSV.open() as handle:
        return list(csv.DictReader(handle))


def load_existing_enriched_rows() -> dict[str, dict[str, str]]:
    if not OUTPUT_CSV.exists():
        return {}
    with OUTPUT_CSV.open() as handle:
        rows = list(csv.DictReader(handle))
    return {
        row["case_key"]: row
        for row in rows
        if row.get("hudoc_match_status") == "matched"
        and row.get("hudoc_match_method") != "single_application_unique_cluster"
        and row.get("hudoc_match_method") != "request_identifier_exact_name_and_year"
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_pipe_list(value: str) -> list[str]:
    return [part for part in value.split("|") if part]


def serialize_pipe_list(values: list[str]) -> str:
    return "|".join(values)


def normalize_case_name_for_match(text: str) -> str:
    text = normalize_display_text(text)
    text = QUOTED_ALIAS_PAREN_RE.sub("", text)
    text = BRACKET_TAG_RE.sub("", text)
    text = DECISION_TAG_RE.sub("", text)
    text = NO_VARIANT_RE.sub(lambda m: f"(no. {m.group(1)})", text)
    text = TRAILING_PROCEDURAL_RE.sub("", text)
    text = text.replace(" c. ", " v. ")
    text = THE_AFTER_V_RE.sub("v. ", text)
    text = WHITESPACE_RE.sub(" ", text).strip(" ,")
    return normalize_case_name(text)


def canonicalize_local_case_name(case_name: str) -> str:
    return normalize_case_name_for_match(case_name)


def canonicalize_hudoc_docname(docname: str) -> str:
    text = normalize_display_text(docname)
    text = TRANSLATION_SUFFIX_RE.sub("", text)
    text = CASE_PREFIX_RE.sub("", text)
    return normalize_case_name_for_match(text)


def build_search_case_name(case_name: str) -> str:
    text = normalize_display_text(case_name)
    text = QUOTED_ALIAS_PAREN_RE.sub("", text)
    text = BRACKET_TAG_RE.sub("", text)
    text = DECISION_TAG_RE.sub("", text)
    text = TRAILING_PROCEDURAL_RE.sub("", text)
    return WHITESPACE_RE.sub(" ", text).strip(" ,")


def extract_request_numbers(text: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for match in REQUEST_NO_RE.findall(text):
        normalized = match.upper()
        if normalized in seen:
            continue
        seen.add(normalized)
        values.append(normalized)
    return values


def row_has_request_identifier(row: dict[str, str]) -> bool:
    return bool(extract_request_numbers(row.get("citation_example", "")))


def dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        itemid = result.get("columns", {}).get("itemid", "") or json.dumps(result, sort_keys=True)
        if itemid in seen:
            continue
        seen.add(itemid)
        deduped.append(result)
    return deduped


def hudoc_query_url(query: str, *, length: int) -> str:
    encoded_query = quote(query, safe="():/\" ")
    encoded_query = encoded_query.replace(" ", "%20")
    return (
        f"{HUDOC_ENDPOINT}?query={encoded_query}"
        f"&select={HUDOC_SELECT}"
        "&sort="
        "&start=0"
        f"&length={length}"
        f"&rankingModelId={HUDOC_RANKING_MODEL_ID}"
    )


def load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_JSON.exists():
        return {}
    return json.loads(CACHE_JSON.read_text())


def save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_JSON.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def fetch_hudoc(query: str, *, length: int, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cache_key = f"{query}||{length}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = hudoc_query_url(query, length=length)
    last_error: Exception | None = None
    for attempt in range(RETRY_COUNT):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "lexgenie-hudoc-enrichment/1.0",
                    "Accept": "application/json",
                },
            )
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            cache[cache_key] = payload
            return payload
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(RETRY_SLEEP_SECONDS * (attempt + 1))

    raise RuntimeError(f"HUDOC request failed for query={query!r}") from last_error


def split_semicolon_field(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(";") if part.strip()]


def row_is_obviously_merged(row: dict[str, str]) -> bool:
    citation = normalize_display_text(row.get("citation_example", ""))
    return bool(UPPER_A_V_UPPER_RE.search(citation) or MERGED_MULTI_CASE_RE.search(citation))


def official_doctype_priority(doctype: str, language: str) -> tuple[int, int, str]:
    if len(doctype) == 5 and doctype.startswith("HE") and language == "ENG":
        return (0, 0, doctype)
    if len(doctype) == 5 and doctype.startswith("HF") and language == "FRE":
        return (1, 0, doctype)
    if len(doctype) == 5 and doctype.startswith("H"):
        return (2, 0, doctype)
    if doctype == "ADVPRO16OPENG":
        return (3, 0, doctype)
    if doctype == "CLIN" and language == "ENG":
        return (4, 0, doctype)
    if doctype == "CLINF" and language == "FRE":
        return (5, 0, doctype)
    if doctype.startswith("ADVPRO16OP"):
        return (6, 0, doctype)
    return (9, 0, doctype)


def result_cluster_key(result: dict[str, Any]) -> tuple[str, str, str]:
    columns = result["columns"]
    appno = columns.get("appno", "") or ""
    kpdate = columns.get("kpdate", "") or ""
    ecli = columns.get("ecli", "") or ""
    docname = canonicalize_hudoc_docname(columns.get("docname", "") or "")
    stable_doc = ecli or docname
    return (appno, kpdate[:10], stable_doc)


def row_year_matches(row: dict[str, str], result: dict[str, Any]) -> bool:
    row_year = row.get("judgment_year", "")
    if not row_year:
        return True
    kpdate = result["columns"].get("kpdate", "") or ""
    return kpdate.startswith(f"{row_year}-")


def result_name_matches_row(row: dict[str, str], result: dict[str, Any]) -> bool:
    row_name = canonicalize_local_case_name(row["case_name"])
    hudoc_name = canonicalize_hudoc_docname(result["columns"].get("docname", "") or "")
    return bool(row_name) and row_name == hudoc_name


def row_has_malformed_legacy_identifier(row: dict[str, str]) -> bool:
    citation = row.get("citation_example", "")
    return bool(LEGACY_CHAIN_RE.search(citation) or TRAILING_SLASH_ID_RE.search(citation))


def choose_from_candidates_for_app_row(
    row: dict[str, str],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    if not candidates:
        return None, "no_candidate_after_year_filter"

    official = [
        result
        for result in candidates
        if official_doctype_priority(
            result["columns"].get("doctype", "") or "",
            result["columns"].get("languageisocode", "") or "",
        )[0]
        < 5
    ]
    if not official:
        return None, "no_official_case_document"

    row_apps = set(parse_pipe_list(row["application_numbers"]))
    merged_row = row_is_obviously_merged(row)
    clusters: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in official:
        clusters[result_cluster_key(result)].append(result)

    exact_app_clusters = [
        cluster_results
        for cluster_results in clusters.values()
        if set(split_semicolon_field(cluster_results[0]["columns"].get("appno", ""))) == row_apps
    ]
    if exact_app_clusters:
        selected_cluster = sorted(
            exact_app_clusters,
            key=lambda group: min(
                official_doctype_priority(
                    item["columns"].get("doctype", "") or "",
                    item["columns"].get("languageisocode", "") or "",
                )
                for item in group
            ),
        )[0]
        selected = min(
            selected_cluster,
            key=lambda item: official_doctype_priority(
                item["columns"].get("doctype", "") or "",
                item["columns"].get("languageisocode", "") or "",
            ),
        )
        return selected, "exact_application_set"

    subset_name_clusters = [
        cluster_results
        for cluster_results in clusters.values()
        if row_apps
        and row_apps.issubset(set(split_semicolon_field(cluster_results[0]["columns"].get("appno", ""))))
        and result_name_matches_row(row, cluster_results[0])
    ]
    if len(subset_name_clusters) == 1 and not merged_row:
        selected_cluster = subset_name_clusters[0]
        selected = min(
            selected_cluster,
            key=lambda item: official_doctype_priority(
                item["columns"].get("doctype", "") or "",
                item["columns"].get("languageisocode", "") or "",
            ),
        )
        return selected, "application_subset_name_year"

    return None, "ambiguous_application_match"


def choose_from_candidates_for_name_row(
    row: dict[str, str],
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    if not candidates:
        return None, "no_candidate_after_year_filter"

    matching = [result for result in candidates if result_name_matches_row(row, result)]
    if not matching:
        return None, "no_exact_name_match"

    official = [
        result
        for result in matching
        if official_doctype_priority(
            result["columns"].get("doctype", "") or "",
            result["columns"].get("languageisocode", "") or "",
        )[0]
        < 5
    ]
    pool = official or matching
    selected = min(
        pool,
        key=lambda item: official_doctype_priority(
            item["columns"].get("doctype", "") or "",
            item["columns"].get("languageisocode", "") or "",
        ),
    )
    return selected, "exact_name_and_year"


def is_official_case_document(result: dict[str, Any]) -> bool:
    columns = result["columns"]
    return official_doctype_priority(
        columns.get("doctype", "") or "",
        columns.get("languageisocode", "") or "",
    )[0] < 3


def promote_name_match_to_official(
    row: dict[str, str],
    selected: dict[str, Any] | None,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if selected is None or is_official_case_document(selected):
        return selected

    selected_appnos = split_semicolon_field(selected["columns"].get("appno"))
    if not selected_appnos:
        return selected

    payload = fetch_hudoc(build_app_query(selected_appnos), length=APP_QUERY_LENGTH, cache=cache)
    results = payload.get("results", [])
    year_filtered = [result for result in results if row_year_matches(row, result)]
    exact_name_matches = [result for result in year_filtered if result_name_matches_row(row, result)]
    official_name_matches = [result for result in exact_name_matches if is_official_case_document(result)]
    if official_name_matches:
        return min(
            official_name_matches,
            key=lambda item: official_doctype_priority(
                item["columns"].get("doctype", "") or "",
                item["columns"].get("languageisocode", "") or "",
            ),
        )

    exact_app_set_official = [
        result
        for result in year_filtered
        if is_official_case_document(result)
        and set(split_semicolon_field(result["columns"].get("appno"))) == set(selected_appnos)
    ]
    if exact_app_set_official:
        return min(
            exact_app_set_official,
            key=lambda item: official_doctype_priority(
                item["columns"].get("doctype", "") or "",
                item["columns"].get("languageisocode", "") or "",
            ),
        )

    return selected


def enrich_row(
    row: dict[str, str],
    selected: dict[str, Any] | None,
    *,
    match_status: str,
    match_method: str,
    query_value: str,
    query_result_count: int,
) -> dict[str, Any]:
    enriched = dict(row)
    enriched["hudoc_match_status"] = match_status
    enriched["hudoc_match_method"] = match_method
    enriched["hudoc_query_value"] = query_value
    enriched["hudoc_query_result_count"] = query_result_count
    enriched["hudoc_docname"] = ""
    enriched["hudoc_appno"] = ""
    enriched["hudoc_doctype"] = ""
    enriched["hudoc_languageisocode"] = ""
    enriched["hudoc_kpdate"] = ""
    enriched["hudoc_kpdateastext"] = ""
    enriched["hudoc_originatingbody"] = ""
    enriched["hudoc_ecli"] = ""
    enriched["hudoc_conclusion"] = ""

    if selected is None:
        return enriched

    columns = selected["columns"]
    articles = split_semicolon_field(columns.get("article"))
    respondents = split_semicolon_field(columns.get("respondent"))

    enriched["hudoc_itemid"] = columns.get("itemid", "") or ""
    enriched["hudoc_importance_level"] = columns.get("importance", "") or ""
    enriched["convention_articles"] = serialize_pipe_list(articles)
    enriched["respondent_states"] = serialize_pipe_list(respondents)
    enriched["hudoc_docname"] = columns.get("docname", "") or ""
    enriched["hudoc_appno"] = columns.get("appno", "") or ""
    enriched["hudoc_doctype"] = columns.get("doctype", "") or ""
    enriched["hudoc_languageisocode"] = columns.get("languageisocode", "") or ""
    enriched["hudoc_kpdate"] = columns.get("kpdate", "") or ""
    enriched["hudoc_kpdateastext"] = columns.get("kpdateastext", "") or ""
    enriched["hudoc_originatingbody"] = columns.get("originatingbody", "") or ""
    enriched["hudoc_ecli"] = columns.get("ecli", "") or ""
    enriched["hudoc_conclusion"] = columns.get("conclusion", "") or ""
    return enriched


def reuse_existing_enrichment(
    row: dict[str, str],
    existing: dict[str, str],
) -> dict[str, str]:
    enriched = dict(row)
    for field in HUDOC_ENRICHED_FIELDS:
        enriched[field] = existing.get(field, "")
    return enriched


def build_app_query(app_numbers: list[str]) -> str:
    clauses = [f'appno:"{app_no}"' for app_no in app_numbers]
    return "(" + " OR ".join(clauses) + ")"


def build_name_query(case_name: str) -> str:
    return f'(docname:"{case_name}")'


def build_text_query(case_name: str) -> str:
    return f'("{case_name}")'


def build_name_query_variants(row: dict[str, str]) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()

    for request_no in extract_request_numbers(row.get("citation_example", "")):
        query = build_app_query([request_no])
        if query not in seen:
            seen.add(query)
            queries.append((query, f"request:{request_no}"))

    raw_name = normalize_display_text(row["case_name"]).strip(" ,")
    search_name = build_search_case_name(row["case_name"])
    for query, label in [
        (build_name_query(raw_name), f"docname_raw:{raw_name}"),
        (build_name_query(search_name), f"docname_clean:{search_name}"),
        (build_text_query(search_name), f"text_clean:{search_name}"),
    ]:
        if search_name and query not in seen:
            seen.add(query)
            queries.append((query, label))

    return queries


def fetch_name_query_results(
    row: dict[str, str],
    cache: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], str]:
    aggregated_results: list[dict[str, Any]] = []
    query_labels: list[str] = []
    for query, label in build_name_query_variants(row):
        payload = fetch_hudoc(query, length=NO_APP_QUERY_LENGTH, cache=cache)
        query_labels.append(label)
        aggregated_results.extend(payload.get("results", []))

    return dedupe_results(aggregated_results), " || ".join(query_labels)


def main() -> None:
    rows = load_rows()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    cache = load_cache()
    existing_enriched_rows = load_existing_enriched_rows()

    reused_rows: list[dict[str, Any]] = []
    rows_to_query: list[dict[str, str]] = []
    for row in rows:
        existing = existing_enriched_rows.get(row["case_key"])
        if existing is None:
            rows_to_query.append(row)
            continue
        reused_rows.append(reuse_existing_enrichment(row, existing))

    rows_with_app = [row for row in rows_to_query if row["primary_application_number"]]
    rows_without_app = [row for row in rows_to_query if not row["primary_application_number"]]

    app_lookup: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for start in range(0, len(rows_with_app), APP_BATCH_SIZE):
        batch_rows = rows_with_app[start : start + APP_BATCH_SIZE]
        batch_app_numbers = sorted(
            {
                app_no
                for row in batch_rows
                for app_no in parse_pipe_list(row["application_numbers"])
                if app_no
            }
        )
        if not batch_app_numbers:
            continue
        query = build_app_query(batch_app_numbers)
        payload = fetch_hudoc(query, length=APP_QUERY_LENGTH, cache=cache)
        for result in payload.get("results", []):
            for app_no in split_semicolon_field(result.get("columns", {}).get("appno")):
                app_lookup[app_no].append(result)

    enriched_rows: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []
    report = {
        "input_rows": len(rows),
        "reused_matched_rows": len(reused_rows),
        "queried_rows": len(rows_to_query),
        "rows_with_application_number": len(rows_with_app),
        "rows_without_application_number": len(rows_without_app),
        "matched_rows": 0,
        "unmatched_rows": 0,
        "match_method_counts": {},
        "unmatched_reason_counts": {},
        "doctype_counts": {},
        "importance_counts": {},
    }

    match_method_counter: Counter[str] = Counter()
    unmatched_reason_counter: Counter[str] = Counter()
    doctype_counter: Counter[str] = Counter()
    importance_counter: Counter[str] = Counter()

    for row in reused_rows:
        match_method_counter[row["hudoc_match_method"]] += 1
        doctype_counter[row["hudoc_doctype"]] += 1
        importance_counter[row["hudoc_importance_level"]] += 1
        enriched_rows.append(row)

    for row in rows_with_app:
        row_app_numbers = parse_pipe_list(row["application_numbers"])
        candidate_results = []
        seen_ids: set[str] = set()
        for app_no in row_app_numbers:
            for result in app_lookup.get(app_no, []):
                itemid = result.get("columns", {}).get("itemid", "") or json.dumps(result, sort_keys=True)
                if itemid in seen_ids:
                    continue
                seen_ids.add(itemid)
                candidate_results.append(result)

        query_value = serialize_pipe_list(row_app_numbers)
        year_filtered = [result for result in candidate_results if row_year_matches(row, result)]
        selected, match_method = choose_from_candidates_for_app_row(row, year_filtered)

        if selected is None:
            name_results, name_query_value = fetch_name_query_results(row, cache)
            for result in name_results:
                itemid = result.get("columns", {}).get("itemid", "") or json.dumps(result, sort_keys=True)
                if itemid in seen_ids:
                    continue
                seen_ids.add(itemid)
                candidate_results.append(result)
            year_filtered = [result for result in candidate_results if row_year_matches(row, result)]
            selected, match_method = choose_from_candidates_for_app_row(row, year_filtered)
            query_value = f"{query_value} || {name_query_value}"

            if selected is None and (
                row_has_malformed_legacy_identifier(row) or row_has_request_identifier(row)
            ):
                selected, name_method = choose_from_candidates_for_name_row(row, year_filtered)
                selected = promote_name_match_to_official(row, selected, cache)
                if selected is not None:
                    prefix = (
                        "request_identifier"
                        if row_has_request_identifier(row)
                        else "legacy_identifier"
                    )
                    match_method = f"{prefix}_{name_method}"

        match_status = "matched" if selected is not None else "unmatched"

        enriched = enrich_row(
            row,
            selected,
            match_status=match_status,
            match_method=match_method,
            query_value=query_value,
            query_result_count=len(candidate_results),
        )
        enriched_rows.append(enriched)

        if selected is not None:
            match_method_counter[match_method] += 1
            doctype_counter[enriched["hudoc_doctype"]] += 1
            importance_counter[enriched["hudoc_importance_level"]] += 1
        else:
            unmatched_reason_counter[match_method] += 1
            unmatched_rows.append(enriched)

    for row in rows_without_app:
        results, query_value = fetch_name_query_results(row, cache)
        year_filtered = [result for result in results if row_year_matches(row, result)]
        selected, match_method = choose_from_candidates_for_name_row(row, year_filtered)
        selected = promote_name_match_to_official(row, selected, cache)
        match_status = "matched" if selected is not None else "unmatched"

        enriched = enrich_row(
            row,
            selected,
            match_status=match_status,
            match_method=match_method,
            query_value=query_value,
            query_result_count=len(results),
        )
        enriched_rows.append(enriched)

        if selected is not None:
            match_method_counter[match_method] += 1
            doctype_counter[enriched["hudoc_doctype"]] += 1
            importance_counter[enriched["hudoc_importance_level"]] += 1
        else:
            unmatched_reason_counter[match_method] += 1
            unmatched_rows.append(enriched)

    enriched_rows.sort(key=lambda row: (canonicalize_local_case_name(row["case_name"]), row["case_key"]))
    unmatched_rows.sort(key=lambda row: (row["hudoc_match_method"], canonicalize_local_case_name(row["case_name"])))

    write_csv(OUTPUT_CSV, enriched_rows)
    OUTPUT_JSON.write_text(json.dumps(enriched_rows, indent=2, ensure_ascii=False))
    write_csv(UNMATCHED_CSV, unmatched_rows)
    save_cache(cache)

    report["matched_rows"] = len(enriched_rows) - len(unmatched_rows)
    report["unmatched_rows"] = len(unmatched_rows)
    report["match_method_counts"] = dict(match_method_counter.most_common())
    report["unmatched_reason_counts"] = dict(unmatched_reason_counter.most_common())
    report["doctype_counts"] = dict(doctype_counter.most_common())
    report["importance_counts"] = dict(importance_counter.most_common())
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"Wrote HUDOC-enriched catalog to {OUTPUT_CSV}")
    print(f"Wrote HUDOC-enriched JSON to {OUTPUT_JSON}")
    print(f"Wrote unmatched case rows to {UNMATCHED_CSV}")
    print(f"Wrote enrichment report to {REPORT_JSON}")
    print(f"Matched {report['matched_rows']} / {report['input_rows']} rows")


if __name__ == "__main__":
    main()
