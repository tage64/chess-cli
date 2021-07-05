from collections import deque
import sys
from typing import NamedTuple, Optional

import chess
import chess.pgn
import cmd2

__version__ = '0.1.0'


class MoveNumber(NamedTuple):
    """ A move number is a fullmove number and the color that made the move.
    E.G. "1." would be move number 1 and color white while "10..." would be move number 10 and color black.
    """

    move_number: int
    color: chess.Color

    @staticmethod
    def last(node: chess.pgn.ChildNode):
        """ Get the move number from the previously executed move.
        """
        board = node.board()
        return MoveNumber(board.fullmove_number, board.turn).previous()

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
        return MoveNumber(number, color)

    def previous(self):
        " Get previous move. "
        if self.color == chess.WHITE:
            return MoveNumber(self.move_number - 1, chess.BLACK)
        else:
            return MoveNumber(self.move_number, chess.WHITE)

    def next(self):
        " Get next move. "
        if self.color == chess.WHITE:
            return MoveNumber(self.move_number, chess.BLACK)
        else:
            return MoveNumber(self.move_number + 1, chess.WHITE)

    def __str__(self) -> str:
        return str(
            self.move_number) + ("." if self.color == chess.WHITE else "...")

    def __lt__(self, other) -> bool:
        return self.move_number < other.move_number or self.move_number == other.move_number and self.color == chess.WHITE and other.color == chess.BLACK

    def __gt__(self, other) -> bool:
        return self.move_number > other.move_number or self.move_number == other.move_number and self.color == chess.BLACK and other.color == chess.WHITE

    def __le__(self, other) -> bool:
        return self.move_number < other.move_number or self.move_number == other.move_number and (
            self.color == chess.WHITE or other.color == chess.BLACK)

    def __ge__(self, other) -> bool:
        return self.move_number > other.move_number or self.move_number == other.move_number and (
            self.color == chess.BLACK or other.color == chess.WHITE)


