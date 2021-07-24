from . import nags

from collections import deque, defaultdict
import copy
import datetime
import os
import re
import sys
import threading
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Union

import appdirs  # type: ignore
import chess
import chess.engine
import chess.pgn
import chess.svg
import cmd2
import toml  # type: ignore

__version__ = '0.1.0'


class MoveNumber(NamedTuple):
    """ A move number is a fullmove number and the color that made the move.
    E.G. "1." would be move number 1 and color white while "10..." would be move number 10 and color black.
    """

    move_number: int
    color: chess.Color

    @staticmethod
    def last(pos: Union[chess.Board, chess.pgn.ChildNode]):
        """ Get the move number from the previously executed move.
        """
        if isinstance(pos, chess.pgn.ChildNode):
            board = pos.board()
        else:
            board = pos
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


class EngineConf(NamedTuple):
    " Configuration for an engine. "
    path: str  # Path of engine executable.
    protocol: str  # "uci" or "xboard"
    options: dict[str, Optional[Union[str, int, bool]]] = {}
    fullname: Optional[str] = None  # Full name of the engine from id.name.


class Analysis(NamedTuple):
    " Information about analysis."

    result: chess.engine.SimpleAnalysisResult
    engine: str
    board: chess.Board
    san: Optional[str]


def move_str(game_node: chess.pgn.GameNode,
             include_move_number: bool = True) -> str:
    res: str = ""
    if not isinstance(game_node, chess.pgn.ChildNode):
        res += "start"
    else:
        if not game_node.is_main_variation():
            res += "<"
        if include_move_number:
            res += str(MoveNumber.last(game_node)) + " "
        if game_node.starting_comment:
            res += "-"
        res += game_node.san()
        if game_node.nags:
            res += str([nags.ascii_glyph(nag) for nag in game_node.nags])
        if game_node.comment:
            res += "-"
        if not game_node.parent.variations[-1] == game_node:
            res += ">"
    return res


def score_str(score: chess.engine.Score) -> str:
    if score == chess.engine.MateGiven:
        return "mate, "
    if score.is_mate():
        mate: int = score.mate()  # type: ignore
        if 0 < mate:
            return f"Mate in {mate}, "
        return f"Mated in {-mate}, "
    cp: int = score.score()  # type: ignore
    if cp > 0:
        return f"+{cp/100} pawns, "
    return f"{cp/100} pawns, "


