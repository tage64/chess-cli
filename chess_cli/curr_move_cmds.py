import asyncio
import datetime
import re
import tempfile
from argparse import ArgumentParser
from collections.abc import Iterable
from typing import assert_never

import chess
import chess.engine
import chess.pgn
import chess.svg
import click
import pyperclip

from . import nags
from .base import Base
from .repl import argparse_command
from .utils import (
    MoveNumber,
    add_to_comment_text,
    castling_descr,
    comment_text,
    score_str,
    update_comment_text,
)


class CurrMoveCmds(Base):
    """Commands related to the current move."""

    def show_evaluation(self) -> str | None:
        eval = self.game_node.eval()
        if eval is None:
            return None
        text: str = score_str(eval.relative)
        if self.game_node.eval_depth() is not None:
            text += f", Depth: {self.game_node.eval_depth()}"
        return text

    def show_fen(self) -> str:
        return self.game_node.board().fen()

    def show_nags(self) -> Iterable[str]:
        for nag in self.game_node.nags:
            yield f"  {nags.ascii_glyph(nag)}  {nags.description(nag)}"

    def show_board(self) -> str:
        board = self.game_node.board()
        cols: str = " ".join(
            map(
                chr,
                range(ord("a"), ord("h") + 1)
                if board.turn == chess.WHITE
                else range(ord("h"), ord("a") - 1, -1),
            )
        )
        text: str = "  " + cols + "\n"
        for row in range(8) if board.turn == chess.BLACK else range(7, -1, -1):
            text += f"{row + 1} "
            for col in range(8) if board.turn == chess.WHITE else range(7, -1, -1):
                try:
                    square_content: str = str(board.piece_map()[8 * row + col])
                except KeyError:
                    square_content = "+" if (row + col) % 2 == 0 else "-"
                text += f"{square_content} "
            text += f"{row + 1}\n"
        text += "  " + cols + "\n\n"
        if board.ep_square is not None:
            text += f"En-passant is possible at {chess.square_name(board.ep_square)}\n"
        text += castling_descr(board) + "\n"
        for color in [chess.WHITE, chess.BLACK]:
            text += "White" if color == chess.WHITE else "Black"
            text += ": "
            for piece_type in [
                chess.KING,
                chess.QUEEN,
                chess.ROOK,
                chess.BISHOP,
                chess.KNIGHT,
                chess.PAWN,
            ]:
                piece = chess.Piece(piece_type, color)
                squares = board.pieces(piece_type, color)
                if squares:
                    text += piece.symbol()
                    text += ",".join(chess.SQUARE_NAMES[sq] for sq in squares)
                    text += " "
            text += "\n"
        text += ("White" if board.turn == chess.WHITE else "Black") + " to move."
        return text

    def show_arrows(self) -> str | None:
        arrows: list = self.game_node.arrows()
        if not arrows:
            return None
        return str([
            f"{arrow.color} {chess.square_name(arrow.tail)}->{chess.square_name(arrow.head)}"
            for arrow in self.game_node.arrows()
        ])

    def show_clock(self) -> str | None:
        clock = self.game_node.clock()
        if clock is None:
            return None
        return str(datetime.timedelta(seconds=clock)).strip("0")

    show_argparser = ArgumentParser()

    @argparse_command(show_argparser, alias=["sh"])
    def do_show(self, args) -> None:
        """Show position, comments, NAGs and more about the current move."""
        self.poutput(f"FEN: {self.show_fen()}")
        self.poutput(f"\n{self.show_board()}")
        starting_comment: str = comment_text(self.game_node.starting_comment)
        if isinstance(self.game_node, chess.pgn.ChildNode) and starting_comment:
            self.poutput(starting_comment)
            self.poutput(f"    {MoveNumber.last(self.game_node)} {self.game_node.san()}")
        comment: str = comment_text(self.game_node.comment)
        if comment:
            self.poutput(comment)
        for nag in self.show_nags():  # noqa: B007
            self.poutput("NAG: {nag}")
        evaluation: str | None = self.show_evaluation()
        if evaluation is not None:
            self.poutput("Evaluation: {evaluation}")
        arrows: str | None = self.show_arrows()
        if arrows is not None:
            self.poutput(f"Arrows: {arrows}")
        clock: str | None = self.show_clock()
        if clock is not None:
            self.poutput(f"Clock: {clock}")

    fen_argparser = ArgumentParser()
    fen_argparser.add_argument(
        "-c", "--clipboard", action="store_true", help="Copy the FEN to the clipboard."
    )

    @argparse_command(fen_argparser)
    def do_fen(self, args) -> None:
        """Show the position as FEN (Forsynth-Edwards Notation)."""
        if args.clipboard:
            pyperclip.copy(self.show_fen())
        else:
            self.poutput(self.show_fen())

    board_argparser = ArgumentParser()

    @argparse_command(board_argparser, alias=["b"])
    def do_board(self, args) -> None:
        """Show the current position as an ASCII chess board."""
        self.poutput(self.show_board())

    comment_argparser = ArgumentParser()
    comment_argparser.add_argument(
        "-s",
        "--starting-comment",
        action="store_true",
        help=(
            "If this move is starting a new variation, act on the starting comment of that"
            " variation."
        ),
    )
    comment_argparser.add_argument(
        "-r",
        "--raw",
        action="store_true",
        help=(
            "Act on the raw pgn comment which may override embedded commands like arrows and"
            " clocks."
        ),
    )
    comment_subcmds = comment_argparser.add_subparsers(dest="subcmd")
    comment_subcmds.add_parser("show", aliases=["sh"], help="Show the comment at the current move.")
    comment_subcmds.add_parser("rm", help="Remove the comment at the current move.")
    comment_subcmds.add_parser("edit", aliases=["e"], help="Open the comment in your editor.")
    comment_set_argparser = comment_subcmds.add_parser(
        "set", aliases=["s"], help="Set the comment for this move."
    )
    comment_set_argparser.add_argument("comment", help="The new text.")
    comment_append_argparser = comment_subcmds.add_parser(
        "append", help="Append text to the already existing comment."
    )
    comment_append_argparser.add_argument("comment", help="The text to append.")

    @argparse_command(comment_argparser, alias=["c"])
    async def do_comment(self, args) -> None:
        """Show, edit or remove the comment at the current move."""
        if args.starting_comment and not self.game_node.starts_variation():
            self.poutput(
                "Error: Starting comments can only exist on moves that starts a variation."
            )
            return

        comment: str = (
            self.game_node.comment if not args.starting_comment else self.game_node.starting_comment
        )
        comment = comment if args.raw else comment_text(comment)

        def set_comment(new_comment: str) -> None:
            if args.starting_comment:
                self.game_node.starting_comment = new_comment
            else:
                self.game_node.comment = new_comment

        match args.subcmd:
            case "show" | "sh" | None:
                self.poutput(comment)
            case "rm":
                set_comment("")
            case "set" | "s":
                new_comment = (
                    args.comment if args.raw else update_comment_text(comment, args.comment)
                )
                set_comment(new_comment)
            case "append":
                set_comment(add_to_comment_text(comment, args.comment))
            case "edit" | "e":
                fd, file_name = tempfile.mkstemp(suffix=".txt", text=True)
                new_comment: str | bytes | None = await asyncio.to_thread(click.edit, comment)
                if isinstance(comment, bytes):
                    comment = comment.decode()
                if new_comment is not None:
                    if not args.raw:
                        new_comment = update_comment_text(comment, new_comment)
                    set_comment(new_comment)
                    print(f"Successfully updated comment to:\n{new_comment}")
            case _:
                raise AssertionError("Unknown subcommand.")

    nag_argparser = ArgumentParser()
    nag_subcmds = nag_argparser.add_subparsers(dest="subcmd")
    nag_subcmds.add_parser("show", help="Show the NAGs at this move.")
    nag_add_argparser = nag_subcmds.add_parser(
        "add", help="Add a nag (numeric annotation glyph) to this move."
    )
    nag_add_argparser.add_argument(
        "nag", help="NAG: either a number like '$17' or an ascii glyph like '!' or '?!'."
    )
    nag_rm_argparser = nag_subcmds.add_parser("rm", help="Remove an NAG at this move.")
    nag_rm_argparser.add_argument(
        "nag", help="NAG: either a number like '$17' or an ascii glyph like '!'."
    )
    nag_subcmds.add_parser("clear", help="Clear all NAGs at this move.")

    @argparse_command(nag_argparser)
    def do_nag(self, args) -> None:
        """Show, edit or remove NAGs (numeric annotation glyphs, E.G.

        '!?') at the current move.
        """
        match args.subcmd:
            case "show":
                for nag_str in self.show_nags():
                    self.poutput("  " + nag_str)
            case "add":
                try:
                    nag: int = nags.parse_nag(args.nag)
                except ValueError as e:
                    self.poutput(f"Error: invalid NAG {args.nag}: {e}")
                    return
                self.game_node.nags.add(nag)
                self.poutput(f"Set NAG ({nags.ascii_glyph(nag)}): {nags.description(nag)}.")
            case "rm":
                try:
                    nag = nags.parse_nag(args.nag)
                except ValueError as e:
                    self.poutput(f"Error: invalid NAG {args.nag}: {e}")
                    return
                try:
                    self.game_node.nags.remove(nag)
                except KeyError:
                    self.poutput(f"Error: NAG '{nags.ascii_glyph(nag)}' was not set on this move.")
            case "clear":
                self.game_node.nags = set()
            case _:
                raise AssertionError("Unknown subcommand.")

    evaluation_argparser = ArgumentParser()
    evaluation_subcmds = evaluation_argparser.add_subparsers(dest="subcmd")
    evaluation_show_argparser = evaluation_subcmds.add_parser(
        "show",
        help=(
            "Show the evaluation at this move. (Note that this is the evaluation stored in the pgn"
            " comment and might neither come from an engine nore be correct."
        ),
    )
    evaluation_rm_argparser = evaluation_subcmds.add_parser(
        "rm", help="Remove the evaluation at this move."
    )
    evaluation_set_argparser = evaluation_subcmds.add_parser(
        "set", help="Set an evaluation for this move."
    )
    evaluation_set_group = evaluation_set_argparser.add_mutually_exclusive_group(required=True)
    evaluation_set_group.add_argument(
        "--cp",
        type=int,
        help="Relative score in centi pawns from the player to move's point of view.",
    )
    evaluation_set_group.add_argument(
        "--mate",
        "--mate-in",
        type=int,
        help="The player to move can force mate in the given number of moves.",
    )
    evaluation_set_group.add_argument(
        "--mated",
        "--mated-in",
        type=int,
        help="The player to move will be mated in the given number of moves.",
    )
    evaluation_set_argparser.add_argument(
        "-d", "--depth", type=int, help="The depth at which the analysis was made."
    )

    @argparse_command(evaluation_argparser, alias=["eval"])
    def do_evaluation(self, args) -> None:
        """Show, edit or remove evaluations at the current move."""
        match args.subcmd:
            case "show" | None:
                text = self.show_evaluation()
                if text is not None:
                    self.poutput(text)
                else:
                    self.poutput("No evaluation at this move.")
            case "rm":
                self.game_node.set_eval(None)
            case "set":
                if args.mate is not None:
                    score: chess.engine.Score = chess.engine.Mate(args.mate)
                elif args.mated is not None:
                    score = chess.engine.Mate(-args.mated)
                else:
                    score = chess.engine.Cp(args.cp)
                self.game_node.set_eval(
                    chess.engine.PovScore(score, self.game_node.turn()), args.depth
                )
            case _:
                raise AssertionError("Unknown subcommand.")

    arrow_argparser = ArgumentParser()
    arrow_subcmds = arrow_argparser.add_subparsers(dest="subcmd")
    arrow_subcmds.add_parser("show", aliases=["s", "sh"], help="Show all arrows on the board.")
    arrow_subcmds.add_parser("clear", aliases=["c", "cl"], help="Clear all arrows on the board.")
    arrow_rm_argparser = arrow_subcmds.add_parser(
        "rm", help="Remove all arrows between two squares."
    )
    arrow_rm_argparser.add_argument(
        "_from", type=chess.parse_square, help="The square from which the arrow is drawn."
    )
    arrow_rm_argparser.add_argument(
        "to", type=chess.parse_square, help="The square which the arrow is pointing to."
    )
    arrow_add_argparser = arrow_subcmds.add_parser(
        "add", aliases=["a"], help="Draw an arrow on the board."
    )
    arrow_add_argparser.add_argument(
        "_from", type=chess.parse_square, help="The square from which the arrow is drawn."
    )
    arrow_add_argparser.add_argument(
        "to", type=chess.parse_square, help="The square which the arrow is pointing to."
    )
    arrow_add_argparser.add_argument(
        "color",
        choices=["red", "r", "yellow", "y", "green", "g", "blue", "b"],
        default="green",
        nargs="?",
        help="Color of the arrow. Red/yellow/green/blue can be abbreviated as r/y/g/b.",
    )

    @argparse_command(arrow_argparser, alias=["ar"])
    def do_arrow(self, args) -> None:
        """Show, edit or remove arrows at the current move."""
        color_abbreviations: dict[str, str] = {"g": "green", "y": "yellow", "r": "red", "b": "blue"}

        match args.subcmd:
            case "show" | "s" | "sh" | None:
                text = self.show_arrows()
                if text is not None:
                    self.poutput(text)
            case "add" | "a":
                color = color_abbreviations.get(args.color, args.color)
                self.game_node.set_arrows([
                    *self.game_node.arrows(),
                    chess.svg.Arrow(args._from, args.to, color=color),
                ])
            case "rm":
                self.game_node.set_arrows(
                    arr
                    for arr in self.game_node.arrows()
                    if not (args._from == arr.tail or args.to == arr.head)
                )
            case "clear" | "c" | "cl":
                self.game_node.set_arrows([])
            case x:
                assert_never(x)

    pgn_clock_argparser = ArgumentParser()
    pgn_clock_subcmds = pgn_clock_argparser.add_subparsers(dest="subcmd")
    pgn_clock_subcmds.add_parser(
        "show", help="Show the remaining time for the player making this move."
    )
    pgn_clock_subcmds.add_parser("rm", help="Remove the clock information at this move.")
    pgn_clock_set_argparser = pgn_clock_subcmds.add_parser(
        "set", help="Set the remaining time for the player making this move."
    )
    pgn_clock_set_argparser.add_argument("time", help="Remaining time.")

    @argparse_command(pgn_clock_argparser, alias="pgnclk")
    def do_pgn_clock(self, args) -> None:
        """Show, edit or remove clock information in [%clk ...] annotations
        in the PGN comment at the current move."""
        match args.subcmd:
            case "show":
                text = self.show_clock()
                if text is not None:
                    self.poutput(text)
            case "rm":
                self.game_node.set_clock(None)
            case "set":
                time_parsed = re.fullmatch(r"(\d+)(:(\d+))?(:(\d+))?([.,](\d+))?", args.time)
                if time_parsed is None:
                    self.poutput(f"Error: Couldn't parse time '{args.time}'.")
                    return
                time_groups = time_parsed.groups()
                time: float = float(time_groups[0])
                if time_groups[2]:
                    time = time * 60 + float(time_groups[2])
                    if time_groups[4]:
                        time = time * 60 + float(time_groups[4])
                if time_groups[6]:
                    time += float("0." + time_groups[6])
                self.game_node.set_clock(time)
            case _:
                raise AssertionError("Unhandled subcommand.")
