import argparse
from typing import assert_never

import chess

from .clock import Clock
from .engine import Engine
from .engine_player import EnginePlayer
from .match import Match, Player
from .repl import argparse_command, command
from .utils import parse_time, show_time


class MatchCmds(Match, EnginePlayer):
    """Commands to play a chess match."""

    match_argparser = argparse.ArgumentParser()
    match_subcmds = match_argparser.add_subparsers(dest="subcmd", required=True)
    match_new_argparser = match_subcmds.add_parser(
        "new", aliases=["n"], help="Start a new chess match against something."
    )
    match_new_argparser.add_argument("engine", help="Name of the engine to play against.")
    match_new_argparser.add_argument(
        "color", choices=["white", "black"], help="The color for the machine to play."
    )

    @argparse_command(match_argparser, alias="ma")
    def do_match(self, args) -> None:
        """Commands to make a chess match against the machine."""
        match args.subcmd:
            case "new" | "n":
                player = self.mk_engine_player(args.engine)
                color = chess.WHITE if args.color == "white" else chess.BLACK
                self.add_player(player, color)
            case x:
                assert_never(x)
