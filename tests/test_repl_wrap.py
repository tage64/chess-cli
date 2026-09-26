"""Tests for plain, wrapped REPL Markdown output."""

import os
from unittest.mock import patch

import pytest

from chess_cli.match_cmds import MatchCmds
from chess_cli.repl import Repl


def test_print_wrapped_markdown_preserves_structure(capsys):
    markdown = (
        "# Heading\n\n"
        "A long sentence with several **bold** words and [a link](https://example.com) "
        "that should wrap onto another line.\n\n"
        "- First item with several words that should wrap\n"
        "- Second item\n\n"
        "```python\nprint('hello')\n```\n"
    )
    with patch("chess_cli.repl.shutil.get_terminal_size", return_value=os.terminal_size((28, 24))):
        Repl().print_wrapped_markdown(markdown)

    assert capsys.readouterr().out == (
        "Heading\n\n"
        "A long sentence with several\n"
        "bold words and a link that\n"
        "should wrap onto another\n"
        "line.\n\n"
        "- First item with several\n"
        "  words that should wrap\n"
        "- Second item\n\n"
        "print('hello')\n"
    )


def test_print_wrapped_markdown_removes_terminal_controls(capsys):
    markdown = (
        "\x1b[31m**Red**\x1b[0m and \x1b]8;;https://example.com\x07link\x1b]8;;\x07 "
        "with café, \u2018quotes\u2019 • and a king ♔\n"
    )
    with patch("chess_cli.repl.shutil.get_terminal_size", return_value=os.terminal_size((80, 24))):
        Repl().print_wrapped_markdown(markdown)
    assert capsys.readouterr().out == "Red and link with cafe, 'quotes' - and a king ?\n"


def test_argparse_help_is_plain_even_when_colors_are_forced(capsys, monkeypatch):
    monkeypatch.setenv("PYTHON_COLORS", "1")
    for args in (["--help"], ["start", "--help"]):
        with pytest.raises(SystemExit) as exit_info:
            MatchCmds.match_argparser.parse_args(args)
        assert exit_info.value.code == 0
        output = capsys.readouterr().out
        assert "usage: match" in output
        assert "positional arguments:" in output or "options:" in output
        assert "\x1b" not in output
    assert "\x1b" not in MatchCmds.do_match.long_help
