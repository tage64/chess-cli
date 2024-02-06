import argparse
import sys

from .analysis_cmds import AnalysisCmds
from .base import InitArgs
from .curr_move_cmds import CurrMoveCmds
from .engine_cmds import EngineCmds
from .game_cmds import GameCmds
from .game_hot_keys import GameHotKeys
from .lichess_cmds import LichessCmds


class Main(AnalysisCmds, CurrMoveCmds, EngineCmds, GameCmds, GameHotKeys, LichessCmds):
    """Main class for the chess-cli app."""

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)


def main() -> None:
    """Run the program."""
    argparser = argparse.ArgumentParser(description="A repl to edit and analyse chess games.")
    argparser.add_argument("pgn_file", nargs="?", help="Open the given pgn file.")
    argparser.add_argument("--config-file", type=str, help="Path to the config file.")
    args = argparser.parse_args()
    init_args: InitArgs = InitArgs(
        **{key: val for key, val in vars(args).items() if val is not None}
    )
    sys.exit(Main(init_args).cmdloop())
