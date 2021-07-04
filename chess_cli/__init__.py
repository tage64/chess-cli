import functools
import sys
from typing import NamedTuple, Optional

import chess
import chess.pgn
import cmd2

__version__ = '0.1.0'


@functools.total_ordering
class Fullmove(NamedTuple):
    """ A fullmove is a move number and the color that made the move.
    E.G. "1." would be move number 1 and color white while "10..." would be move number 10 and color black.
    """

    move_number: int
    color: chess.Color

    @staticmethod
    def from_board(board: chess.Board):
        """ Get the fullmove from the previously executed move on a board.
        # Returns one if the move stack is empty.
        """
        return Fullmove(board.fullmove_number, board.turn).previous()

    @staticmethod
    def parse(move_text: str):
        """ Parse a chess move number like "3." or "5...".
        Plain numbers without any dots at the end will be parsed as if it was white who moved.
        Will raise ValueError if the parsing failes.
        """

        if move_text.endswith("..."):
            number = int(move_text[:-3])
            color = chess.BLACK
        elif move_text.endswith("."):
            number = int(move_text[:-1])
            color = chess.WHITE
        else:
            number = int(move_text)
            color = chess.WHITE
        return Fullmove(number, color)

    def previous(self):
        " Get previous move. "
        if self.color == chess.WHITE:
            return Fullmove(self.move_number - 1, chess.BLACK)
        else:
            return Fullmove(self.move_number, chess.WHITE)

    def __str__(self) -> str:
        return str(
            self.move_number) + ("." if self.color == chess.WHITE else "...")

        def __lt__(self, other) -> bool:
            return self.move_number < other.move_number or self.color == chess.WHITE and other.color == chess.BLACK


class ChessCli(cmd2.Cmd):
    """A repl to edit and analyse chess games. """
    def __init__(self, file_name: Optional[str] = None):
        # Set cmd shortcuts
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts)

        # Read the pgn file
        if file_name is not None:
            with open(file_name) as pgn_file:
                res = chess.pgn.read_game(pgn_file)
            if res is None:
                self.poutput(f"Error: Couldn't find any game in {file_name}")
                self.game_node: chess.pgn.GameNode = chess.pgn.Game()
            else:
                self.game_node = res
        else:
            self.game_node = chess.pgn.Game()
        self.file_name: Optional[str] = file_name

        self.register_postcmd_hook(self.set_prompt)
        self.set_prompt(None)  # type: ignore

    def set_prompt(
        self, postcommand_data: cmd2.plugin.PostcommandData
    ) -> cmd2.plugin.PostcommandData:
        if self.game_node.parent is None:
            # This is the root node.
            self.prompt = "start: "
        else:
            self.prompt = f"{Fullmove.from_board(self.game_node.board())} {self.game_node.san()}: "  # type: ignore
        return postcommand_data

    play_argparser = cmd2.Cmd2ArgumentParser()
    play_argparser.add_argument(
        "moves",
        nargs="+",
        help="A list of moves in standard algibraic notation.")
    play_argparser.add_argument(
        "-c",
        "--comment",
        help=
        "Add a comment for the move (or the last move if more than one is supplied."
    )
    play_argparser.add_argument(
        "-m",
        "--main-line",
        action="store_true",
        help=
        "If a variation already exists from this move, add this new variation as the main line rather than a side line."
    )

    @cmd2.with_argparser(play_argparser)  # type: ignore
    def do_play(self, args) -> None:
        """Play a sequence of moves from the current position."""
        for move_text in args.moves:
            try:
                move: chess.Move = self.game_node.board().parse_san(move_text)
            except ValueError:
                self.poutput(f"Error: Illegal move: {move_text}")
                break
            if args.main_line:
                self.game_node = self.game_node.add_main_variation(move)
            else:
                self.game_node = self.game_node.add_variation(move)
        if args.comment is not None:
            self.game_node.comment = args.comment

    moves_argparser = cmd2.Cmd2ArgumentParser()
    _moves_before_group = moves_argparser.add_mutually_exclusive_group()
    _moves_before_group.add_argument(
        "-s",
        "--start",
        action="store_true",
        help=
        "Print moves from the start of the game, this is the default if no other constraint is specified."
    )
    _moves_before_group.add_argument(
        "-f",
        "--from",
        dest="_from",
        help="Print moves from the given move number, defaults to current move."
    )
    _moves_after_group = moves_argparser.add_mutually_exclusive_group()
    _moves_after_group.add_argument(
        "-e",
        "--end",
        action="store_true",
        help=
        "Print moves to the end of the game, this is the default if no other constraint is specified."
    )
    _moves_after_group.add_argument(
        "-t",
        "--to",
        help="Print moves to the given move number, defaults to current move.")

    @cmd2.with_argparser(moves_argparser)  # type: ignore
    def do_moves(self, args) -> None:
        """Print the moves in the game.
        Print all moves by default, but if some constraint is specified, print only those moves.
        """

        current_board: chess.Board = self.game_node.board()
        current_fullmove: Fullmove = Fullmove.from_board(current_board)

        # If No constraint is specified, print all moves.
        if not (args.start or args.end or args._from or args.to):
            args.start = True
            args.end = True

        if args.start:
            first_board: chess.Board = current_board.root()
            move_list = current_board.move_stack
        elif args._from:
            try:
                from_move: Fullmove = Fullmove.parse(args._from)
            except ValueError:
                self.poutput(f"Error: Unable to parse fullmove: {args._from}")
                return
            if from_move.move_number <= current_fullmove.move_number:
                start_board = current_board.root()
            else:
                start_board = current_board


def run():
    sys.exit(ChessCli().cmdloop())