class ChessCli(cmd2.Cmd):
    """A repl to edit and analyse chess games. """
    def __init__(self, file_name: Optional[str] = None):
        # Set cmd shortcuts
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts, include_py=True)
        self.self_in_py = True

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
            assert isinstance(self.game_node, chess.pgn.ChildNode)
            self.prompt = f"{MoveNumber.last(self.game_node)} {self.game_node.san()}: "
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

        # If No constraint is specified, print all moves.
        if not (args.start or args.end or args._from or args.to):
            args.start = True
            args.end = True

        _start_node = self.game_node.game().next()
        if _start_node is not None:
            start_node: chess.pgn.ChildNode = _start_node
        else:
            # The game doesn't contains any moves.
            return
        if args.start:
            node: chess.pgn.ChildNode = start_node
        elif args._from:
            try:
                from_move: MoveNumber = MoveNumber.parse(args._from)
            except ValueError:
                self.poutput(
                    f"Error: Unable to parse move number: {args._from}")
                return
            node = start_node
            while from_move > MoveNumber.last(node) and not node.is_end():
                node = node.next()  # type: ignore
        else:
            node = self.game_node if isinstance(
                self.game_node, chess.pgn.ChildNode) else start_node

        if args.to:
            try:
                to_move: Optional[MoveNumber] = MoveNumber.parse(args._from)
            except ValueError:
                self.poutput(f"Error: Unable to parse move number: {args.to}")
                return
        else:
            to_move = None

        moves_per_line: int = 6
        lines: list[str] = []
        moves_at_last_line: int = 0
        while not node.is_end():
            if to_move and to_move > MoveNumber.last(node):
                break
            if moves_at_last_line >= moves_per_line:
                lines.append("")
                moves_at_last_line = 0

            move_number: MoveNumber = MoveNumber.last(node)
            if move_number.color == chess.WHITE or lines == []:
                if lines == []:
                    lines.append("")
                lines[-1] += str(move_number) + " "
            lines[-1] += node.san() + " "
            if move_number.color == chess.BLACK:
                moves_at_last_line += 1
            node = node.next()  # type: ignore
        for line in lines:
            self.poutput(line)

    goto_argparser = cmd2.Cmd2ArgumentParser()
    goto_argparser.add_argument("move_number",
                                nargs="?",
                                help="A move number like 10. or 9...")
    goto_argparser.add_argument("move",
                                nargs="?",
                                help="A move like e4 or Nxd5+.")
    goto_argparser.add_argument("-s",
                                "--start",
                                action="store_true",
                                help="Go to the start of the game.")
    _goto_sidelines_group = goto_argparser.add_mutually_exclusive_group()
    _goto_sidelines_group.add_argument(
        "-r",
        "--recurse-sidelines",
        action="store_true",
        help=
        "Make a bredth first search BFS into sidelines. Only works forwards in the game."
    )
    _goto_sidelines_group.add_argument(
        "-n",
        "--no-sidelines",
        action="store_true",
        help="Don't search any sidelines at all.")
    _goto_direction_group = goto_argparser.add_mutually_exclusive_group()
    _goto_direction_group.add_argument("-b",
                                       "--backwards-only",
                                       action="store_true",
                                       help="Only search the game backwards.")
    _goto_direction_group.add_argument("-f",
                                       "--forwards-only",
                                       action="store_true",
                                       help="Only search the game forwards.")

    @cmd2.with_argparser(goto_argparser)  # type: ignore
    def do_goto(self, args) -> None:
        """Goto a move specified by a move number or a move in standard algibraic notation.
        If a move number is specified, it will follow the main line to that move if it does exist. If a move like "e4" or "Nxd5+" is specified as well, it will go to the specific move number and search between variations at that level for the specified move. If only a move but not a move number and no other constraints are given, it'll first search sidelines at the current move, then follow the mainline and check if any move or sideline matches, but not recurse into sidelines. Lastly, it'll search backwards in the game.
        """
        if args.start:
            self.game_node = self.game_node.game()
            return

        # This hack is needed because argparse isn't smart enough to understand that it should skip to the next argument if the parsing of an optional argument failes.
        if args.move_number is not None:
            try:
                args.move_number = MoveNumber.parse(args.move_number)
            except ValueError:
                if args.move is not None:
                    self.poutput(
                        "Error: Unable to parse move number: {args.move_number}"
                    )
                    return
                else:
                    args.move = args.move_number
                    args.move_number = None

        def check_move(node: chess.pgn.ChildNode) -> bool:
            if args.move is not None:
                try:
                    if not node.move == node.parent.board().push_san(
                            args.move):
                        return False
                except ValueError:
                    return False
            return True

        if isinstance(self.game_node, chess.pgn.ChildNode):
            current_node: chess.pgn.ChildNode = self.game_node
        else:
            next = self.game_node.next()
            if next is not None:
                current_node = next
            else:
                self.poutput("Error: No moves in the game.")
                return
        search_queue: deque[chess.pgn.ChildNode] = deque()
        search_queue.append(current_node)
        if not args.no_sidelines:
            sidelines = current_node.parent.variations
            search_queue.extend(
                (x for x in sidelines if not x == current_node))
        if not args.backwards_only and (
                args.move_number is None
                or args.move_number >= MoveNumber.last(current_node)):
            while search_queue:
                node: chess.pgn.ChildNode = search_queue.popleft()
                if args.move_number is not None:
                    if args.move_number == MoveNumber.last(node):
                        if check_move(node):
                            self.game_node = node
                            return
                    elif args.move_number < MoveNumber.last(node):
                        break
                else:
                    if check_move(node):
                        self.game_node = node
                        return
                if args.recurse_sidelines or node.is_main_variation():
                    if not args.no_sidelines:
                        search_queue.extend(node.variations)
                    else:
                        next = node.next()
                        if next is not None:
                            search_queue.append(next)
            if args.move_number is not None and args.move_number > MoveNumber.last(
                    node):
                self.poutput(
                    "Error: The move number was beyond the end of the game.")
                return
        if not args.forwards_only and (
                args.move_number is None
                or args.move_number < MoveNumber.last(current_node)):
            node = current_node
            while isinstance(node.parent, chess.pgn.ChildNode):
                node = node.parent
                if args.move_number is not None:
                    if args.move_number == MoveNumber.last(node):
                        if check_move(node):
                            self.game_node = node
                            return
                    elif args.move_number > MoveNumber.last(node):
                        break
                else:
                    if check_move(node):
                        self.game_node = node
                        return
            if args.move_number is not None and args.move_number < MoveNumber.last(
                    node):
                self.poutput(
                    "Error: The move number was beyond the beginning of the game."
                )
                return
        self.poutput("Error: Couldn't find the move.")


def run():
    sys.exit(ChessCli().cmdloop())
