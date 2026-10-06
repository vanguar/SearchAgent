"""Shared text cleaning for source descriptions and vacancy normalization."""
from __future__ import annotations

import html
import re

_HTML_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|br|li|ul|ol|tr|h[1-6]|table|section|article|blockquote)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RUN_RE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_html(text: str) -> str:
    """Convert vacancy HTML to text while preserving block boundaries."""
    without_blocks = _HTML_BLOCK_TAG_RE.sub("\n", text)
    without_tags = _HTML_TAG_RE.sub(" ", without_blocks)
    unescaped = html.unescape(without_tags).replace("\u00a0", " ")
    collapsed = _WHITESPACE_RUN_RE.sub(" ", unescaped)
    lines = [line.strip() for line in collapsed.split("\n")]
    return _BLANK_LINES_RE.sub("\n\n", "\n".join(lines)).strip()


def looks_like_html(text: str) -> bool:
    return "<" in text and ">" in text and bool(_HTML_TAG_RE.search(text))
