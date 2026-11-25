from datetime import datetime
from copy import deepcopy
import re
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

from bs4 import BeautifulSoup, NavigableString, Tag
import requests


WIKIPEDIA_BASE_URL = "https://en.wikipedia.org/wiki/"
USER_AGENT = (
    "ISOM3400WikipediaExtractor/1.0 "
    "(Educational Python Project; contact@example.com)"
)
WIKIPEDIA_LICENSE = (
    "Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)"
)

# Classes of layout/navigation elements to ignore during content extraction
IGNORED_ELEMENT_CLASSES = {
    "mw-editsection",
    "navbox",
    "vertical-navbox",
    "sistersitebox",
    "ambox",
    "metadata",
    "hatnote",
    "shortdescription",
    "infobox",
    "sidebar",
    "toc",
    "reflist",
    "refbegin",
    "mw-references-wrap",
    "gallery",
    "noprint",
}


class ArticleFetchError(Exception):
    """Raised when a Wikipedia article cannot be retrieved or found."""


class ArticleParseError(Exception):
    """Raised when unexpected HTML structures prevent article parsing."""


class WikipediaArticle:
    """
    Represents a parsed Wikipedia article and provides methods to inspect
    its top-level sections and extract clean text from selected sections.
    """

    def __init__(self, title: str, url: str, soup: BeautifulSoup) -> None:
        self.title: str = title
        self.url: str = url
        self.soup: BeautifulSoup = soup
        self.sections: List[Dict[str, object]] = []
        self._parse_sections()

    def _has_ignored_class(self, tag: Tag) -> bool:
        """Check whether a Tag carries any layout/navigation CSS class."""
        classes = set(tag.get("class") or [])
        return bool(classes & IGNORED_ELEMENT_CLASSES)

    def _clean_text(self, text: str) -> str:
        """
        Remove inline citation markers (e.g., [1], [2], [a], [citation needed])
        and normalize excessive whitespace.
        """
        cleaned = re.sub(
            r"\[(?:\d+|[a-zA-Z]|citation needed|note \d+|clarification needed)\]",
            "",
            text,
        )
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        return cleaned.strip()

    def _extract_tag_text(self, tag: Tag) -> str:
        """Extract clean text from a tag after stripping superscript references."""
        for sup in tag.find_all("sup", class_="reference"):
            sup.decompose()
        for edit_span in tag.find_all("span", class_="mw-editsection"):
            edit_span.decompose()
        return self._clean_text(tag.get_text(" ", strip=True))

    def _detect_heading(self, node: Tag) -> Optional[Tuple[int, str]]:
        """
        Identify if a DOM node is a section heading (h2-h6), supporting both
        modern <div class="mw-heading mw-headingX"> wrappers and bare <hX> tags.
        Returns a tuple of (heading_level, heading_title) or None.
        """
        heading_tag: Optional[Tag] = None

        if node.name == "div" and "mw-heading" in (node.get("class") or []):
            heading_tag = node.find(["h2", "h3", "h4", "h5", "h6"])
        elif node.name in ("h2", "h3", "h4", "h5", "h6"):
            heading_tag = node

        if heading_tag is None:
            return None

        level = int(heading_tag.name[1])
        title_text = self._extract_tag_text(heading_tag)
        if not title_text:
            return None
        return (level, title_text)

    def _iter_content_nodes(self, container: Tag) -> Iterator[Tag]:
        """
        Yield content nodes in document order, transparently unwrapping
        Parsoid <section> wrappers while skipping ignored layout elements.
        """
        for child in container.children:
            if isinstance(child, NavigableString) or not isinstance(child, Tag):
                continue
            if self._has_ignored_class(child):
                continue
            if child.name == "section":
                yield from self._iter_content_nodes(child)
            else:
                yield child

    def _find_main_container(self) -> Tag:
        """
        Locate the main article parser-output container, skipping any empty
        notice/indicator mw-parser-output blocks that precede the article body.
        """
        content_div = self.soup.find("div", id="mw-content-text")
        if content_div is None:
            raise ArticleParseError(
                "Could not locate main content container ('mw-content-text')."
            )

        parser_outputs = content_div.find_all("div", class_="mw-parser-output")
        for candidate in parser_outputs:
            if candidate.find("h2") is not None:
                return candidate

        if parser_outputs:
            return max(parser_outputs, key=lambda tag: len(tag.get_text()))

        return content_div

    def _parse_sections(self) -> None:
        """
        Locate the main parser output container and group top-level (h2)
        sections along with their constituent DOM elements.
        """
        try:
            container = self._find_main_container()

            current_section: Optional[Dict[str, object]] = None
            section_counter = 0

            for node in self._iter_content_nodes(container):
                heading_info = self._detect_heading(node)
                if heading_info is not None and heading_info[0] == 2:
                    section_counter += 1
                    current_section = {
                        "index": section_counter,
                        "title": heading_info[1],
                        "nodes": [],
                    }
                    self.sections.append(current_section)
                elif current_section is not None:
                    nodes_list = current_section["nodes"]
                    if isinstance(nodes_list, list):
                        nodes_list.append(node)

        except ArticleParseError:
            raise
        except Exception as exc:
            raise ArticleParseError(
                f"Unexpected error while parsing article structure: {exc}"
            ) from exc

    def get_section_list(self) -> List[Tuple[int, str]]:
        """Return a list of (section_number, section_title) tuples."""
        return [
            (int(sec["index"]), str(sec["title"]))
            for sec in self.sections
        ]

    def _format_list(self, list_tag: Tag, indent: int = 0) -> List[str]:
        """Format an ordered (<ol>) or unordered (<ul>) list into clean lines."""
        lines: List[str] = []
        is_ordered = list_tag.name == "ol"
        items = list_tag.find_all("li", recursive=False)

        for idx, li in enumerate(items, start=1):
            nested_lists = li.find_all(["ul", "ol"], recursive=False)
            for nl in nested_lists:
                nl.extract()

            item_text = self._extract_tag_text(li)
            prefix = f"{idx}." if is_ordered else "-"
            indent_str = "  " * indent
            if item_text:
                lines.append(f"{indent_str}{prefix} {item_text}")

            for nl in nested_lists:
                lines.extend(self._format_list(nl, indent=indent + 1))

        return lines

    def _format_table(self, table_tag: Tag) -> List[str]:
        """Format an HTML table into readable pipe-delimited rows."""
        if self._has_ignored_class(table_tag):
            return []

        rows = table_tag.find_all("tr")
        if not rows:
            return []

        formatted_rows: List[str] = []
        for row_idx, tr in enumerate(rows):
            cells = tr.find_all(["th", "td"], recursive=False)
            if not cells:
                continue

            cell_texts = [self._extract_tag_text(cell) for cell in cells]
            if not any(cell_texts):
                continue

            row_line = " | ".join(cell_texts)
            formatted_rows.append(row_line)

            if row_idx == 0 and any(cell.name == "th" for cell in cells):
                formatted_rows.append("-" * min(len(row_line), 80))

        return formatted_rows

    def _extract_node_content(self, node: Tag) -> List[str]:
        """Extract formatted text blocks from a single DOM element."""
        if self._has_ignored_class(node):
            return []

        # Subheadings (h3 - h6)
        heading_info = self._detect_heading(node)
        if heading_info is not None and heading_info[0] >= 3:
            level, sub_title = heading_info
            hashes = "#" * level
            return [f"\n{hashes} {sub_title}"]

        # Paragraphs
        if node.name == "p":
            text = self._extract_tag_text(node)
            return [text] if text else []

        # Ordered and unordered lists
        if node.name in ("ul", "ol"):
            return self._format_list(node)

        # Tables
        if node.name == "table":
            table_lines = self._format_table(node)
            if table_lines:
                return ["[Table]"] + table_lines
            return []

        # Blockquotes
        if node.name == "blockquote":
            quote_text = self._extract_tag_text(node)
            return [f'> "{quote_text}"'] if quote_text else []

        # Wrapper divs (such as multi-column list containers like div.div-col)
        if node.name == "div":
            extracted: List[str] = []
            for child in node.children:
                if isinstance(child, Tag):
                    extracted.extend(self._extract_node_content(child))
            return extracted

        return []

    def extract_sections(self, selected_indices: List[int]) -> str:
        """
        Extract and format content for the given 1-based section numbers.
        Includes section titles, paragraphs, lists, tables, and subheadings.
        """
        section_map: Dict[int, Dict[str, object]] = {
            int(sec["index"]): sec for sec in self.sections
        }

        output_blocks: List[str] = []

        for idx in selected_indices:
            section = section_map.get(idx)
            if section is None:
                continue

            sec_title = str(section["title"])
            divider = "=" * len(sec_title)
            section_lines: List[str] = [f"{sec_title}\n{divider}"]

            nodes = section.get("nodes", [])
            if isinstance(nodes, list):
                for node in nodes:
                    if isinstance(node, Tag):
                        blocks = self._extract_node_content(deepcopy(node))
                        if blocks:
                            section_lines.append("\n".join(blocks))

            if len(section_lines) == 1:
                section_lines.append("(No extractable text content in this section.)")

            output_blocks.append("\n\n".join(section_lines))

        return "\n\n" + ("\n\n" + "-" * 80 + "\n\n").join(output_blocks) + "\n"


