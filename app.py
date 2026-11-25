import streamlit as st

from wikipedia_extractor_core import (
    ArticleFetchError, ArticleParseError, WikipediaScraper, format_txt, sanitize_filename,
)

st.set_page_config(page_title="Wikipedia Extractor", layout="wide")
state = st.session_state
for key, default in dict(article=None, error=None, preview=None, downloads=[], filename=None).items():
    state.setdefault(key, default)


def clear_preview():
    state.preview = None
    state.filename = None


def record_download(filename):
    if filename not in state.downloads:
        state.downloads.append(filename)
    state.filename = None


with st.sidebar:
    st.header("Wikipedia Extractor")
    if st.button("Start Over"):
        state.clear()
        st.rerun()

st.title("Wikipedia Article Extractor")
with st.expander("How to use this app"):
    st.write("1. Search for a Wikipedia topic.\n2. Select sections.\n3. Click Extract to preview the text.\n4. Click Download .txt to save it.")

steps = [("Search", state.article), ("Select", state.get("selected")),
         ("Extract", state.preview), ("Download", state.downloads)]
for column, (label, complete) in zip(st.columns(4), steps):
    column.caption(("✓ " if complete else "") + label)

with st.form("search"):
    topic = st.text_input("Enter a topic", placeholder="Hong Kong")
    search = st.form_submit_button("Search")

if search:
    state.article = None
    state.pop("selected", None)
    clear_preview()
    state.error = None
    if topic.strip():
        try:
            with st.spinner("Searching Wikipedia…"):
                state.article = WikipediaScraper().fetch_article(topic.strip())
        except (ArticleFetchError, ArticleParseError) as error:
            state.error = str(error)
    else:
        state.error = "Enter a topic to search."

if state.error:
    st.error(state.error)
article = state.article
if article:
    st.success(f"Found {article.title}")
    st.write(article.url)
    titles = dict(article.get_section_list())
    if titles:
        selected = st.multiselect("Select sections", list(titles), format_func=titles.get,
                                  key="selected", on_change=clear_preview)
        if st.button("Extract"):
            if selected:
                state.preview = format_txt(article, article.extract_sections(selected))
                state.filename = None
            else:
                st.warning("Select at least one section.")
    else:
        st.warning("This article has no top-level sections. Try another topic.")

if article and state.preview:
    with st.container(border=True):
        st.subheader("Preview")
        st.code(state.preview, language=None, wrap_lines=True)
    base = sanitize_filename(article.title)
    if base in state.downloads and state.filename is None:
        st.warning(f"You already downloaded {base} in this session.")
        if st.button("Use the same filename"):
            state.filename = base
        if st.button("Use a numbered filename"):
            number = 2
            while f"{base[:-4]} ({number}).txt" in state.downloads:
                number += 1
            state.filename = f"{base[:-4]} ({number}).txt"
    else:
        state.filename = state.filename or base
    if state.filename:
        st.caption(state.filename)
        st.download_button("Download .txt", data=state.preview, file_name=state.filename,
                           mime="text/plain", on_click=record_download, args=(state.filename,))

st.caption("Content from Wikipedia, CC BY-SA 4.0.")
