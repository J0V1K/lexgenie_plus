from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Iterable

import fitz
import streamlit as st
from huggingface_hub import hf_hub_download


DATASET_REPO_ID = os.environ.get(
    "LEXGENIE_DATASET_REPO_ID",
    "lexgenie/echr-guide-citation-diffs",
)
DATASET_REPO_TYPE = "dataset"
DIFFS_FILENAME = "diffs_grouped.json"
LIST_OF_CITED_CASES_HEADER = "List of cited cases"
APP_TITLE = "ECHR Guide Citation Diffs"


@dataclass(frozen=True)
class Mention:
    page_number: int
    paragraph: str


def get_hf_token() -> str | None:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def local_data_path(*parts: str) -> Path | None:
    candidates = [Path.cwd(), Path(__file__).parent]
    for base in candidates:
        candidate = base.joinpath(*parts)
        if candidate.exists():
            return candidate
    return None


def normalize_text(text: str) -> str:
    cleaned = (
        text.lower()
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_application_numbers(citation: str) -> list[str]:
    return re.findall(r"\b\d+/\d+\b", citation)


def extract_case_name(citation: str) -> str:
    case_name = citation.split(",", 1)[0].strip()
    return re.sub(r"\s+", " ", case_name).strip("* ").strip()


def build_search_terms(citation: str) -> list[str]:
    terms = extract_application_numbers(citation)
    case_name = extract_case_name(citation)
    if case_name:
        terms.append(case_name)
    if citation:
        terms.append(citation)
    deduped = []
    seen = set()
    for term in terms:
        normalized = normalize_text(term)
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(term)
    return deduped


def dedupe_page_numbers(page_numbers: Iterable[int]) -> list[int]:
    return sorted({page_number for page_number in page_numbers if page_number > 0})


def clamp_page(page_number: int, page_count: int) -> int:
    return max(1, min(page_number, page_count))


def highlight_paragraph(paragraph: str, terms: Iterable[str]) -> str:
    highlighted = escape(paragraph)
    for term in sorted(terms, key=len, reverse=True):
        if not term.strip():
            continue
        pattern = re.compile(re.escape(escape(term)), re.IGNORECASE)
        highlighted = pattern.sub(
            lambda match: f"<mark>{match.group(0)}</mark>",
            highlighted,
        )
    return highlighted


def diff_label(diff: dict) -> str:
    return (
        f"{diff['from_version']} -> {diff['to_version']} "
        f"(+{len(diff.get('added', []))} / -{len(diff.get('removed', []))})"
    )


def diff_token(diff: dict) -> str:
    return "|".join(
        [
            diff["guide_id"],
            diff["from_snapshot"],
            diff["to_snapshot"],
        ]
    )


def citation_token(diff: dict, change: str, citation: str) -> str:
    return "|".join([diff_token(diff), change, citation])


def snapshot_url(diff: dict, side: str) -> str:
    return (
        diff.get(f"{side}_hf_url")
        or diff.get(f"{side}_wayback_url")
        or diff.get(f"{side}_url", "")
    )


def mention_summary(mentions: list[Mention]) -> str:
    if not mentions:
        return "No paragraph mentions found in the guide body."
    pages = ", ".join(str(mention.page_number) for mention in mentions[:5])
    suffix = "" if len(mentions) <= 5 else ", ..."
    return f"{len(mentions)} body mention(s) found on page(s): {pages}{suffix}"


def initialize_viewer_state(
    selection_token: str,
    from_page_count: int,
    to_page_count: int,
    default_focus_page: int,
) -> None:
    if st.session_state.get("viewer_selection_token") == selection_token:
        return

    st.session_state["viewer_selection_token"] = selection_token
    st.session_state["from_page"] = clamp_page(default_focus_page, from_page_count)
    st.session_state["to_page"] = clamp_page(default_focus_page, to_page_count)
    st.session_state["from_mention_page"] = "None"
    st.session_state["to_mention_page"] = "None"


@st.cache_data(show_spinner=False)
def load_diffs() -> list[dict]:
    local_path = local_data_path(DIFFS_FILENAME)
    if local_path is not None:
        return json.loads(local_path.read_text())

    path = hf_hub_download(
        repo_id=DATASET_REPO_ID,
        repo_type=DATASET_REPO_TYPE,
        filename=DIFFS_FILENAME,
        token=get_hf_token(),
    )
    return json.loads(Path(path).read_text())


@st.cache_data(show_spinner=False)
def download_pdf(guide_id: str, snapshot: str) -> bytes:
    local_path = local_data_path("pdfs", guide_id, snapshot)
    if local_path is not None:
        return local_path.read_bytes()

    path = hf_hub_download(
        repo_id=DATASET_REPO_ID,
        repo_type=DATASET_REPO_TYPE,
        filename=f"pdfs/{guide_id}/{snapshot}",
        token=get_hf_token(),
    )
    return Path(path).read_bytes()


@st.cache_data(show_spinner=False)
def extract_mentions(pdf_bytes: bytes, citation: str) -> list[Mention]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    search_terms = [normalize_text(term) for term in build_search_terms(citation)]
    mentions: list[Mention] = []
    list_page_index = None

    for page_index in range(document.page_count):
        page = document.load_page(page_index)
        page_text = page.get_text("text")
        if LIST_OF_CITED_CASES_HEADER in page_text:
            list_page_index = page_index

    search_page_count = list_page_index if list_page_index is not None else document.page_count

    for page_index in range(search_page_count):
        page = document.load_page(page_index)
        blocks = page.get_text("blocks")
        for block in blocks:
            text = block[4].strip()
            if not text:
                continue
            for paragraph in re.split(r"\n\s*\n", text):
                cleaned = re.sub(r"\s+", " ", paragraph).strip()
                if len(cleaned) < 40:
                    continue
                normalized = normalize_text(cleaned)
                if any(term in normalized for term in search_terms):
                    mentions.append(Mention(page_number=page_index + 1, paragraph=cleaned))

    return mentions


@st.cache_data(show_spinner=False)
def get_pdf_page_count(pdf_bytes: bytes) -> int:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    return document.page_count


@st.cache_data(show_spinner=False)
def get_page_highlight_rects(
    pdf_bytes: bytes,
    page_number: int,
    citation: str,
) -> list[tuple[float, float, float, float]]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = document.load_page(max(page_number - 1, 0))
    rects: list[tuple[float, float, float, float]] = []
    seen = set()

    for term in build_search_terms(citation):
        if len(term.strip()) < 4:
            continue
        for rect in page.search_for(term):
            rounded = tuple(round(value, 2) for value in (rect.x0, rect.y0, rect.x1, rect.y1))
            if rounded in seen:
                continue
            seen.add(rounded)
            rects.append(rounded)

    return rects


@st.cache_data(show_spinner=False)
def render_pdf_page_image(
    pdf_bytes: bytes,
    page_number: int,
    citation: str,
    zoom: float = 1.6,
) -> tuple[bytes, int]:
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = document.load_page(max(page_number - 1, 0))
    highlight_rects = get_page_highlight_rects(pdf_bytes, page_number, citation)
    for x0, y0, x1, y1 in highlight_rects:
        page.draw_rect(
            fitz.Rect(x0, y0, x1, y1),
            color=(0.84, 0.18, 0.12),
            fill=(1.0, 0.93, 0.45),
            fill_opacity=0.45,
            width=1.5,
        )
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    return pixmap.tobytes("png"), len(highlight_rects)


def render_pdf_page(
    pdf_bytes: bytes,
    page_number: int,
    snapshot_name: str,
    citation: str,
    key_prefix: str,
) -> None:
    image_bytes, highlight_count = render_pdf_page_image(pdf_bytes, page_number, citation)
    st.image(image_bytes, use_container_width=True)
    st.caption(f"Highlighted matches on this page: {highlight_count}")
    st.download_button(
        "Download PDF",
        data=pdf_bytes,
        file_name=snapshot_name,
        mime="application/pdf",
        key=f"{key_prefix}_download",
    )


def render_matching_paragraphs(mentions: list[Mention], current_page: int, citation: str, empty_text: str) -> None:
    focus_mentions = [mention for mention in mentions if mention.page_number == current_page]
    if not focus_mentions:
        st.info(empty_text)
        return

    st.markdown("**Matching paragraphs**")
    for mention in focus_mentions:
        st.markdown(
            f"<div style='padding:0.5rem 0.75rem;border:1px solid #e5e7eb;border-radius:8px;margin-bottom:0.75rem;'>"
            f"<div style='font-size:0.85rem;color:#666;margin-bottom:0.5rem;'>Page {mention.page_number}</div>"
            f"{highlight_paragraph(mention.paragraph, build_search_terms(citation))}"
            f"</div>",
            unsafe_allow_html=True,
        )


def render_viewer(diff: dict, change: str, citation: str, selection_token: str) -> None:
    try:
        with st.spinner("Loading PDFs and extracting mentions..."):
            from_pdf = download_pdf(diff["guide_id"], diff["from_snapshot"])
            to_pdf = download_pdf(diff["guide_id"], diff["to_snapshot"])
            from_mentions = extract_mentions(from_pdf, citation)
            to_mentions = extract_mentions(to_pdf, citation)
            from_page_count = get_pdf_page_count(from_pdf)
            to_page_count = get_pdf_page_count(to_pdf)
    except Exception as exc:
        st.error(
            "Could not load the PDF snapshots for this citation. "
            "If this is running on a Hugging Face Space, make sure the runtime has "
            "`HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` with access to the private dataset."
        )
        st.caption(str(exc))
        return

    if change == "Added":
        anchor_mentions = to_mentions or from_mentions
    else:
        anchor_mentions = from_mentions or to_mentions

    default_focus_page = anchor_mentions[0].page_number if anchor_mentions else 1
    initialize_viewer_state(
        selection_token=selection_token,
        from_page_count=from_page_count,
        to_page_count=to_page_count,
        default_focus_page=default_focus_page,
    )

    st.markdown("---")
    st.markdown(f"**PDF viewer for {change.lower()} citation**")
    st.code(citation, language=None)

    summary_col1, summary_col2 = st.columns(2)
    with summary_col1:
        st.caption(mention_summary(from_mentions))
    with summary_col2:
        st.caption(mention_summary(to_mentions))

    from_mention_pages = dedupe_page_numbers(mention.page_number for mention in from_mentions)
    to_mention_pages = dedupe_page_numbers(mention.page_number for mention in to_mentions)

    jump_col1, jump_col2 = st.columns(2)
    with jump_col1:
        if from_mention_pages:
            selected_from_mention_page = st.selectbox(
                "Jump to from mention page",
                ["None"] + [str(page) for page in from_mention_pages],
                key="from_mention_page",
            )
            if selected_from_mention_page != "None":
                st.session_state["from_page"] = int(selected_from_mention_page)

    with jump_col2:
        if to_mention_pages:
            selected_to_mention_page = st.selectbox(
                "Jump to to mention page",
                ["None"] + [str(page) for page in to_mention_pages],
                key="to_mention_page",
            )
            if selected_to_mention_page != "None":
                st.session_state["to_page"] = int(selected_to_mention_page)

    viewer_col1, viewer_col2 = st.columns(2)

    with viewer_col1:
        st.markdown("**From PDF**")
        st.number_input(
            f"From page (1-{from_page_count})",
            min_value=1,
            max_value=from_page_count,
            step=1,
            key="from_page",
        )
        from_page = int(st.session_state["from_page"])
        render_pdf_page(
            from_pdf,
            from_page,
            diff["from_snapshot"],
            citation,
            "from_pdf",
        )
        render_matching_paragraphs(
            mentions=from_mentions,
            current_page=from_page,
            citation=citation,
            empty_text="No body-paragraph mention found on this page in the earlier guide.",
        )

    with viewer_col2:
        st.markdown("**To PDF**")
        st.number_input(
            f"To page (1-{to_page_count})",
            min_value=1,
            max_value=to_page_count,
            step=1,
            key="to_page",
        )
        to_page = int(st.session_state["to_page"])
        render_pdf_page(
            to_pdf,
            to_page,
            diff["to_snapshot"],
            citation,
            "to_pdf",
        )
        render_matching_paragraphs(
            mentions=to_mentions,
            current_page=to_page,
            citation=citation,
            empty_text="No body-paragraph mention found on this page in the later guide.",
        )


def activate_viewer(diff: dict, change: str, citation: str) -> None:
    st.session_state["active_diff_token"] = diff_token(diff)
    st.session_state["active_citation_token"] = citation_token(diff, change, citation)


def render_citation_list(diff: dict, change: str, citations: list[str]) -> None:
    header = "✅ Added" if change == "Added" else "❌ Removed"
    st.markdown(f"**{header} ({len(citations)})**")
    for index, citation in enumerate(citations):
        token = citation_token(diff, change, citation)
        is_active = st.session_state.get("active_citation_token") == token
        text_col, action_col = st.columns([7, 1])
        with text_col:
            st.markdown(f"- {citation}")
        with action_col:
            if st.button(
                "Viewing" if is_active else "View PDF",
                key=f"{token}_{index}",
                disabled=is_active,
                use_container_width=True,
            ):
                activate_viewer(diff, change, citation)
                st.rerun()


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(
    "Shows what cases were added or removed from the List of cited cases section of ECHR "
    "legal guides between consecutive versions, with an optional before/after PDF viewer."
)

diffs = load_diffs()

guide_options = {}
for diff in diffs:
    guide_options[diff["guide_title"]] = diff["guide_id"]

selected_title = st.sidebar.selectbox("Select guide", sorted(guide_options.keys()))
selected_id = guide_options[selected_title]
guide_diffs = [diff for diff in diffs if diff["guide_id"] == selected_id]

st.sidebar.markdown("---")
st.sidebar.markdown(f"**{len(guide_diffs)}** diff(s) for this guide")

seen_snapshots = {}
for diff in guide_diffs:
    for side in ("from", "to"):
        snapshot = diff[f"{side}_snapshot"]
        if snapshot not in seen_snapshots:
            seen_snapshots[snapshot] = snapshot_url(diff, side)

with st.sidebar.expander(f"All snapshots ({len(seen_snapshots)})", expanded=False):
    for filename, url in sorted(seen_snapshots.items()):
        if url:
            st.markdown(f"[{filename} ↗]({url})")
        else:
            st.markdown(f"- {filename}")

st.subheader(selected_title)

for diff in guide_diffs:
    added = diff.get("added", [])
    removed = diff.get("removed", [])
    current_diff_token = diff_token(diff)
    is_active_diff = st.session_state.get("active_diff_token") == current_diff_token
    active_citation_token = st.session_state.get("active_citation_token")
    label = f"{diff['from_version']} → {diff['to_version']}  (+{len(added)} / -{len(removed)})"

    with st.expander(label, expanded=is_active_diff):
        col1, col2 = st.columns(2)

        with col1:
            from_url = snapshot_url(diff, "from")
            if from_url:
                st.markdown(f"[{diff['from_snapshot']} ↗]({from_url})")
            else:
                st.code(diff["from_snapshot"], language=None)

        with col2:
            to_url = snapshot_url(diff, "to")
            if to_url:
                st.markdown(f"[{diff['to_snapshot']} ↗]({to_url})")
            else:
                st.code(diff["to_snapshot"], language=None)

        st.markdown("---")

        if added:
            render_citation_list(diff, "Added", added)

        if removed:
            render_citation_list(diff, "Removed", removed)

        if not added and not removed:
            st.info("This diff has no added or removed citations.")

        active_change = None
        active_citation = None
        for change_name, citations in (("Added", added), ("Removed", removed)):
            for citation in citations:
                token = citation_token(diff, change_name, citation)
                if token == active_citation_token:
                    active_change = change_name
                    active_citation = citation
                    break
            if active_citation is not None:
                break

        if is_active_diff and active_citation is not None and active_change is not None:
            render_viewer(
                diff=diff,
                change=active_change,
                citation=active_citation,
                selection_token=active_citation_token,
            )
