from .analysis_cmds import *
from .base import *
from .curr_move_cmds import *
from .engine_cmds import *
from .game_cmds import *
from .lichess_api import *

import argparse
import sys
from typing import *


class Main(AnalysisCmds, CurrMoveCmds, EngineCmds, GameCmds, LichessApi):
    "Main class for the chess-cli app."

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)


def main() -> None:
    "The main function of the entire program."
    argparser = argparse.ArgumentParser(description="A repl to edit and analyse chess games.")
    argparser.add_argument("pgn_file", nargs="?", help="Open the given pgn file.")
    argparser.add_argument("--config-file", type=str, help="Path to the config file.")
    args = argparser.parse_args()
    init_args: InitArgs = InitArgs(
        **{key: val for key, val in vars(args).items() if val is not None}
    )
    sys.exit(Main(init_args).cmdloop())
