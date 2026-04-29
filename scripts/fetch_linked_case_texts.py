from __future__ import annotations

import csv
import io
import json
import random
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

INPUT_CSV = Path("outputs/case_linked_guide_diffs/case_linked_guide_diffs.csv")
OUTPUT_DIR = Path("outputs/case_texts")
DOCX_DIR = OUTPUT_DIR / "docx"
TEXT_DIR = OUTPUT_DIR / "text"
INDEX_CSV = OUTPUT_DIR / "case_texts_index.csv"
REPORT_JSON = OUTPUT_DIR / "case_texts_report.json"

DOCX_URL_TEMPLATE = (
    "https://hudoc.echr.coe.int/app/conversion/docx"
    "?library=ECHR&id={itemid}&filename={itemid}.docx"
)

USER_AGENT = "lexgenie-research/0.1 (+contact: javokhir@stanford.edu)"
REQUEST_TIMEOUT_SECONDS = 60
RETRY_COUNT = 3
RETRY_BASE_SLEEP_SECONDS = 1.5
BASE_DELAY_SECONDS = 0.4  # polite delay between fresh fetches

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W_P = f"{{{WORD_NS}}}p"
W_T = f"{{{WORD_NS}}}t"
W_TAB = f"{{{WORD_NS}}}tab"
W_BR = f"{{{WORD_NS}}}br"


def load_linked_itemid_index() -> dict[str, dict[str, str]]:
    """Return itemid -> representative case metadata."""
    index: dict[str, dict[str, str]] = {}
    with INPUT_CSV.open() as handle:
        for row in csv.DictReader(handle):
            if row.get("link_status") != "linked_paragraphs":
                continue
            itemid = row.get("hudoc_itemid", "").strip()
            if not itemid:
                continue
            if itemid in index:
                continue
            index[itemid] = {
                "hudoc_itemid": itemid,
                "case_key": row.get("case_key", ""),
                "case_name": row.get("case_name", ""),
                "application_numbers": row.get("application_numbers", ""),
                "judgment_year": row.get("judgment_year", ""),
                "hudoc_doctype": row.get("hudoc_doctype", ""),
                "hudoc_docname": row.get("hudoc_docname", ""),
            }
    return index


def fetch_docx(itemid: str) -> bytes:
    url = DOCX_URL_TEMPLATE.format(itemid=itemid)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read()
                if "wordprocessingml" not in ctype and not body.startswith(b"PK"):
                    raise RuntimeError(
                        f"Unexpected content-type {ctype!r} for {itemid}"
                    )
                return body
        except urllib.error.HTTPError as exc:
            # 404 -> case unavailable in DOCX form, don't retry
            if exc.code == 404:
                raise
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            last_exc = exc
        sleep_for = RETRY_BASE_SLEEP_SECONDS * (2 ** (attempt - 1))
        sleep_for += random.uniform(0, 0.4)
        time.sleep(sleep_for)
    assert last_exc is not None
    raise last_exc


def paragraph_text(p: ET.Element) -> str:
    parts: list[str] = []
    for el in p.iter():
        tag = el.tag
        if tag == W_T and el.text:
            parts.append(el.text)
        elif tag == W_TAB:
            parts.append("\t")
        elif tag == W_BR:
            parts.append("\n")
    return "".join(parts).strip()


def extract_text_from_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        with z.open("word/document.xml") as f:
            tree = ET.parse(f)
    paragraphs: list[str] = []
    for p in tree.iter(W_P):
        text = paragraph_text(p)
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def process_one(
    itemid: str, meta: dict[str, str]
) -> tuple[str, dict[str, Any]]:
    docx_path = DOCX_DIR / f"{itemid}.docx"
    text_path = TEXT_DIR / f"{itemid}.txt"
    cached = docx_path.exists() and text_path.exists()

    record: dict[str, Any] = {
        "hudoc_itemid": itemid,
        "case_key": meta["case_key"],
        "case_name": meta["case_name"],
        "application_numbers": meta["application_numbers"],
        "judgment_year": meta["judgment_year"],
        "hudoc_doctype": meta["hudoc_doctype"],
        "hudoc_docname": meta["hudoc_docname"],
        "docx_path": str(docx_path),
        "text_path": str(text_path),
        "docx_bytes": 0,
        "text_chars": 0,
        "text_paragraphs": 0,
        "status": "",
        "error": "",
        "cached": cached,
    }

    status = "ok"
    try:
        if docx_path.exists():
            data = docx_path.read_bytes()
        else:
            data = fetch_docx(itemid)
            docx_path.write_bytes(data)
        record["docx_bytes"] = len(data)

        if text_path.exists():
            text = text_path.read_text()
        else:
            text = extract_text_from_docx(data)
            text_path.write_text(text)
        record["text_chars"] = len(text)
        record["text_paragraphs"] = sum(1 for line in text.splitlines() if line.strip())
    except urllib.error.HTTPError as exc:
        status = f"http_{exc.code}"
        record["error"] = f"HTTPError {exc.code}"
    except Exception as exc:  # noqa: BLE001
        status = "error"
        record["error"] = f"{type(exc).__name__}: {exc}"

    record["status"] = status
    return status, record


def main() -> None:
    DOCX_DIR.mkdir(parents=True, exist_ok=True)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    index = load_linked_itemid_index()
    ordered = sorted(index.items())
    total = len(ordered)
    print(f"Unique linked itemids to process: {total}")

    records: list[dict[str, Any]] = []
    counter: Counter = Counter()
    for i, (itemid, meta) in enumerate(ordered, start=1):
        was_cached = (
            (DOCX_DIR / f"{itemid}.docx").exists()
            and (TEXT_DIR / f"{itemid}.txt").exists()
        )
        status, record = process_one(itemid, meta)
        records.append(record)
        counter[status] += 1
        if i % 50 == 0 or i == total:
            print(
                f"  [{i}/{total}] last={itemid} status={status} "
                f"ok={counter['ok']} errors={sum(c for s, c in counter.items() if s != 'ok')}"
            )
        if not was_cached and status == "ok":
            time.sleep(BASE_DELAY_SECONDS)

    if records:
        fieldnames = list(records[0].keys())
        with INDEX_CSV.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

    ok_records = [r for r in records if r["status"] == "ok"]
    text_chars = [r["text_chars"] for r in ok_records]
    report = {
        "input_unique_itemids": total,
        "status_counts": dict(counter),
        "ok_total_text_chars": sum(text_chars),
        "ok_median_text_chars": (
            sorted(text_chars)[len(text_chars) // 2] if text_chars else 0
        ),
        "ok_min_text_chars": min(text_chars) if text_chars else 0,
        "ok_max_text_chars": max(text_chars) if text_chars else 0,
        "errors": [
            {"hudoc_itemid": r["hudoc_itemid"], "status": r["status"], "error": r["error"]}
            for r in records
            if r["status"] != "ok"
        ][:50],
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "errors"}, indent=2))


if __name__ == "__main__":
    main()
