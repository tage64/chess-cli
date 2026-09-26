"""Render Markdown as plain, wrapped terminal text without ANSI styling."""

import re
import textwrap
import unicodedata

from markdown_it import MarkdownIt

# Strip ANSI CSI, OSC (including hyperlinks), and other ESC sequences before parsing.
_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|.)")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_ASCII_PUNCTUATION = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "—": "-",
    "…": "...",
    "•": "-",
    "→": "->",
    "←": "<-",
    "\u00a0": " ",
})


def _clean(text: str) -> str:
    text = _CONTROL_RE.sub("", _ESCAPE_RE.sub("", text))
    text = unicodedata.normalize("NFKD", text.translate(_ASCII_PUNCTUATION))
    return (
        "".join(c for c in text if not unicodedata.combining(c))
        .encode("ascii", "replace")
        .decode("ascii")
    )


def wrap_markdown(markdown: str, width: int) -> str:
    """Convert Markdown to wrapped plain text, preserving lists and code blocks."""
    blocks: list[tuple[str, bool]] = []
    lists: list[tuple[str, int]] = []
    item_prefix = ""
    in_item = False
    for token in MarkdownIt().parse(_clean(markdown)):
        if token.type in ("bullet_list_open", "ordered_list_open"):
            marker = "-" if token.type == "bullet_list_open" else "."
            lists.append((marker, int(token.attrGet("start") or 1)))
        elif token.type in ("bullet_list_close", "ordered_list_close"):
            lists.pop()
        elif token.type == "list_item_open":
            marker, number = lists[-1]
            item_prefix = "  " * (len(lists) - 1) + ("- " if marker == "-" else f"{number}. ")
            lists[-1] = (marker, number + 1)
            in_item = True
        elif token.type == "list_item_close":
            in_item = False
        elif token.type == "inline":
            # Container tokens (emphasis, links, etc.) carry no text themselves.
            content = "".join(
                "\n"
                if child.type == "hardbreak"
                else " "
                if child.type == "softbreak"
                else child.content
                for child in token.children or []
                if child.type in ("text", "code_inline", "image", "softbreak", "hardbreak")
            )
            content = _clean(content)
            if content.strip():
                prefix = item_prefix if in_item else ""
                wrapper = textwrap.TextWrapper(
                    width=max(width, len(prefix) + 1),
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                blocks.append((
                    "\n".join(wrapper.fill(line) for line in content.split("\n")),
                    in_item,
                ))
        elif token.type in ("fence", "code_block"):
            blocks.append((_clean(token.content).rstrip("\n"), in_item))

    result = ""
    previous_list_item = False
    for text, is_list_item in blocks:
        if result:
            result += "\n" if previous_list_item and is_list_item else "\n\n"
        result += text
        previous_list_item = is_list_item
    return result + "\n" if result else ""
