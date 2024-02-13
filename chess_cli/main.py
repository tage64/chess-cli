from cmd2 import Cmd2ArgumentParser

from .base import Base, InitArgs


# class Main(AnalysisCmds, CurrMoveCmds, EngineCmds, GameCmds, GameHotKeys, LichessCmds):
class Main(Base):
    """Main class for the chess-cli app."""

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)


def main() -> None:
    """Run the program."""
    argparser = Cmd2ArgumentParser(description="A repl to edit and analyse chess games.")
    argparser.add_argument("pgn_file", nargs="?", help="Open the given pgn file.")
    argparser.add_argument("--config-file", type=str, help="Path to the config file.")
    args = argparser.parse_args()
    init_args: InitArgs = InitArgs(
        **{key: val for key, val in vars(args).items() if val is not None}
    )
    Main(init_args).cmd_loop()