class WikipediaScraper:
    """
    Handles HTTP requests to Wikipedia and constructs WikipediaArticle objects.
    """

    def __init__(self, timeout: int = 10) -> None:
        self.timeout: int = timeout
        self.session: requests.Session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def _build_url(self, topic: str) -> str:
        """Convert a user-entered topic string into a valid Wikipedia URL."""
        normalized = "_".join(topic.strip().split())
        encoded = quote(normalized, safe="()_,-'")
        return f"{WIKIPEDIA_BASE_URL}{encoded}"

    def fetch_article(self, topic: str) -> WikipediaArticle:
        """
        Fetch and parse a Wikipedia article by topic name.
        Raises ArticleFetchError or ArticleParseError on failure.
        """
        url = self._build_url(topic)

        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.exceptions.Timeout as exc:
            raise ArticleFetchError(
                "The request timed out while connecting to Wikipedia. "
                "Please check your internet connection and try again."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise ArticleFetchError(
                "Network connection error: Unable to reach Wikipedia."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ArticleFetchError(
                f"An unexpected network error occurred: {exc}"
            ) from exc

        if response.status_code == 404:
            raise ArticleFetchError(
                f"Article '{topic}' was not found on Wikipedia (HTTP 404)."
            )

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise ArticleFetchError(
                f"HTTP error {response.status_code} while retrieving '{topic}'."
            ) from exc

        try:
            soup = BeautifulSoup(response.text, "html.parser")
            heading_tag = soup.find("h1", id="firstHeading")
            if heading_tag is None:
                raise ArticleParseError(
                    "Unable to find the article title in the returned page."
                )

            article_title = heading_tag.get_text(" ", strip=True)
            final_url = response.url
            return WikipediaArticle(
                title=article_title,
                url=final_url,
                soup=soup,
            )
        except ArticleParseError:
            raise
        except Exception as exc:
            raise ArticleParseError(
                f"Failed to parse HTML response for '{topic}': {exc}"
            ) from exc


def sanitize_filename(title: str) -> str:
    """Create a safe, meaningful .txt filename from a Wikipedia article title."""
    safe = re.sub(r'[<>:"/\\|?*]', "", title).strip()
    safe = re.sub(r"\s+", "_", safe)
    if not safe:
        safe = "wikipedia_article"
    return f"{safe}.txt"


def format_txt(article: WikipediaArticle, content: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"{'=' * 80}\n"
        f"Article Title : {article.title}\n"
        f"Source URL    : {article.url}\n"
        f"Extracted At  : {timestamp}\n"
        f"License       : {WIKIPEDIA_LICENSE}\n"
        f"{'=' * 80}\n"
    )

    return header + content

