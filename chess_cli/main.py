import asyncio
from argparse import ArgumentParser

import spdx_license_list

from . import __author__, __metadata__, __version__
from .analysis_cmds import AnalysisCmds
from .base import InitArgs
from .curr_move_cmds import CurrMoveCmds
from .engine_cmds import EngineCmds
from .game_cmds import GameCmds
from .game_shortcuts import GameShortcuts
from .lichess_cmds import LichessCmds
from .record_cmds import RecordCmds


class Main(
    AnalysisCmds, CurrMoveCmds, EngineCmds, GameCmds, LichessCmds, GameShortcuts, RecordCmds
):
    """Main class for the chess-cli app."""

    def __init__(self, args: InitArgs) -> None:
        print(f"Welcome to Chess-CLI v{__version__}")
        print(f"Author: {__author__} <{__metadata__["author-email"]}>")
        print(f"Licensed under {spdx_license_list.LICENSES[__metadata__["license"]].name}")
        print()
        print("Type 'help' to get a list of possible commands.")
        print()
        super().__init__(args)


def main() -> None:
    """Run the program."""
    argparser = ArgumentParser(description="A repl to edit and analyse chess games.")
    argparser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show version and exit.",
    )
    argparser.add_argument("pgn_file", nargs="?", help="Open the given pgn file.")
    argparser.add_argument("--config-file", type=str, help="Path to the config file.")
    args = argparser.parse_args()
    init_args: InitArgs = InitArgs(**{
        key: val for key, val in vars(args).items() if val is not None
    })
    asyncio.run(Main(init_args).cmd_loop())