class ChessCli(cmd2.Cmd):
    """A repl to edit and analyse chess games. """
    def __init__(self,
                 file_name: Optional[str] = None,
                 config_file: Optional[str] = None):
        # Set cmd shortcuts
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts, include_py=True)
        self.self_in_py = True
        self.register_postloop_hook(self.close_engines)

        self.config_file: str = (config_file or os.path.join(
            appdirs.user_config_dir("chess-cli"), "config.toml"))

        self.engine_confs: dict[str, EngineConf] = {}
        self.loaded_engines: dict[str, chess.engine.SimpleEngine] = {}
        self.selected_engine: Optional[str] = None
        self.running_analysis: dict[str, Analysis] = dict()
        self.analysis: list[Analysis] = []
        self.analysis_by_node: defaultdict[chess.pgn.GameNode,
                                           list[Analysis]] = defaultdict(list)

        if os.path.exists(self.config_file):
            with open(self.config_file) as f:
                try:
                    items = toml.load(f)
                    engine_confs = items["engine-configurations"]
                    if not isinstance(engine_confs, dict):
                        raise Exception(
                            "'engine-configurations' must be a list.")
                    self.engine_confs = {
                        name: EngineConf(**values)
                        for (name, values) in engine_confs.items()
                    }
                except Exception as ex:
                    self.poutput(
                        f"Error while processing config file at '{self.config_file}': {repr(ex)}"
                    )
                    self.poutput(
                        "You might want to go to the file and inspect it yourself. Or just delete it to get rid of the problem."
                    )
                    self.poutput(
                        "This session will be started with an empty configuration."
                    )
        elif config_file is not None:
            self.poutput(
                f"Warning: Couldn't find config file at '{config_file}'.")
            self.poutput(
                "This session will be started with an empty configuration.")

        # Read the pgn file
        if file_name is not None:
            with open(file_name) as pgn_file:
                res = chess.pgn.read_game(pgn_file)
            if res is None:
                self.poutput(f"Error: Couldn't find any game in {file_name}")
                game_node: chess.pgn.GameNode = chess.pgn.Game()
            else:
                game_node = res
        else:
            game_node = chess.pgn.Game()
        self.games: dict[str, chess.pgn.GameNode] = {"main": game_node}
        self.file_names: dict[str, str] = ({} if file_name is None else {
            "main": file_name
        })
        self.current_game: str = "main"

        self.register_postcmd_hook(self.set_prompt)
        self.set_prompt(None)  # type: ignore

    @property
    def game_node(self) -> chess.pgn.GameNode:
        return self.games[self.current_game]

    @game_node.setter
    def game_node(self, value: chess.pgn.GameNode) -> None:
        self.games[self.current_game] = value

    def close_engines(self) -> None:
        for _, analysis in self.running_analysis.items():
            analysis.result.stop()
        self.running_analysis.clear()
        for engine in self.loaded_engines.values():
            engine.quit()
        self.loaded_engines.clear()

    def set_prompt(
        self, postcommand_data: cmd2.plugin.PostcommandData
    ) -> cmd2.plugin.PostcommandData:
        self.prompt = f"{move_str(self.game_node)}: "
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

    show_argparser = cmd2.Cmd2ArgumentParser()
    show_argparser.add_argument("what",
                                choices=[
                                    "comment", "nags", "evaluation", "arrows",
                                    "clock", "starting-comment", "all"
                                ],
                                default="all",
                                nargs="?",
                                help="What to show.")

    @cmd2.with_argparser(show_argparser)  # type: ignore
    def do_show(self, args) -> None:
        " Show various things like comments and arrows about the current move. "
        if (isinstance(self.game_node, chess.pgn.ChildNode)
                and self.game_node.starting_comment
                and (args.what == "starting_comment" or args.what == "all")):
            self.poutput(self.game_node.starting_comment)
            self.poutput(
                f"    {MoveNumber.last(self.game_node)} {self.game_node.san()}"
            )
        if (self.game_node and (args.what == "comment" or args.what == "all")):
            self.poutput(self.game_node.comment)
        if (self.game_node.nags
                and (args.what == "nags" or args.what == "all")):
            for nag in self.game_node.nags:
                text: str = "NAG: " if not args.what == "all" else ""
                text += f"({nags.ascii_glyph(nag)}) {nags.description(nag)}"
                self.poutput(text)
        eval = self.game_node.eval()
        if (eval is not None
                and (args.what == "evaluation" or args.what == "all")):
            text = "Evaluation: " if not args.what == "all" else ""
            text += score_str(eval.relative)
            if self.game_node.eval_depth() is not None:
                text += f", Depth: {self.game_node.eval_depth()}"
            self.poutput(text)
        if (self.game_node.arrows()
                and (args.what == "arrows" or args.what == "all")):
            text = "Arows: " if not args.what == "all" else ""
            text += str([
                f"{arrow.color} {arrow.tail}-{arrow.head}"
                for arrow in self.game_node.arrows()
            ])
            self.poutput(text)
        clock = self.game_node.clock()
        if (clock is not None
                and (args.what == "clock" or args.what == "all")):
            text = "Clock: " if not args.what == "all" else ""
            text += str(datetime.timedelta(seconds=clock)).strip("0")
            self.poutput(text)

    set_argparser = cmd2.Cmd2ArgumentParser()
    set_subcmds = set_argparser.add_subparsers(dest="subcmd")
    set_comment_argparser = set_subcmds.add_parser(
        "comment", aliases=["c"], help="Set comment for this move.")
    set_comment_argparser.add_argument("comment", help="The new text.")
    set_comment_argparser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="Append this text to the old comment.")
    set_starting_comment_argparser = set_subcmds.add_parser(
        "starting-comment",
        aliases=["sc"],
        help=
        "Set starting_comment for this move. Only moves that starts a variation can have a starting comment."
    )
    set_starting_comment_argparser.add_argument("comment",
                                                help="The new text.")
    set_starting_comment_argparser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="Append this text to the old comment.")
    set_nag_argparser = set_subcmds.add_parser(
        "nag", help="Set a nag (numeric annotation glyph) on this move.")
    set_nag_argparser.add_argument(
        "nag",
        help=
        "Nag, either a number like '$17' or an ascii glyph like '!' or '?!'.")
    set_nag_argparser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help=
        "Append this nag to the list of existing nags at this move instead of replacing them."
    )
    set_eval_argparser = set_subcmds.add_parser(
        "evaluation",
        aliases=["eval"],
        help="Set an evaluation for this move.")
    set_eval_group = set_eval_argparser.add_mutually_exclusive_group(
        required=True)
    set_eval_group.add_argument(
        "--cp",
        type=int,
        help=
        "Relative score in centi pawns from the player to move's point of view."
    )
    set_eval_group.add_argument(
        "--mate",
        "--mate-in",
        type=int,
        help="The player to move can force mate in the given number of moves.")
    set_eval_group.add_argument(
        "--mated",
        "--mated-in",
        type=int,
        help="The player to move will be mated in the given number of moves.")
    set_eval_argparser.add_argument(
        "-d",
        "--depth",
        type=int,
        help="The depth at which the analysis was made.")
    set_arrow_argparser = set_subcmds.add_parser(
        "arrow", aliases=["arr"], help="Draw an arrow on the board.")
    set_arrow_argparser.add_argument(
        "from",
        type=chess.parse_square,
        dest="_from",
        help="The square from which the arrow is drawn.")
    set_arrow_argparser.add_argument(
        "to",
        type=chess.parse_square,
        help="The square which the arrow is pointing to.")
    set_arrow_argparser.add_argument(
        "color",
        choices=["red", "r", "yellow", "y", "green", "g", "blue", "b"],
        default="green",
        nargs="?",
        help=
        "Color of the arrow. Red/yellow/green/blue can be abbreviated as r/y/g/b."
    )
    set_clock_argparser = set_subcmds.add_parser(
        "clock",
        help="Set the remaining time for the player making this move.")
    set_clock_argparser.add_argument("time", help="Remaining time.")

    @cmd2.with_argparser(set_argparser)  # type: ignore
    def do_set(self, args) -> None:
        " Set various things (like comments, nags or arrows) at the current move. "
        if args.subcmd == "comment":
            if args.append and self.game_node.comment:
                self.game_node.comment = " ".join(
                    (self.game_node.comment, args.comment))
            else:
                self.game_node.comment = args.comment
        elif args.subcmd == "starting_comment":
            if not self.game_node.starts_variation():
                self.poutput(
                    "Error: Only moves that starts a variation can have a starting comment and this move doesn't start a variation.\nYour attempt to set a starting comment for this move was a complete failure!"
                )
            if args.append and self.game_node.starting_comment:
                self.game_node.starting_comment = " ".join(
                    (self.game_node.starting_comment, args.comment))
            else:
                self.game_node.starting_comment = args.comment
        elif args.subcmd == "nag":
            try:
                nag: int = nags.parse_nag(args.nag)
            except ValueError as e:
                self.poutput(f"Error: invalid NAG {args.nag}: {e}")
                return
            if args.append:
                self.game_node.nags.add(nag)
            else:
                self.game_node.nags = {nag}
            self.poutput(
                f"Set NAG ({nags.ascii_glyph(nag)}): {nags.description(nag)}.")
        elif args.subcmd == "eval":
            if args.mate_in is not None:
                score: chess.engine.Score = chess.engine.Mate(args.mate_in)
            elif args.mated_in is not None:
                score = chess.engine.Mate(-args.mated_in)
            else:
                score = chess.engine.Cp(args.cp)
            self.game_node.set_eval(
                chess.engine.PovScore(score, self.game_node.turn()),
                args.depth)
        elif args.subcmd == "arrow":
            color_abbreviations: dict[str, str] = {
                "g": "green",
                "y": "yellow",
                "r": "red",
                "b": "blue"
            }
            if args.color in color_abbreviations:
                color = color_abbreviations[args.color]
            else:
                color = args.color
            self.game_node.set_arrows(
                self.game_node.arrows() +
                [chess.svg.Arrow(args._from, args.to, color=color)])
        elif args.subcmd == "clock":
            time_parsed = re.fullmatch("(\d+)(:(\d+))?(:(\d+))?([.,](\d+))?",
                                       args.time)
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

    rm_argparser = cmd2.Cmd2ArgumentParser()
    rm_subcmds = rm_argparser.add_subparsers(dest="subcmd")
    rm_subcmds.add_parser("comment",
                          aliases=["c"],
                          help="Remove the comment at this move.")
    rm_subcmds.add_parser("starting-comment",
                          aliases=["sc"],
                          help="Remove the starting comment at this move.")
    rm_subcmds.add_parser("nags", help="Remove all NAGs at this move.")
    rm_nag_argparser = rm_subcmds.add_parser(
        "nag", help="Remove a specific NAG at this move.")
    rm_nag_argparser.add_argument("which",
                                  type=nags.parse_nag,
                                  help="A NAG to remove. Like '$16' or '??'.")
    rm_subcmds.add_parser(
        "evaluation",
        aliases=["eval"],
        help="Remove the evaluation annotation at this move if any.")
    rm_arrows_argparser = rm_subcmds.add_parser(
        "arrows", help="Remove arrows at this move.")
    rm_arrows_argparser.add_argument(
        "-f",
        "--from",
        dest="_from",
        type=chess.parse_square,
        help="Remove arrows starting at this square.")
    rm_arrows_argparser.add_argument(
        "-t",
        "--to",
        type=chess.parse_square,
        help="Remove arrows ending at this square.")
    rm_arrows_argparser.add_argument(
        "-c",
        "--color",
        choices=["red", "r", "yellow", "y", "green", "g", "blue", "b"],
        help=
        "Remove only arrows with this color. Red/yellow/green/blue can be abbreviated as r/y/g/b."
    )
    rm_subcmds.add_parser(
        "clock", help="Remove the clock annotation at this move if any.")

    @cmd2.with_argparser(rm_argparser)  # type: ignore
    def do_rm(self, args) -> None:
        " Remove various things at the current move (like the comment or arrows). "
        if args.subcmd == "comment":
            self.game_node.comment = ""
        elif args.subcmd == "starting_comment":
            self.game_node.starting_comment = ""
        elif args.subcmd == "nags":
            self.game_node.nags = set()
        elif args.subcmd == "eval":
            self.game_node.set_eval(None)
        elif args.subcmd == "clock":
            self.game_node.set_clock(None)
        elif args.subcmd == "nag":
            try:
                self.game_node.nags.remove(args.nag)
            except KeyError:
                self.poutput(
                    f"Error: The NAG {nags.ascii_glyph(args.nag)} was not set for this move so it couldn't be removed."
                )
        elif args.subcmd == "arrows":
            color_abbreviations: dict[str, str] = {
                "g": "green",
                "y": "yellow",
                "r": "red",
                "b": "blue"
            }
            if args.color is None:
                color: Optional[str] = None
            elif args.color in color_abbreviations:
                color = color_abbreviations[args.color]
            else:
                color = args.color
            self.game_node.set_arrows(
                (arr for arr in self.game_node.arrows()
                 if args._from is None or not args._from == arr.tail
                 if args.to is None or not args.to == arr.head
                 if color is None or not color == arr.color))

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
        while node is not None:
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

    delete_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(delete_argparser)  # type: ignore
    def do_delete(self, _args) -> None:
        " Delete the current move. "
        if isinstance(self.game_node, chess.pgn.ChildNode):
            parent = self.game_node.parent
            new_node = parent
            for (i, node) in enumerate(parent.variations):
                if node is self.game_node:
                    if i + 1 < len(parent.variations):
                        self.game_node = parent.variations[i + 1]
                    elif i > 0:
                        self.game_node = parent.variations[i - 1]
                    else:
                        self.game_node = parent
                    parent.variations = parent.variations[:
                                                          i] + parent.variations[
                                                              i + 1:]

    engine_argparser = cmd2.Cmd2ArgumentParser()
    engine_argparser.add_argument(
        "-s",
        "--select",
        help=
        "Select a different engine for this command. The pre selected engine can be altered with the command `engine select <name>`."
    )
    engine_subcmds = engine_argparser.add_subparsers(dest="subcmd")
    engine_ls_argparser = engine_subcmds.add_parser(
        "ls", help="List loaded chess engines.")
    engine_ls_argparser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Display more information about the engines.")
    engine_load_argparser = engine_subcmds.add_parser(
        "load", help="Load a chess engine.")
    engine_load_argparser.add_argument(
        "name",
        help=
        "Name of the engine. List availlable engines with the command `engine ls`"
    )
    engine_load_argparser.add_argument(
        "--as",
        dest="load_as",
        help=
        "Load the engine with a different name. Useful if you want to have multiple instances of an engine running at the same time."
    )
    engine_import_argparser = engine_subcmds.add_parser(
        "import", help="Import a chess engine.")
    engine_import_argparser.add_argument("path",
                                         help="Path to engine executable.")
    engine_import_argparser.add_argument(
        "name",
        help="A short name for the engine, (PRO TIP: avoid spaces in the name)."
    )
    engine_import_argparser.add_argument("-p",
                                         "--protocol",
                                         choices=["uci", "xboard"],
                                         default="uci",
                                         help="Type of engine protocol.")
    engine_close_argparser = engine_subcmds.add_parser(
        "close", help="Close a chess engine.")
    engine_select_argparser = engine_subcmds.add_parser(
        "select",
        help=
        "Select a loaded engine. The selected engine will be default commands like `engine analyse` or `engine config`."
    )
    engine_select_argparser.add_argument("engine",
                                         help="Name of engine to select.")
    engine_config_argparser = engine_subcmds.add_parser(
        "config",
        help=
        "Set values for or get current values of different engine specific parameters."
    )
    engine_config_subcmds = engine_config_argparser.add_subparsers(
        dest="config_subcmd")
    engine_config_get_argparser = engine_config_subcmds.add_parser(
        "get", help="Get the value of an option for the selected engine.")
    engine_config_get_argparser.add_argument("name",
                                             help="Name of the option.")
    engine_config_ls_argparser = engine_config_subcmds.add_parser(
        "ls",
        aliases=["list"],
        help=
        "List availlable options and their current values for the selected engine."
    )
    engine_config_ls_argparser.add_argument(
        "-r",
        "--regex",
        help="Filter option names by a case insensitive regular expression.")
    engine_config_ls_argparser.add_argument(
        "-t",
        "--type",
        choices=["checkbox", "combobox"
                 "integer", "text", "button"],
        nargs="+",
        help="Filter options by the given type.")
    engine_config_ls_configured_group = engine_config_ls_argparser.add_mutually_exclusive_group(
    )
    engine_config_ls_configured_group.add_argument(
        "--configured",
        action="store_true",
        help="Only list options that are already configured in some way.")
    engine_config_ls_configured_group.add_argument(
        "--not-configured",
        action="store_true",
        help="Only list options that are not configured.")
    engine_config_ls_argparser.add_argument(
        "--include-auto",
        "--include-automatically-managed",
        action="store_true",
        help=
        "By default, some options like MultiPV or Ponder are managed automatically. There is no reason to change them so they are hidden by default. This options makes them vissable."
    )
    engine_config_set_argparser = engine_config_subcmds.add_parser(
        "set", help="Set a value of an option for the selected engine.")
    engine_config_set_argparser.add_argument("name",
                                             help="Name of the option to set.")
    engine_config_set_argparser.add_argument(
        "value",
        help=
        "The new value. Use true/check or false/uncheck to set a checkbox. Buttons can only be set to 'trigger-on-startup', but note that you must use the `engine config trigger` command to trigger it right now."
    )
    engine_config_set_argparser.add_argument(
        "-t",
        "--temporary",
        action="store_true",
        help=
        "Set the value in the running engine but don't store it in the engine configuration. It'll not be set if you save this configuration and load the engine again."
    )
    engine_config_unset_argparser = engine_config_subcmds.add_parser(
        "unset",
        help=
        "Change an option back to its default value and remove it from the configuration."
    )
    engine_config_unset_argparser.add_argument(
        "name", help="Name of the option to unset.")
    engine_config_unset_argparser.add_argument(
        "-t",
        "--temporary",
        action="store_true",
        help=
        "Unset the value in the running engine but keep it in the engine configuration. It'll still be set if you save this configuration and load the engine again."
    )
    engine_config_trigger_argparser = engine_config_subcmds.add_parser(
        "trigger", help="Trigger an option of type button.")
    engine_config_trigger_argparser.add_argument(
        "name", help="Name of the option to trigger.")
    engine_config_save_argparser = engine_config_subcmds.add_parser(
        "save", help="Save the current configuration.")

    @cmd2.with_argparser(engine_argparser)  # type: ignore
    def do_engine(self, args: Any) -> None:
        if args.subcmd == "ls":
            self.engine_ls(args)
        elif args.subcmd == "import":
            self.engine_import(args)
        elif args.subcmd == "load":
            self.engine_load(args)
        elif args.subcmd == "select":
            self.engine_select(args)
        else:
            if args.select is not None:
                if args.select not in self.loaded_engines:
                    if args.select in self.engine_confs:
                        self.poutput(
                            f"Error: {args.select} is not loaded. You can try to load it by running `engine load {args.select}`."
                        )
                    else:
                        self.poutput(
                            "f Error: There is no engine named {args.select}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                        )
                    return
                selected_engine: str = args.select
            elif self.selected_engine is not None:
                selected_engine = self.selected_engine
            else:
                self.poutput(
                    "Error: No engines loaded. Consider loading one with the `engine load` command."
                )
                return
            if args.subcmd == "close":
                self.engine_close(selected_engine, args)
            if args.subcmd == "config":
                if args.config_subcmd == "get":
                    self.engine_config_get(selected_engine, args)
                if args.config_subcmd == "ls":
                    self.engine_config_ls(selected_engine, args)
                if args.config_subcmd == "set":
                    self.engine_config_set(selected_engine, args)
                if args.config_subcmd == "unset":
                    self.engine_config_unset(selected_engine, args)
                if args.config_subcmd == "trigger":
                    self.engine_config_trigger(selected_engine, args)
                if args.config_subcmd == "save":
                    self.engine_config_save(selected_engine, args)

    def engine_select(self, args) -> None:
        if args.engine not in self.loaded_engines:
            if args.engine in self.engine_confs:
                self.poutput(
                    f"Error: {args.engine} is not loaded. You can try to load it by running `engine load {args.engine}`."
                )
            else:
                self.poutput(
                    "f Error: There is no engine named {args.engine}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                )
            return
        self.selected_engine = args.engine

    def show_engine(self, name: str, verbose: bool = False) -> None:
        conf: EngineConf = self.engine_confs[name]
        if name == self.selected_engine:
            show_str: str = ">"
        else:
            show_str = " "
        show_str += name
        if conf.fullname is not None:
            show_str += ": " + conf.fullname
        if name in self.loaded_engines:
            show_str += ", (loaded)"
        else:
            show_str += ", (not loaded)"
        if name == self.selected_engine:
            show_str += ", (selected)"
        self.poutput(show_str)
        if verbose:
            engine: chess.engine.SimpleEngine = self.loaded_engines[name]
            for key, val in engine.id.items():
                if not key == "name":
                    self.poutput(f"   {key}: {val}")

    def engine_ls(self, args) -> None:
        for name in self.engine_confs.keys():
            self.show_engine(name, verbose=args.verbose)

    def load_engine(self, name: str) -> None:
        engine_conf: EngineConf = self.engine_confs[name]
        try:
            if engine_conf.protocol == "uci":
                engine = chess.engine.SimpleEngine.popen_uci(engine_conf.path)
            elif engine_conf.protocol == "xboard":
                engine = chess.engine.SimpleEngine.popen_xboard(
                    engine_conf.path)
        except chess.engine.EngineError as e:
            self.poutput(
                f"Engine Terminated Error: The engine {engine_conf.path} didn't behaved as it should. Either it is broken, or this program containes a bug. It might also be that you've specified wrong path to the engine executable."
            )
            raise e
        except chess.engine.EngineTerminatedError as e:
            self.poutput(
                f"Engine Terminated Error: The engine {engine_conf.path} terminated unexpectedly. Either the engine is broken or you've specified wrong path to the executable."
            )
            raise e
        except FileNotFoundError as e:
            self.poutput(
                f"Error: Couldn't find the engine executable {engine_conf.path}: {e}"
            )
            raise e
        self.loaded_engines[name] = engine
        self.selected_engine = name
        self.engine_confs[name] = engine_conf._replace(
            fullname=engine.id.get("name"))
        self.show_engine(name, verbose=True)

    def engine_load(self, args) -> None:
        try:
            if args.name not in self.engine_confs:
                self.poutput(
                    f"Error: There is no engine named {args.name}. Consider importing one with `engine import`."
                )
                return
            if args.load_as is not None:
                if args.load_as in self.engine_confs:
                    self.poutput(
                        f"Error: There is already an engine named {args.load_as}."
                    )
                    return
                engine_conf = self.engine_confs[args.name]
                self.engine_confs[args.load_as] = engine_conf
                self.load_engine(args.load_as)
            elif args.name in self.loaded_engines:
                self.poutput(
                    f"Error: An engine named {args.name} is already loaded. If you want to run multiple instances of a given engine, consider to load it as another name like `engine load <name> --as <name2>`"
                )
                return
            else:
                self.load_engine(args.name)
            self.poutput(f"Successfully loaded and selected {args.name}.")
        except FileNotFoundError as e:
            self.poutput(
                "Perhaps the executable has been moved or deleted, or you might be in a different folder now than when you configured the engine."
            )
            self.poutput(
                "You should probably locate the engine's executable (something like stockfish.exe) and update the engine configuration with the `engine config` command if necessary."
            )
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            self.poutput(f"Loading of {args.name} failed.")

    def engine_import(self, args) -> None:
        if args.name in self.engine_confs:
            self.poutput(
                f"Error: The name {args.name} is already in use, please pick another name or consider removing or updating the existing configuration with the `engine config` command."
            )
            return
        engine_conf: EngineConf = EngineConf(path=args.path,
                                             protocol=args.protocol)
        self.engine_confs[args.name] = engine_conf
        try:
            self.load_engine(args.name)
            self.poutput(
                f"Successfully imported, loaded and selected {args.name}.")
        except (FileNotFoundError, chess.engine.EngineError,
                chess.engine.EngineTerminatedError):
            del self.engine_confs[args.name]
            self.poutput(f"Importing of the engine {engine_conf.path} failed.")

    def engine_close(self, selected_engine: str, _args) -> None:
        engine = self.loaded_engines[selected_engine]
        engine.close()
        del self.loaded_engines[selected_engine]
        self.poutput(f"Successfully closed {selected_engine}.")
        if self.selected_engine == selected_engine:
            if self.loaded_engines:
                self.selected_engine = list(self.loaded_engines)[-1]
                self.poutput(
                    f"Changed selected engine to {self.selected_engine}.")
            else:
                self.selected_engine = None

    def show_engine_option(self, engine: str, name: str) -> None:
        opt: chess.engine.Option = self.loaded_engines[engine].options[name]
        configured_val: Optional[Union[
            str, int, bool]] = self.engine_confs[engine].options.get(name)
        val: Optional[Union[str, int, bool]] = configured_val or opt.default

        show_str: str = name
        if val is not None:
            if opt.type == "checkbox":
                if val:
                    show_str += " [X]"
                else:
                    show_str += " [ ]"
            else:
                show_str += " = " + repr(val)
        if configured_val is not None and opt.default is not None:
            show_str += f": Default: {repr(opt.default)}, "
        else:
            show_str += " (default): "
        if opt.var:
            show_str += f"Alternatives: {repr(opt.var)}, "
        if opt.min is not None:
            show_str += f"Min: {repr(opt.min)}, "
        if opt.max is not None:
            show_str += f"Max: {repr(opt.max)}, "
        show_str += "Type: "
        if opt.type == "check":
            show_str += "checkbox"
        elif opt.type == "combo":
            show_str += "combobox"
        elif opt.type == "spin":
            show_str += "integer"
        elif opt.type == "string":
            show_str += "text"
        elif opt.type == "file":
            show_str += "text (file path)"
        elif opt.type == "path":
            show_str += "text (directory path)"
        elif opt.type == "button":
            show_str += "button"
        elif opt.type == "reset":
            show_str += "button (reset)"
        elif opt.type == "save":
            show_str += "button (save)"
        else:
            assert False, f"Unsupported option type: {opt.type}."

        if configured_val is not None:
            show_str += ", (Configured)"
        if opt.is_managed():
            show_str += ", (Managed automatically)"

        self.poutput(show_str)

    def engine_config_get(self, engine: str, args) -> None:
        options: Mapping[
            str, chess.engine.Option] = self.loaded_engines[engine].options
        if args.name in options:
            self.show_engine_option(engine, args.name)
        else:
            self.poutput(
                f"Error: {engine} has no option named {args.name}. Consider listing availlable options with `engine configure ls`."
            )

    def engine_config_ls(self, engine: str, args) -> None:
        conf: EngineConf = self.engine_confs[engine]
        for name, opt in self.loaded_engines[engine].options.items():
            if ((args.configured and name not in conf.options)
                    or (args.not_configured and name in conf.options)):
                continue
            if (opt.is_managed() and not args.include_auto
                    and name not in conf.options):
                continue
            if args.regex:
                try:
                    pattern: re.Pattern = re.compile(args.regex)
                except re.error as e:
                    self.poutput(
                        f"Error: Invalid regular expression \"{args.regex}\": {e}"
                    )
                    return
                if not pattern.fullmatch(name):
                    continue
            if (args.type
                    and ((opt.type == "check" and not "checkbox" in args.type)
                         or opt.type == "combo" and not "combobox" in args.type
                         or opt.type == "spin" and not "integer" in args.type
                         or opt.type in ["button", "reset", "save"]
                         and not "button" in args.type
                         or opt.type in ["string", "file", "path"]
                         and not "string" in args.type)):
                continue
            self.show_engine_option(engine, name)

    def engine_config_set(self, engine: str, args) -> None:
        options: Mapping[
            str, chess.engine.Option] = self.loaded_engines[engine].options
        conf: EngineConf = self.engine_confs[engine]
        if args.name not in options:
            self.poutput(
                f"Error: {args.name} is not a name for an option for {engine}. You can list availlable options with `engine config ls`."
            )
            return
        option: chess.engine.Option = options[args.name]
        if option.type in ["string", "file", "path"]:
            value: Union[str, int, bool] = args.value
        elif option.type == "combo":
            if not option.var:
                self.poutput(
                    f"There are no valid alternatives for {args.name}, so you cannot set it to any value. It's strange I know, but I'm probably not the engine's author so I can't do much about it."
                )
                return
            if args.value not in option.var:
                self.poutput(
                    f"Error: {args.value} is not a valid alternative for the combobox {args.name}. The list of valid options is: {repr(args.var)}."
                )
                return
            value = args.value
        elif option.type == "spin":
            try:
                value = int(args.value)
            except ValueError:
                self.poutput(
                    f"Error: Invalid integer: {args.value}. Note: This option expects an integer and nothing else."
                )
                return
            if option.min is not None and value < option.min:
                self.poutput(
                    f"Error: The minimum value for {args.name} is {option.min}, you specified {value}."
                )
                return
            if option.max is not None and value < option.max:
                self.poutput(
                    f"Error: The maximum value for {args.name} is {option.max}, you specified {value}."
                )
                return
        elif option.type == "check":
            if args.value.lower() in ["true", "check"]:
                value = True
            elif args.value in ["false", "uncheck"]:
                value = False
            else:
                self.poutput(
                    f"Error: {args.name} is a checkbox and can only be set to true/check or false/uncheck, but you set it to {args.value}. Please go ahead and correct your mistake."
                )
                return
        elif option.type in ["button", "reset", "save"]:
            if not args.value.lower() == "trigger-on-startup":
                self.poutput(
                    f"Error: {args.name} is a button and buttons can only be configured to 'trigger-on-startup', (which means what it sounds like). If you want to trigger {args.name}, please go ahead and run `engine config trigger {args.name}` instead. Or you might just have made a typo when you entered this command, if so, go ahead and run `engine config set {args.name} trigger-on-startup`."
                )
                return
            value = "trigger-on-startup"
        else:
            assert False, f"Unsupported option type: {option.type}"
        if not args.temporary:
            conf.options[args.name] = value
        if option.type not in ["button", "reset", "save"]:
            self.loaded_engines[engine].configure({args.name: value})

    def engine_config_unset(self, engine: str, args) -> None:
        options: Mapping[
            str, chess.engine.Option] = self.loaded_engines[engine].options
        if args.name not in options:
            self.poutput(
                f"Error: {args.name} is not a name for an option for {engine}. You can list availlable options with `engine config ls`."
            )
            return
        default = options[args.name].default
        if default is None:
            if args.temporary:
                self.poutput(
                    f"Error: {args.name} has no default value and wasn't changed. Try to set it to a custom value with `engine config set --temporary {args.name} <value>`."
                )
                return
            self.poutput(
                f"Warning: {args.name} has no default value so it's unchanged in the running engine."
            )
        else:
            self.loaded_engines[engine].configure({args.name: default})
            self.poutput(
                f"Successfully changed {args.name} back to its default value: {default}."
            )

        if not args.temporary:
            conf: EngineConf = self.engine_confs[engine]
            conf.options.pop(args.name, None)

    def engine_config_trigger(self, engine: str, args) -> None:
        options: Mapping[
            str, chess.engine.Option] = self.loaded_engines[engine].options
        if args.name not in options:
            self.poutput(
                f"Error: {args.name} is not a name for an option for {engine}. You can list availlable options with `engine config ls`."
            )
            return
        if options[args.name].type not in ["button", "reset", "save"]:
            self.poutput(f"Error: {args.name} is not a button.")
            return
        self.loaded_engines[engine].configure({args.name: None})

    def engine_config_save(self, _engine, _args) -> None:
        os.makedirs(os.path.split(self.config_file)[0], exist_ok=True)
        with open(self.config_file, "w") as f:
            engine_confs = {
                name: conf._asdict()
                for (name, conf) in self.engine_confs.items()
            }
            items = {"engine-configurations": engine_confs}
            toml.dump(items, f)

    analysis_argparser = cmd2.Cmd2ArgumentParser()
    analysis_argparser.add_argument(
        "-s",
        "--select",
        help=
        "Select a different engine for this command. The pre selected engine can be altered with the command `engine select <name>`."
    )
    analysis_subcmds = analysis_argparser.add_subparsers(dest="subcmd")
    analysis_start_argparser = analysis_subcmds.add_parser(
        "start", help="Start to analyse current position.")
    analysis_start_argparser.add_argument("moves",
                                          nargs="*",
                                          help="List of moves to analysis.")
    analysis_start_argparser.add_argument("-n",
                                          "--number-of-moves",
                                          type=int,
                                          default=3,
                                          help="Show the n best moves.")
    analysis_start_argparser.add_argument(
        "--time", type=float, help="Analyse only the given number of seconds.")
    analysis_start_argparser.add_argument(
        "--depth", type=int, help="Analyse until the given depth is reached.")
    analysis_start_argparser.add_argument(
        "--nodes", type=int, help="Search only the given number of nodes.")
    analysis_start_argparser.add_argument(
        "--mate",
        type=int,
        help="Search for a mate in the given number of moves and stop then.")
    analysis_stop_argparser = analysis_subcmds.add_parser(
        "stop", help="Stop analysing.")
    analysis_ls_argparser = analysis_subcmds.add_parser("ls",
                                                        help="List analysis.")
    analysis_ls_argparser.add_argument("-v",
                                       "--verbose",
                                       action="store_true",
                                       help="Print out more info.")
    analysis_ls_group = analysis_ls_argparser.add_mutually_exclusive_group()
    analysis_ls_group.add_argument("-r",
                                   "--running",
                                   action="store_true",
                                   help="List only running analysis.")
    analysis_ls_group.add_argument("-s",
                                   "--stopped",
                                   action="store_true",
                                   help="List only stopped analysis.")
    analysis_show_argparser = analysis_subcmds.add_parser(
        "show", help="Show all analysis performed at the current move.")
    analysis_clone_argparser = analysis_subcmds.add_parser(
        "clone",
        help=
        "Clone the game until this move and add the analysed engine lines as variations. This will create a copy of the original game which can be altered or deleted as wanted. To delete the new game and go back, simply type `game rm`. Or to update the analysis lines in this new game, type `analyse update`."
    )
    analysis_clone_argparser.add_argument(
        "index",
        type=int,
        default=-1,
        nargs="?",
        help=
        "Index of analysis to clone if multiple analysis has been performed at this move. List all analysis with `analysis show`. Defaults to the last analysis."
    )
    analysis_clone_argparser.add_argument(
        "--name",
        help=
        "A name for the new game. An auto generated name will be set if no name is specified."
    )
    analysis_update_argparser = analysis_subcmds.add_parser(
        "update",
        help=
        "Update the variations from this node to match the given lines in an analysis. But be careful, this will delete all existing variations including comments from this move before. If you want to keep them, please clone the game with `analysis clone` first."
    )
    analysis_update_argparser.add_argument(
        "index",
        type=int,
        default=-1,
        nargs="?",
        help=
        "Index of analysis to use for the update. List all analysis with `analysis show`. Defaults to the last analysis."
    )

    @cmd2.with_argparser(analysis_argparser)  # type: ignore
    def do_analysis(self, args) -> None:
        """ Manage analysis. """
        if args.subcmd == "ls":
            self.analysis_ls(args)
        elif args.subcmd == "show":
            self.analysis_show(args)
        elif args.subcmd == "clone":
            self.analysis_clone(args)
        elif args.subcmd == "update":
            self.analysis_update(args)
        else:
            if args.select is not None:
                if args.select not in self.loaded_engines:
                    if args.select in self.engine_confs:
                        self.poutput(
                            f"Error: {args.select} is not loaded. You can try to load it by running `engine load {args.select}`."
                        )
                    else:
                        self.poutput(
                            "f Error: There is no engine named {args.select}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                        )
                    return
                selected_engine: str = args.select
            elif self.selected_engine is not None:
                selected_engine = self.selected_engine
            else:
                self.poutput(
                    "Error: No engines loaded. Consider loading one with the `engine load` command."
                )
                return
            if args.subcmd == "start":
                self.analysis_start(selected_engine, args)
            elif args.subcmd == "stop":
                self.analysis_stop(selected_engine, args)
            else:
                assert False, "Invalid command."

    def analysis_start(self, selected_engine: str, args) -> None:
        if selected_engine in self.running_analysis:
            self.stop_analysis(selected_engine)
        root_moves: list[chess.Move] = []
        board: chess.Board = self.game_node.board()
        for san in args.moves:
            try:
                root_moves.append(board.parse_san(san))
            except ValueError as e:
                self.poutput(
                    f"Error: {san} is not a valid move in this position: {e}")
                return
        analysis: Analysis = Analysis(
            result=self.loaded_engines[selected_engine].analysis(
                board,
                root_moves=root_moves if root_moves != [] else None,
                limit=chess.engine.Limit(time=args.time,
                                         depth=args.depth,
                                         nodes=args.nodes,
                                         mate=args.mate),
                multipv=args.number_of_moves,
                game="this"),
            engine=selected_engine,
            board=board,
            san=(self.game_node.san()
                 if isinstance(self.game_node, chess.pgn.ChildNode) else None),
        )
        self.analysis.append(analysis)
        self.running_analysis[selected_engine] = analysis
        self.analysis_by_node[self.game_node].append(analysis)
        self.poutput("Analysis started successfully.")

    def stop_analysis(self, selected_engine: str) -> None:
        self.running_analysis[selected_engine].result.stop()
        del self.running_analysis[selected_engine]

    def analysis_stop(self, selected_engine: str, args) -> None:
        if selected_engine not in self.running_analysis:
            self.poutput(
                f"Error: {selected_engine} is currently not running any analysis so nothing could be stopped."
            )
            return
        self.stop_analysis(selected_engine)

    def show_analysis(self, analysis: Analysis, verbose: bool = False) -> None:
        show_str: str = analysis.engine + " @ "
        if analysis.san is not None:
            show_str += f"{MoveNumber.last(analysis.board)} {analysis.san}: "
        else:
            show_str += "starting position: "

        def score_and_wdl_str(info: chess.engine.InfoDict) -> str:
            res: str = ""
            if "pv" in info and info["pv"]:
                move_number: MoveNumber = (MoveNumber.last(
                    self.game_node).next() if isinstance(
                        self.game_node, chess.pgn.ChildNode) else MoveNumber(
                            1, chess.WHITE))
                res += f"{move_number} {analysis.board.san(info['pv'][0])}: "
            if "score" in info:
                score: chess.engine.Score = info["score"].relative
                res += score_str(score)
                wdl_from_score: Optional[chess.engine.Wdl] = score.wdl(
                    ply=analysis.board.ply())
            else:
                wdl_from_score = None
            if "wdl" in info:
                wdl: Optional[chess.engine.Wdl] = info["wdl"].relative
            else:
                wdl = wdl_from_score
            if wdl is not None:
                res += f"{round(wdl.expectation() * 100)} %, "
                res += f"{round(wdl.wins * 100 / wdl.total())}% win, "
                res += f"{round(wdl.draws * 100 / wdl.total())}% draw, "
                res += f"{round(wdl.losses * 100 / wdl.total())}% loss, "
            return res

        if not verbose:
            show_str += score_and_wdl_str(analysis.result.info)
            if analysis.engine in self.running_analysis:
                show_str += "(running)"
            else:
                show_str += "(stopped)"
        else:
            if analysis.engine in self.running_analysis:
                show_str += "(running), "
            else:
                show_str += "(stopped), "
            if "string" in analysis.result.info:
                show_str += analysis.result.info["string"] + "\n    "
            for (key, val) in analysis.result.info.items():
                if key not in [
                        "score", "pv", "multipv", "currmove", "currmovenumber",
                        "wdl", "string"
                ]:
                    show_str += f"{key}: {val}, "
            for i, info in enumerate(analysis.result.multipv, 1):
                show_str += f"\n  {i}. {score_and_wdl_str(info)}"
        self.poutput(show_str)

    def analysis_ls(self, args) -> None:
        for analysis in self.analysis:
            if (args.running and
                    not (analysis.engine in self.running_analysis and analysis
                         == self.running_analysis[analysis.engine])):
                continue
            if (args.stopped and
                (analysis.engine in self.running_analysis
                 and analysis == self.running_analysis[analysis.engine])):
                continue
            self.show_analysis(analysis, verbose=args.verbose)

    def analysis_show(self, args) -> None:
        if not self.analysis_by_node[self.game_node]:
            self.poutput("No analysis at this move.")
            return
        for i, analysis in enumerate(self.analysis_by_node[self.game_node], 1):
            self.poutput(f"({i})", end=" ")
            self.show_analysis(analysis, verbose=True)

    def analysis_clone(self, args) -> None:
        if not self.analysis_by_node[self.game_node]:
            self.poutput("Error: No analysis at this move.")
            return
        if 0 < args.index:
            index = args.index - 1
        else:
            index = args.index
        analysis: Analysis = self.analysis_by_node[self.game_node][index]

        def fix_parents(node: chess.pgn.GameNode) -> chess.pgn.GameNode:
            node_copied = copy.copy(node)
            if isinstance(node_copied, chess.pgn.ChildNode):
                parent = fix_parents(node_copied.parent)
                assert isinstance(node, chess.pgn.ChildNode)
                parent.variations = [node_copied] + list(
                    filter(lambda x: x is not node, parent.variations))
                node_copied.parent = parent
            return node_copied

        new_game = fix_parents(self.game_node)
        new_game.variations = []
        for line in analysis.result.multipv:
            moves = line["pv"]
            new_game.add_line(moves)
        if args.name is None:
            if isinstance(new_game, chess.pgn.ChildNode):
                suggested_name = f"{MoveNumber.last(new_game)}{new_game.san()}_analysis"
            else:
                suggested_name = "start_analysis"
            if suggested_name in self.games:

                def make_name(i: int = 2) -> str:
                    try_name = f"{suggested_name}_{i}"
                    if try_name in self.games:
                        return try_name
                    return make_name(i + 1)

                name: str = make_name()
            else:
                name = suggested_name
        else:
            name = args.name
        self.games[self.current_game] = self.game_node
        self.games[name] = new_game
        self.current_game = name
        self.analysis_by_node[new_game].append(analysis)
        self.poutput(
            f"Successfully cloned the game and set the analysis in the new game '{name}'."
        )

    def analysis_update(self, args) -> None:
        if not self.analysis_by_node[self.game_node]:
            self.poutput("Error: No analysis at this move.")
            return
        if 0 < args.index:
            index = args.index - 1
        else:
            index = args.index
        analysis: Analysis = self.analysis_by_node[self.game_node][index]
        self.game_node.variations = []
        for line in analysis.result.multipv:
            moves = line["pv"]
            self.game_node.add_line(moves)

    game_argparser = cmd2.Cmd2ArgumentParser()
    game_subcmds = game_argparser.add_subparsers(dest="subcmd")
    game_ls_argparser = game_subcmds.add_parser("ls", help="List all games.")
    game_rm_argparser = game_subcmds.add_parser("rm",
                                                aliases=["remove"],
                                                help="Remove a game.")
    game_rm_argparser.add_argument(
        "name",
        nargs="?",
        help="Name of game to remove. Defaults to the current game.")
    game_goto_argparser = game_subcmds.add_parser("goto",
                                                  aliases=["gt"],
                                                  help="Goto a game.")
    game_goto_argparser.add_argument(
        "name",
        help="Name of the game to goto. List all games with `game ls`.")

    @cmd2.with_argparser(game_argparser)  # type: ignore
    def do_game(self, args) -> None:
        " Switch between, delete or create new games. "
        if args.subcmd == "ls":
            for name, game in self.games.items():
                if game.game() is self.game_node.game():
                    show_str: str = ">"
                else:
                    show_str = " "
                show_str += name
                if isinstance(game, chess.pgn.ChildNode):
                    show_str += f" @ {MoveNumber.last(game)} {game.san()}"
                else:
                    suggested_name = " @ start"
                self.poutput(show_str)
        elif args.subcmd == "rm":
            name = args.name or self.current_game
            del self.games[name]
            if not self.games:
                self.games = {"main": chess.pgn.Game()}
            self.current_game = list(self.games.keys())[0]
        elif args.subcmd == "goto":
            if args.name not in self.games:
                self.poutput(
                    f"Error: {args.name} is not the name of a game. List all games with `game ls`."
                )
                return
            self.games[self.current_game] = self.game_node
            self.current_game = args.name


def run():
    sys.exit(ChessCli().cmdloop())
