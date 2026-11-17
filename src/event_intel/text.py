"""Readable text from an HTML page: drop scripts, styles and navigation, keep block structure."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP = frozenset(
    {"script", "style", "noscript", "nav", "footer", "header", "svg", "form", "button", "iframe", "ix:header"}
)
_BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "td"})


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text) with collapsed whitespace and one blank line between blocks."""

    parser = _TextParser()
    parser.feed(html)
    parser.close()
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return re.sub(r"\s+", " ", parser.title).strip(), text
