from __future__ import annotations

"""
Retry fetching judgment text for positives that are missing case text.

Strategy (tried in order for each case):
  1. DOCX conversion endpoint (same as original fetch — catches transient errors)
  2. HTML body endpoint (/app/conversion/docx/html/body) — works for some cases

Outputs:
  - New text files in outputs/case_texts/text/
  - outputs/case_texts/retry_report.json
"""

import csv
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

POSITIVE_CSV = Path("outputs/prototype/filtered_case_linked_rows.csv")
TEXT_DIR = Path("outputs/case_texts/text")
DOCX_DIR = Path("outputs/case_texts/docx")
REPORT_JSON = Path("outputs/case_texts/retry_report.json")

USER_AGENT = "lexgenie-research/0.1 (+contact: javokhir@stanford.edu)"
REQUEST_TIMEOUT = 30
DELAY_BETWEEN = 0.5

DOCX_URL = "https://hudoc.echr.coe.int/app/conversion/docx?library=ECHR&id={itemid}&filename={itemid}.docx"
HTML_BODY_URL = "https://hudoc.echr.coe.int/app/conversion/docx/html/body?library=ECHR&id={itemid}"

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{WORD_NS}}}p"
W_T = f"{{{WORD_NS}}}t"
W_TAB = f"{{{WORD_NS}}}tab"
W_BR = f"{{{WORD_NS}}}br"


def fetch(url: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception:
        return 0, b""


def docx_to_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        with z.open("word/document.xml") as f:
            tree = ET.parse(f)
    parts: list[str] = []
    for p in tree.iter(W_P):
        line: list[str] = []
        for el in p.iter():
            if el.tag == W_T and el.text:
                line.append(el.text)
            elif el.tag == W_TAB:
                line.append("\t")
            elif el.tag == W_BR:
                line.append("\n")
        text = "".join(line).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)


def html_to_text(html: bytes) -> str:
    text = html.decode("utf-8", errors="replace")
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


def try_fetch_case(itemid: str) -> dict[str, Any]:
    result: dict[str, Any] = {"itemid": itemid, "strategy": None, "status": None, "chars": 0}

    # Strategy 1: DOCX
    code, body = fetch(DOCX_URL.format(itemid=itemid))
    if code == 200 and body.startswith(b"PK"):
        try:
            text = docx_to_text(body)
            if len(text) > 200:
                result.update({"strategy": "docx", "status": "ok", "chars": len(text)})
                return result, text
        except Exception:
            pass
    result["status"] = f"docx_{code}"
    time.sleep(DELAY_BETWEEN)

    # Strategy 2: HTML body
    code, body = fetch(HTML_BODY_URL.format(itemid=itemid))
    if code == 200 and body:
        text = html_to_text(body)
        if len(text) > 200:
            result.update({"strategy": "html_body", "status": "ok", "chars": len(text)})
            return result, text
    result["status"] = f"html_{code}"
    time.sleep(DELAY_BETWEEN)

    return result, ""


def load_missing_itemids() -> list[tuple[str, dict[str, str]]]:
    fetched = {p.stem for p in TEXT_DIR.glob("*.txt")}
    seen: dict[str, dict[str, str]] = {}
    with POSITIVE_CSV.open() as f:
        for r in csv.DictReader(f):
            if r.get("link_status") != "linked_paragraphs":
                continue
            itemid = (r.get("hudoc_itemid") or "").strip()
            if not itemid or itemid in fetched or itemid in seen:
                continue
            seen[itemid] = {
                "case_key": r.get("case_key", ""),
                "case_name": r.get("case_name", ""),
                "judgment_year": r.get("judgment_year", ""),
                "hudoc_importance_level": r.get("hudoc_importance_level", ""),
            }
    return list(seen.items())


def main() -> None:
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    DOCX_DIR.mkdir(parents=True, exist_ok=True)

    missing = load_missing_itemids()
    print(f"Cases missing text: {len(missing)}")

    results: list[dict[str, Any]] = []
    recovered = 0

    for i, (itemid, meta) in enumerate(missing, 1):
        result, text = try_fetch_case(itemid)
        result.update(meta)
        results.append(result)

        if result["status"] == "ok":
            TEXT_DIR.joinpath(f"{itemid}.txt").write_text(text)
            recovered += 1
            print(f"  [{i}/{len(missing)}] OK via {result['strategy']:10s}  {itemid}  "
                  f"{meta['judgment_year']}  {meta['case_name'][:50]}")
        else:
            if i <= 20 or i % 20 == 0:
                print(f"  [{i}/{len(missing)}] FAIL {result['status']:12s}  {itemid}  "
                      f"{meta['judgment_year']}  {meta['case_name'][:40]}")

    ok = [r for r in results if r["status"] == "ok"]
    fail_statuses = {}
    for r in results:
        if r["status"] != "ok":
            fail_statuses[r["status"]] = fail_statuses.get(r["status"], 0) + 1

    report = {
        "attempted": len(missing),
        "recovered": recovered,
        "still_missing": len(missing) - recovered,
        "fail_status_counts": fail_statuses,
        "recovered_cases": [{"itemid": r["itemid"], "strategy": r["strategy"],
                              "chars": r["chars"], "year": r["judgment_year"]}
                             for r in ok],
        "still_missing_cases": [{"itemid": r["itemid"], "status": r["status"],
                                  "year": r["judgment_year"],
                                  "importance": r.get("hudoc_importance_level", "")}
                                 for r in results if r["status"] != "ok"],
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print(f"\nRecovered: {recovered}/{len(missing)}")
    print(f"Fail breakdown: {fail_statuses}")
    print(f"Report: {REPORT_JSON}")


if __name__ == "__main__":
    main()
