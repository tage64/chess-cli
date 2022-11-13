from . import nags

import argparse
from collections import deque, defaultdict
import copy
import datetime
import logging
import logging.handlers
import os
import queue
import re
import sys
import tempfile
import threading
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional, Union

import appdirs  # type: ignore
import chess
import chess.engine
import chess.pgn
import chess.svg
import cmd2
import more_itertools
import toml  # type: ignore

__version__ = "0.1.0"

MOVE_NUMBER_REGEX: re.Pattern = re.compile("(\d+)((\.{3})|\.?)")
COMMANDS_IN_COMMENTS_REGEX: re.Pattern = re.compile("\[%.+?\]")


class MoveNumber(NamedTuple):
    """A move number is a fullmove number and the color that made the move.
    E.G. "1." would be move number 1 and color white while "10..." would be move number 10 and color black.
    """

    move_number: int
    color: chess.Color

    @staticmethod
    def last(pos: Union[chess.Board, chess.pgn.ChildNode]):
        """Get the move number from the previously executed move."""
        if isinstance(pos, chess.pgn.ChildNode):
            board = pos.board()
        else:
            board = pos
        return MoveNumber(board.fullmove_number, board.turn).previous()

    @staticmethod
    def from_regex_match(match: re.Match):
        "Create a move number from a regex match."
        number: int = int(match.group(1))
        if match.group(3) is not None:
            color = chess.BLACK
        else:
            color = chess.WHITE
        return MoveNumber(number, color)

    @staticmethod
    def parse(move_text: str):
        """Parse a chess move number like "3." or "5...".
        Plain numbers without any dots at the end will be parsed as if it was white who moved.
        Will raise ValueError if the parsing failes.
        """
        match = MOVE_NUMBER_REGEX.fullmatch(move_text)
        if match is None:
            raise ValueError(f"Invalid move number {move_text}")
        return MoveNumber.from_regex_match(match)

    def previous(self):
        "Get previous move."
        if self.color == chess.WHITE:
            return MoveNumber(self.move_number - 1, chess.BLACK)
        else:
            return MoveNumber(self.move_number, chess.WHITE)

    def next(self):
        "Get next move."
        if self.color == chess.WHITE:
            return MoveNumber(self.move_number, chess.BLACK)
        else:
            return MoveNumber(self.move_number + 1, chess.WHITE)

    def __str__(self) -> str:
        return str(self.move_number) + ("." if self.color == chess.WHITE else "...")

    def __lt__(self, other) -> bool:
        return (
            self.move_number < other.move_number
            or self.move_number == other.move_number
            and self.color == chess.WHITE
            and other.color == chess.BLACK
        )

    def __gt__(self, other) -> bool:
        return (
            self.move_number > other.move_number
            or self.move_number == other.move_number
            and self.color == chess.BLACK
            and other.color == chess.WHITE
        )

    def __le__(self, other) -> bool:
        return (
            self.move_number < other.move_number
            or self.move_number == other.move_number
            and (self.color == chess.WHITE or other.color == chess.BLACK)
        )

    def __ge__(self, other) -> bool:
        return (
            self.move_number > other.move_number
            or self.move_number == other.move_number
            and (self.color == chess.BLACK or other.color == chess.WHITE)
        )


class EngineConf(NamedTuple):
    "Configuration for an engine."
    path: str  # Path of engine executable.
    protocol: str  # "uci" or "xboard"
    options: dict[str, Union[str, int, bool, None]] = {}
    fullname: Optional[str] = None  # Full name of the engine from id.name.


class Analysis(NamedTuple):
    "Information about analysis."

    result: chess.engine.SimpleAnalysisResult
    engine: str
    board: chess.Board
    san: Optional[str]


def move_str(
    game_node: chess.pgn.GameNode,
    include_move_number: bool = True,
    include_sideline_arrows: bool = True,
) -> str:
    res: str = ""
    if not isinstance(game_node, chess.pgn.ChildNode):
        res += "start"
    else:
        if include_sideline_arrows and not game_node.is_main_variation():
            res += "<"
        if include_move_number:
            res += str(MoveNumber.last(game_node)) + " "
        if game_node.starting_comment:
            res += "-"
        res += game_node.san()
        if game_node.nags:
            nag_strs = [nags.ascii_glyph(nag) for nag in game_node.nags]
            if len(nag_strs) == 1:
                res += nag_strs[0]
            else:
                res += f"[{', '.join(nag_strs)}]"
    if (
        game_node.comment
        or game_node.arrows()
        or game_node.eval() is not None
        or game_node.clock() is not None
    ):
        res += "-"
    if (
        include_sideline_arrows
        and game_node.parent is not None
        and not game_node.parent.variations[-1] == game_node
    ):
        res += ">"
    return res


def score_str(score: chess.engine.Score) -> str:
    if score == chess.engine.MateGiven:
        return "mate"
    if score.is_mate():
        mate: int = score.mate()  # type: ignore
        if 0 < mate:
            return f"Mate in {mate}"
        return f"Mated in {-mate}"
    cp: int = score.score()  # type: ignore
    if cp > 0:
        return f"+{cp/100} pawns"
    return f"{cp/100} pawns"


def comment_text(raw_comment: str) -> str:
    """Strip out all commands like [%cal xxx] or [%clk xxx] from a comment."""
    return " ".join(COMMANDS_IN_COMMENTS_REGEX.split(raw_comment)).strip()


def commands_in_comment(raw_comment: str) -> str:
    "Get a string with all embedded commands in a pgn comment."
    return " ".join(COMMANDS_IN_COMMENTS_REGEX.findall(raw_comment))


def update_comment_text(original_comment: str, new_text: str) -> str:
    "Return a new comment with the same embedded commands but with the text replaced."
    return f"{commands_in_comment(original_comment)}\n{new_text}"


class ChessCli(cmd2.Cmd):
    """A repl to edit and analyse chess games."""

    def __init__(
        self, file_name: Optional[str] = None, config_file: Optional[str] = None
    ):
        # Set cmd shortcuts
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts, include_py=True, allow_cli_args=False)
        self.self_in_py = True
        self.register_postloop_hook(self.close_engines)

        self.config_file: str = config_file or os.path.join(
            appdirs.user_config_dir("chess-cli"), "config.toml"
        )

        self.engine_confs: dict[str, EngineConf] = {}
        self.loaded_engines: dict[str, chess.engine.SimpleEngine] = {}
        self.engines_saved_log: deque[str] = deque()
        self.engines_log_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        log_handler = logging.handlers.QueueHandler(self.engines_log_queue)
        log_handler.setLevel(logging.WARNING)
        log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        chess.engine.LOGGER.addHandler(log_handler)
        self.selected_engines: list[str] = []
        self.running_analysis: dict[str, Analysis] = dict()
        self.analysis: list[Analysis] = []
        self.analysis_by_node: defaultdict[
            chess.pgn.GameNode, dict[str, Analysis]
        ] = defaultdict(dict)
        self.auto_analysis_engines: list[str] = []
        self.auto_analysis_number_of_moves = (
            3  # This is just arbitrary, it'll be changed later.
        )

        def update_auto_analysis(
            x: cmd2.plugin.PostcommandData,
        ) -> cmd2.plugin.PostcommandData:
            self.update_auto_analysis()
            return x

        self.register_postcmd_hook(update_auto_analysis)

        if os.path.exists(self.config_file):
            with open(self.config_file) as f:
                try:
                    items = toml.load(f)
                    engine_confs = items["engine-configurations"]
                    if not isinstance(engine_confs, dict):
                        raise Exception("'engine-configurations' must be a list.")
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
            self.poutput(f"Warning: Couldn't find config file at '{config_file}'.")
            self.poutput("This session will be started with an empty configuration.")

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
        self.file_names: dict[str, str] = (
            {} if file_name is None else {"main": file_name}
        )
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
        "moves", nargs="+", help="A list of moves in standard algibraic notation."
    )
    play_argparser.add_argument(
        "-c",
        "--comment",
        help="Add a comment for the move (or the last move if more than one is supplied.",
    )
    play_argparser.add_argument(
        "-m",
        "--main-line",
        action="store_true",
        help="If a variation already exists from this move, add this new variation as the main line rather than a side line.",
    )
    play_argparser.add_argument(
        "-s", "--sideline", action="store_true", help="Add a sideline to this move."
    )

    @cmd2.with_argparser(play_argparser)  # type: ignore
    def do_play(self, args) -> None:
        """Play a sequence of moves from the current position."""
        if args.sideline:
            if not isinstance(self.game_node, chess.pgn.ChildNode):
                self.poutput(f"Cannot add a sideline to the root of the game.")
                return
            self.game_node = self.game_node.parent

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
    show_argparser.add_argument(
        "what",
        choices=[
            "comment",
            "nags",
            "evaluation",
            "arrows",
            "clock",
            "starting-comment",
            "all",
        ],
        default="all",
        nargs="?",
        help="What to show.",
    )

    @cmd2.with_argparser(show_argparser)  # type: ignore
    def do_show(self, args) -> None:
        "Show various things like comments and arrows about the current move."
        if (
            isinstance(self.game_node, chess.pgn.ChildNode)
            and self.game_node.starting_comment
            and (args.what == "starting_comment" or args.what == "all")
        ):
            self.poutput(self.game_node.starting_comment)
            self.poutput(
                f"    {MoveNumber.last(self.game_node)} {self.game_node.san()}"
            )
        if self.game_node and (args.what == "comment" or args.what == "all"):
            self.poutput(self.game_node.comment)
        if self.game_node.nags and (args.what == "nags" or args.what == "all"):
            for nag in self.game_node.nags:
                text: str = "NAG: " if args.what == "all" else ""
                text += f"({nags.ascii_glyph(nag)}) {nags.description(nag)}"
                self.poutput(text)
        eval = self.game_node.eval()
        if eval is not None and (args.what == "evaluation" or args.what == "all"):
            text = "Evaluation: " if args.what == "all" else ""
            text += score_str(eval.relative)
            if self.game_node.eval_depth() is not None:
                text += f", Depth: {self.game_node.eval_depth()}"
            self.poutput(text)
        if self.game_node.arrows() and (args.what == "arrows" or args.what == "all"):
            text = "Arows: " if args.what == "all" else ""
            text += str(
                [
                    f"{arrow.color} {chess.square_name(arrow.tail)}-{chess.square_name(arrow.head)}"
                    for arrow in self.game_node.arrows()
                ]
            )
            self.poutput(text)
        clock = self.game_node.clock()
        if clock is not None and (args.what == "clock" or args.what == "all"):
            text = "Clock: " if args.what == "all" else ""
            text += str(datetime.timedelta(seconds=clock)).strip("0")
            self.poutput(text)

    add_argparser = cmd2.Cmd2ArgumentParser()
    add_subcmds = add_argparser.add_subparsers(dest="subcmd")
    add_comment_argparser = add_subcmds.add_parser(
        "comment", aliases=["c"], help="Set comment for this move."
    )
    add_comment_argparser.add_argument(
        "comment", default="", nargs="?", help="The new text."
    )
    add_comment_argparser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="Append this text to the old comment.",
    )
    add_comment_argparser.add_argument(
        "-r",
        "--raw",
        action="store_true",
        help="Replace the raw pgn comment which will override embedded commands like arrows and clocks.",
    )
    add_comment_argparser.add_argument(
        "-e", "--edit", action="store_true", help="Open the comment in your editor."
    )
    add_starting_comment_argparser = add_subcmds.add_parser(
        "starting-comment",
        aliases=["sc"],
        help="Set starting_comment for this move. Only moves that starts a variation can have a starting comment.",
    )
    add_starting_comment_argparser.add_argument(
        "comment", default="", nargs="?", help="The new text."
    )
    add_starting_comment_argparser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="Append this text to the old comment.",
    )
    add_starting_comment_argparser.add_argument(
        "-e", "--edit", action="store_true", help="Open the comment in your editor."
    )
    add_nag_argparser = add_subcmds.add_parser(
        "nag", help="Set a nag (numeric annotation glyph) on this move."
    )
    add_nag_argparser.add_argument(
        "nag",
        help="Nag, either a number like '$17' or an ascii glyph like '!' or '?!'.",
    )
    add_nag_argparser.add_argument(
        "-a",
        "--append",
        action="store_true",
        help="Append this nag to the list of existing nags at this move instead of replacing them.",
    )
    add_eval_argparser = add_subcmds.add_parser(
        "evaluation", aliases=["eval"], help="Set an evaluation for this move."
    )
    add_eval_group = add_eval_argparser.add_mutually_exclusive_group(required=True)
    add_eval_group.add_argument(
        "--cp",
        type=int,
        help="Relative score in centi pawns from the player to move's point of view.",
    )
    add_eval_group.add_argument(
        "--mate",
        "--mate-in",
        type=int,
        help="The player to move can force mate in the given number of moves.",
    )
    add_eval_group.add_argument(
        "--mated",
        "--mated-in",
        type=int,
        help="The player to move will be mated in the given number of moves.",
    )
    add_eval_argparser.add_argument(
        "-d", "--depth", type=int, help="The depth at which the analysis was made."
    )
    add_arrow_argparser = add_subcmds.add_parser(
        "arrow", aliases=["arr"], help="Draw an arrow on the board."
    )
    add_arrow_argparser.add_argument(
        "_from",
        type=chess.parse_square,
        help="The square from which the arrow is drawn.",
    )
    add_arrow_argparser.add_argument(
        "to", type=chess.parse_square, help="The square which the arrow is pointing to."
    )
    add_arrow_argparser.add_argument(
        "color",
        choices=["red", "r", "yellow", "y", "green", "g", "blue", "b"],
        default="green",
        nargs="?",
        help="Color of the arrow. Red/yellow/green/blue can be abbreviated as r/y/g/b.",
    )
    add_clock_argparser = add_subcmds.add_parser(
        "clock", help="Set the remaining time for the player making this move."
    )
    add_clock_argparser.add_argument("time", help="Remaining time.")

    @cmd2.with_argparser(add_argparser)  # type: ignore
    def do_add(self, args) -> None:
        "Add various things (like comments, nags or arrows) at the current move."
        if args.subcmd in ["comment", "c"]:
            if args.edit:
                with tempfile.NamedTemporaryFile(mode="w+") as file:
                    file.write(
                        self.game_node.comment
                        if args.raw
                        else comment_text(self.game_node.comment)
                    )
                    file.flush()
                    self.do_shell(f"{self.editor} '{file.name}'")
                    file.seek(0)
                    new_comment: str = file.read().strip()
                    self.game_node.comment = (
                        new_comment
                        if args.raw
                        else update_comment_text(self.game_node.comment, new_comment)
                    )
            elif args.append and self.game_node.comment:
                raw_comment: str = self.game_node.comment
                to_edit = raw_comment if args.raw else comment_text(raw_comment)
                appended: str = " ".join((to_edit, args.comment))
                self.game_node.comment = (
                    appended if args.raw else update_comment_text(raw_comment, appended)
                )
            else:
                self.game_node.comment = (
                    args.comment
                    if args.raw
                    else update_comment_text(self.game_node.comment, args.comment)
                )
        elif args.subcmd in ["sc", "starting-comment"]:
            if not self.game_node.starts_variation():
                self.poutput(
                    "Error: Only moves that starts a variation can have a starting comment and this move doesn't start a variation.\nYour attempt to set a starting comment for this move was a complete failure!"
                )
                return
            if args.edit:
                with tempfile.NamedTemporaryFile(mode="w+") as file:
                    file.write(self.game_node.starting_comment)
                    file.flush()
                    self.do_shell(f"{self.editor} '{file.name}'")
                    file.seek(0)
                    self.game_node.starting_comment = file.read().strip()
            elif args.append and self.game_node.starting_comment:
                self.game_node.starting_comment = " ".join(
                    (self.game_node.starting_comment, args.comment)
                )
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
            self.poutput(f"Set NAG ({nags.ascii_glyph(nag)}): {nags.description(nag)}.")
        elif args.subcmd in ["eval", "evaluation"]:
            if args.mate is not None:
                score: chess.engine.Score = chess.engine.Mate(args.mate)
            elif args.mated is not None:
                score = chess.engine.Mate(-args.mated)
            else:
                score = chess.engine.Cp(args.cp)
            self.game_node.set_eval(
                chess.engine.PovScore(score, self.game_node.turn()), args.depth
            )
        elif args.subcmd in ["arr", "arrow"]:
            color_abbreviations: dict[str, str] = {
                "g": "green",
                "y": "yellow",
                "r": "red",
                "b": "blue",
            }
            if args.color in color_abbreviations:
                color = color_abbreviations[args.color]
            else:
                color = args.color
            self.game_node.set_arrows(
                self.game_node.arrows()
                + [chess.svg.Arrow(args._from, args.to, color=color)]
            )
        elif args.subcmd == "clock":
            time_parsed = re.fullmatch("(\d+)(:(\d+))?(:(\d+))?([.,](\d+))?", args.time)
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
        else:
            assert False, "Unhandled subcommand."

    rm_argparser = cmd2.Cmd2ArgumentParser()
    rm_subcmds = rm_argparser.add_subparsers(dest="subcmd")
    rm_comment_argparser = rm_subcmds.add_parser(
        "comment", aliases=["c"], help="Remove the comment at this move."
    )
    rm_comment_argparser.add_argument(
        "-r",
        "--raw",
        action="store_true",
        help="Remove the entire raw comment which will include all embedded commands like arrows and evaluations.",
    )
    rm_subcmds.add_parser(
        "starting-comment",
        aliases=["sc"],
        help="Remove the starting comment at this move.",
    )
    rm_subcmds.add_parser("nags", help="Remove all NAGs at this move.")
    rm_nag_argparser = rm_subcmds.add_parser(
        "nag", help="Remove a specific NAG at this move."
    )
    rm_nag_argparser.add_argument(
        "nag", type=nags.parse_nag, help="A NAG to remove. Like '$16' or '??'."
    )
    rm_subcmds.add_parser(
        "evaluation",
        aliases=["eval"],
        help="Remove the evaluation annotation at this move if any.",
    )
    rm_arrows_argparser = rm_subcmds.add_parser(
        "arrows",
        aliases=["arr"],
        help="Remove arrows at this move. Please specify some options if you not intend to remove all arrows at this move.",
    )
    rm_arrows_argparser.add_argument(
        "-f",
        "--from",
        dest="_from",
        type=chess.parse_square,
        help="Remove only arrows starting at this square.",
    )
    rm_arrows_argparser.add_argument(
        "-t",
        "--to",
        type=chess.parse_square,
        help="Remove only arrows ending at this square.",
    )
    rm_arrows_argparser.add_argument(
        "-c",
        "--color",
        choices=["red", "r", "yellow", "y", "green", "g", "blue", "b"],
        help="Remove only arrows with this color. Red/yellow/green/blue can be abbreviated as r/y/g/b.",
    )
    rm_subcmds.add_parser(
        "clock", help="Remove the clock annotation at this move if any."
    )

    @cmd2.with_argparser(rm_argparser)  # type: ignore
    def do_rm(self, args) -> None:
        "Remove various things at the current move (like the comment or arrows)."
        if args.subcmd in ["c", "comment"]:
            if args.raw:
                self.game_node.comment = ""
            else:
                self.game_node.comment = update_comment_text(self.game_node.comment, "")
        elif args.subcmd in ["sc", "starting_comment"]:
            self.game_node.starting_comment = ""
        elif args.subcmd == "nags":
            self.game_node.nags = set()
        elif args.subcmd in ["eval", "evaluation"]:
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
        elif args.subcmd in ["arrows", "arr"]:
            color_abbreviations: dict[str, str] = {
                "g": "green",
                "y": "yellow",
                "r": "red",
                "b": "blue",
            }
            if args.color is None:
                color: Optional[str] = None
            elif args.color in color_abbreviations:
                color = color_abbreviations[args.color]
            else:
                color = args.color
            self.game_node.set_arrows(
                (
                    arr
                    for arr in self.game_node.arrows()
                    if args._from is None or not args._from == arr.tail
                    if args.to is None or not args.to == arr.head
                    if color is None or not color == arr.color
                )
            )
        else:
            assert False, "Unhandled subcommand."

    moves_argparser = cmd2.Cmd2ArgumentParser()
    moves_argparser.add_argument(
        "-c",
        "--comments",
        action="store_true",
        help='Show all comments. Otherwise just a dash ("-") will be shown at each move with a comment.',
    )
    _moves_from_group = moves_argparser.add_mutually_exclusive_group()
    _moves_from_group.add_argument(
        "--fc",
        "--from-current",
        dest="from_current",
        action="store_true",
        help="Print moves from the current move.",
    )
    _moves_from_group.add_argument(
        "-f", "--from", dest="_from", help="Print moves from the given move number."
    )
    _moves_to_group = moves_argparser.add_mutually_exclusive_group()
    _moves_to_group.add_argument(
        "--tc",
        "--to-current",
        dest="to_current",
        action="store_true",
        help="Print only moves upto and including the current move.",
    )
    _moves_to_group.add_argument(
        "-t", "--to", help="Print moves to the given move number."
    )
    moves_argparser.add_argument(
        "-s",
        "--sidelines",
        action="store_true",
        help="Print a short list of the sidelines at each move with variations.",
    )
    moves_argparser.add_argument(
        "-r", "--recurse", action="store_true", help="Recurse into sidelines."
    )

    @cmd2.with_argparser(moves_argparser)  # type: ignore
    def do_moves(self, args) -> None:
        """Print the moves in the game.
        Print all moves by default, but if some constraint is specified, print only those moves.
        """

        if args._from is not None:
            # If the user has specified a given move as start.
            node = self.find_move(
                args._from,
                search_sidelines=False,
            )
            if node is None:
                self.poutput(f"Error: Couldn't find the move {args._from}")
                return
            start_node: chess.pgn.ChildNode = node
        elif args.from_current:
            # Start printing at the current move.
            if isinstance(self.game_node, chess.pgn.ChildNode):
                start_node = self.game_node
            else:
                # If `self.game_node` is the root node.
                next = self.game_node.next()
                if next is None:
                    return
                start_node = next
        else:
            # Print moves from the start of the game.
            first_move = self.game_node.game().next()
            if first_move is None:
                return
            start_node = first_move

        if args.to is not None:
            node = self.find_move(
                args.to, break_search_backwards_at=lambda x: x is start_node
            )
            if node is None:
                self.poutput(f"Error: Couldn't find the move {args.to}")
                return
            end_node = node
        elif args.to_current:
            if isinstance(self.game_node, chess.pgn.ChildNode):
                end_node = self.game_node
            else:
                return
        else:
            # Print moves until the end of the game.
            end = self.game_node.end()
            if not isinstance(end, chess.pgn.ChildNode):
                return
            end_node = end

        lines: Iterable[str] = self.display_game_segment(
            start_node,
            end_node,
            show_sidelines=args.sidelines,
            recurse_sidelines=args.recurse,
            show_comments=args.comments,
        )

        for line in lines:
            self.poutput(f"  {line}")

    def display_game_segment(
        self,
        start_node: chess.pgn.ChildNode,
        end_node: chess.pgn.ChildNode,
        show_sidelines: bool,
        recurse_sidelines: bool,
        show_comments: bool,
    ) -> Iterable[str]:
        """Given a start and end node in this game, which must be connected,
        yield lines printing all moves between them (including endpoints).
        There are also options to toggle visibility of comments, show a short
        list of the sidelines at each move with sidelines, or even recurse and
        show the endire sidelines.
        """

        # Create a list of all moves that should be displayed following the
        # main line (I.E not recursing into sidelines).
        # The list is created in reversed order. This is important because we
        # want to display the moves from the start to the end, but we don't
        # know the path from the start to the end. Imagine for instance that we
        # are not following the main line, then we don't know what variation to
        # choose at a certain move number.
        moves_on_mainline: deque[chess.pgn.ChildNode] = deque()
        node: chess.pgn.ChildNode = end_node
        while True:
            moves_on_mainline.appendleft(node)
            if node is start_node:
                break
            if not isinstance(node.parent, chess.pgn.ChildNode):
                break
            node = node.parent
        return self.display_moves(
            moves_on_mainline,
            show_sidelines=show_sidelines,
            recurse_sidelines=recurse_sidelines,
            show_comments=show_comments,
        )

    def display_moves(
        self,
        moves: Iterable[chess.pgn.ChildNode],
        show_sidelines: bool,
        recurse_sidelines: bool,
        show_comments: bool,
        include_sidelines_at_first_move: bool = True,
    ) -> Iterable[str]:
        """Same as display_game_segment(), but this function takes an iterable
        of moves instead of a starting and ending game node.
        """

        moves_per_line: int = 6
        current_line: str = ""
        moves_at_current_line: int = 0

        # Just a very small method that should be called when we've yielded a line.
        def carriage_return():
            nonlocal current_line
            nonlocal moves_at_current_line
            current_line = ""
            moves_at_current_line = 0

        for (i, node) in enumerate(moves):
            if moves_at_current_line >= moves_per_line:
                yield current_line
                carriage_return()

            include_move_number = (
                True if moves_at_current_line == 0 else node.turn() == chess.BLACK
            )

            # Add a space if current_line is not empty.
            if current_line:
                current_line += " "
            current_line += move_str(
                node,
                include_move_number=include_move_number,
                include_sideline_arrows=True,
            )
            if node.turn() == chess.BLACK:
                moves_at_current_line += 1

            if node.comment and show_comments:
                yield current_line
                carriage_return()
                yield f"   {node.comment}"
                # No carriage_return() is needed here.

            # If this move has any sidelines.
            if len(node.parent.variations) > 1 and (
                include_sidelines_at_first_move or not i == 0
            ):
                if recurse_sidelines:
                    # Flush the current line if needed.
                    if current_line:
                        yield current_line
                        carriage_return()

                    # Loop through the sidelines (siblings) to this node.
                    for sideline in node.parent.variations:
                        if sideline is node:
                            continue

                        # Display any possible starting_comment.
                        if show_comments and sideline.starting_comment:
                            yield f"     {sideline.starting_comment}"

                        # Call this method recursively with the mainline
                        # following the sideline as moves iterator.
                        for line in self.display_moves(
                            more_itertools.prepend(sideline, sideline.mainline()),
                            show_sidelines=show_sidelines,
                            recurse_sidelines=recurse_sidelines,
                            show_comments=show_comments,
                            include_sidelines_at_first_move=False,
                        ):
                            # Indent the sideline a bit.
                            yield f"  {line}"
                elif show_sidelines:
                    # Only show a short list of all sideline moves.

                    # Flush the current line if needed.
                    if current_line:
                        yield current_line
                        carriage_return()
                    current_line = (
                        "  ("
                        + "; ".join(
                            map(
                                lambda sideline: (
                                    " "
                                    if sideline is node
                                    else move_str(
                                        sideline,
                                        include_move_number=False,
                                        include_sideline_arrows=False,
                                    )
                                ),
                                node.parent.variations,
                            )
                        )
                        + ")"
                    )
                    yield current_line
                    carriage_return()

        # A final flush!
        if current_line:
            yield current_line

    def find_move(
        self,
        move_str: str,
        search_sidelines: bool = True,
        search_forwards: bool = True,
        search_backwards: bool = True,
        recurse_sidelines: bool = True,
        break_search_forwards_at: Optional[
            Callable[[chess.pgn.ChildNode], bool]
        ] = None,
        break_search_backwards_at: Optional[
            Callable[[chess.pgn.ChildNode], bool]
        ] = None,
    ) -> Optional[chess.pgn.ChildNode]:
        """Search for a move by a string of its move number and SAN.
        Like 'e4' '8.Nxe5' or 8...'.
        """
        move_number_match = MOVE_NUMBER_REGEX.match(move_str)
        if move_number_match is not None:
            move_number: Optional[MoveNumber] = MoveNumber.from_regex_match(
                move_number_match
            )
            if len(move_str) > move_number_match.end():
                move: Optional[str] = move_str[move_number_match.end() :]
            else:
                move = None
        else:
            move_number = None
            move = move_str

        def check(node: chess.pgn.ChildNode) -> bool:
            if move is not None:
                try:
                    if not node.move == node.parent.board().push_san(move):
                        return False
                except ValueError:
                    return False
            if move_number is not None and not move_number == MoveNumber.last(node):
                return False
            return True

        if isinstance(self.game_node, chess.pgn.ChildNode):
            current_node: chess.pgn.ChildNode = self.game_node
        else:
            next = self.game_node.next()
            if next is not None and search_forwards:
                current_node = next
            else:
                return None

        search_queue: deque[chess.pgn.ChildNode] = deque()
        search_queue.append(current_node)
        if search_sidelines:
            sidelines = current_node.parent.variations
            search_queue.extend((x for x in sidelines if not x is current_node))

        if search_forwards and (
            move_number is None or move_number >= MoveNumber.last(current_node)
        ):
            while search_queue:
                node: chess.pgn.ChildNode = search_queue.popleft()
                if check(node):
                    return node
                if break_search_forwards_at is not None and break_search_forwards_at(
                    node
                ):
                    break
                if move_number is not None and move_number < MoveNumber.last(node):
                    break
                if search_sidelines and (node.is_main_variation or recurse_sidelines):
                    search_queue.extend(node.variations)
                else:
                    next = node.next()
                    if next is not None:
                        search_queue.append(next)

        if search_backwards and (
            move_number is None or move_number < MoveNumber.last(current_node)
        ):
            node = current_node
            while isinstance(node.parent, chess.pgn.ChildNode):
                node = node.parent
                if check(node):
                    return node
                if break_search_backwards_at is not None and break_search_backwards_at(
                    node
                ):
                    break
                if move_number is not None and move_number > MoveNumber.last(node):
                    break
        return None

    goto_argparser = cmd2.Cmd2ArgumentParser()
    goto_argparser.add_argument(
        "move",
        nargs="?",
        help="A move, move number or both. E.G. 'e4', '8...' or '9.dxe5+'.",
    )
    goto_group = goto_argparser.add_mutually_exclusive_group()
    goto_group.add_argument(
        "-s", "--start", action="store_true", help="Go to the start of the game."
    )
    goto_group.add_argument(
        "-e", "--end", action="store_true", help="Go to the end of the game."
    )
    goto_argparser.add_argument(
        "-n",
        "--no-sidelines",
        action="store_true",
        help="Don't search any sidelines at all.",
    )
    _goto_direction_group = goto_argparser.add_mutually_exclusive_group()
    _goto_direction_group.add_argument(
        "-b",
        "--backwards-only",
        action="store_true",
        help="Only search the game backwards.",
    )
    _goto_direction_group.add_argument(
        "-f",
        "--forwards-only",
        action="store_true",
        help="Only search the game forwards.",
    )

    @cmd2.with_argparser(goto_argparser)  # type: ignore
    def do_goto(self, args) -> None:
        """Goto a move specified by a move number or a move in standard algibraic notation.
        If a move number is specified, it will follow the main line to that move if it does exist. If a move like "e4" or "Nxd5+" is specified as well, it will go to the specific move number and search between variations at that level for the specified move. If only a move but not a move number and no other constraints are given, it'll first search sidelines at the current move, then follow the mainline and check if any move or sideline matches, but not recurse into sidelines. Lastly, it'll search backwards in the game.
        """
        if args.start:
            self.game_node = self.game_node.game()
        elif args.end:
            self.game_node = self.game_node.end()
        elif args.move is not None:
            node = self.find_move(
                args.move,
                search_sidelines=not args.no_sidelines,
                search_forwards=not args.backwards_only,
                search_backwards=not args.forwards_only,
            )
            if node is None:
                self.poutput(f"Error: Couldn't find the move {args.move}")
                return
            self.game_node = node

    delete_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(delete_argparser)  # type: ignore
    def do_delete(self, _args) -> None:
        "Delete the current move."
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
                    parent.variations = (
                        parent.variations[:i] + parent.variations[i + 1 :]
                    )

    engine_argparser = cmd2.Cmd2ArgumentParser(
        description="Everything related to chess engines. See subcommands for detailes"
    )
    engine_argparser.add_argument(
        "-s",
        "--select",
        action="append",
        help="Select a different engine for this command. The option can be repeated to select multiple engines.",
    )
    engine_subcmds = engine_argparser.add_subparsers(dest="subcmd")
    engine_ls_argparser = engine_subcmds.add_parser(
        "ls", help="List loaded chess engines."
    )
    engine_ls_argparser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Display more information about the engines.",
    )
    engine_ls_argparser.add_argument(
        "-c",
        "--configured",
        action="store_true",
        help="List all configured engines, even those that aren't loaded.",
    )
    engine_ls_argparser.add_argument(
        "-s", "--selected", action="store_true", help="List the selected engines."
    )
    engine_load_argparser = engine_subcmds.add_parser(
        "load", help="Load a chess engine."
    )
    engine_load_argparser.add_argument(
        "name",
        help="Name of the engine. List availlable engines with the command `engine ls`",
    )
    engine_load_argparser.add_argument(
        "--as",
        dest="load_as",
        help="Load the engine with a different name. Useful if you want to have multiple instances of an engine running at the same time.",
    )
    engine_import_argparser = engine_subcmds.add_parser(
        "import", help="Import a chess engine."
    )
    engine_import_argparser.add_argument("path", help="Path to engine executable.")
    engine_import_argparser.add_argument(
        "name", help="A short name for the engine, (PRO TIP: avoid spaces in the name)."
    )
    engine_import_argparser.add_argument(
        "-p",
        "--protocol",
        choices=["uci", "xboard"],
        default="uci",
        help="Type of engine protocol.",
    )
    engine_quit_argparser = engine_subcmds.add_parser(
        "quit", help="Quit all selected engines."
    )
    engine_select_argparser = engine_subcmds.add_parser(
        "select",
        help="Select a loaded engine. The selected engine will be default commands like `engine analyse` or `engine config`.",
    )
    engine_select_argparser.add_argument(
        "engines", nargs="+", help="List of engines to select."
    )
    engine_config_argparser = engine_subcmds.add_parser(
        "config",
        aliases=["conf", "configure"],
        help="Set values for or get current values of different engine specific parameters.",
    )
    engine_config_argparser.add_argument("engine", help="Engine to configure.")
    engine_config_subcmds = engine_config_argparser.add_subparsers(dest="config_subcmd")
    engine_config_get_argparser = engine_config_subcmds.add_parser(
        "get", help="Get the value of an option for the selected engine."
    )
    engine_config_get_argparser.add_argument("name", help="Name of the option.")
    engine_config_ls_argparser = engine_config_subcmds.add_parser(
        "ls",
        aliases=["list"],
        help="List availlable options and their current values for the selected engine.",
    )
    engine_config_ls_argparser.add_argument(
        "-r",
        "--regex",
        help="Filter option names by a case insensitive regular expression.",
    )
    engine_config_ls_argparser.add_argument(
        "-t",
        "--type",
        choices=["checkbox", "combobox" "integer", "text", "button"],
        nargs="+",
        help="Filter options by the given type.",
    )
    engine_config_ls_configured_group = (
        engine_config_ls_argparser.add_mutually_exclusive_group()
    )
    engine_config_ls_configured_group.add_argument(
        "--configured",
        action="store_true",
        help="Only list options that are already configured in some way.",
    )
    engine_config_ls_configured_group.add_argument(
        "--not-configured",
        action="store_true",
        help="Only list options that are not configured.",
    )
    engine_config_ls_argparser.add_argument(
        "--include-auto",
        "--include-automatically-managed",
        action="store_true",
        help="By default, some options like MultiPV or Ponder are managed automatically. There is no reason to change them so they are hidden by default. This options makes them vissable.",
    )
    engine_config_set_argparser = engine_config_subcmds.add_parser(
        "set", help="Set a value of an option for the selected engine."
    )
    engine_config_set_argparser.add_argument("name", help="Name of the option to set.")
    engine_config_set_argparser.add_argument(
        "value",
        help="The new value. Use true/check or false/uncheck to set a checkbox. Buttons can only be set to 'trigger-on-startup', but note that you must use the `engine config trigger` command to trigger it right now.",
    )
    engine_config_set_argparser.add_argument(
        "-t",
        "--temporary",
        action="store_true",
        help="Set the value in the running engine but don't store it in the engine configuration. It'll not be set if you save this configuration and load the engine again.",
    )
    engine_config_unset_argparser = engine_config_subcmds.add_parser(
        "unset",
        help="Change an option back to its default value and remove it from the configuration.",
    )
    engine_config_unset_argparser.add_argument(
        "name", help="Name of the option to unset."
    )
    engine_config_unset_argparser.add_argument(
        "-t",
        "--temporary",
        action="store_true",
        help="Unset the value in the running engine but keep it in the engine configuration. It'll still be set if you save this configuration and load the engine again.",
    )
    engine_config_trigger_argparser = engine_config_subcmds.add_parser(
        "trigger", help="Trigger an option of type button."
    )
    engine_config_trigger_argparser.add_argument(
        "name", help="Name of the option to trigger."
    )
    engine_config_save_argparser = engine_config_subcmds.add_parser(
        "save", help="Save the current configuration."
    )
    engine_log_argparser = engine_subcmds.add_parser(
        "log", help="Show the logged things (like stderr) from the loaded engines."
    )
    engine_log_subcmds = engine_log_argparser.add_subparsers(dest="log_subcmd")
    engine_log_subcmds.add_parser("clear", help="Clear the log.")
    engine_log_subcmds.add_parser("show", help="Show the log.")

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
        elif args.subcmd == "log":
            self.engine_log(args)
        elif args.subcmd in ["config", "conf", "configure"]:
            if args.engine not in self.loaded_engines:
                if args.engine in self.engine_confs:
                    self.poutput(
                        f"Error: {args.engine} is not loaded. You can try to load it by running `engine load {args.engine}`."
                    )
                else:
                    self.poutput(
                        f"Error: There is no engine named {args.engine}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                    )
                return
            if args.config_subcmd == "get":
                self.engine_config_get(args.engine, args)
            if args.config_subcmd == "ls":
                self.engine_config_ls(args.engine, args)
            if args.config_subcmd == "set":
                self.engine_config_set(args.engine, args)
            if args.config_subcmd == "unset":
                self.engine_config_unset(args.engine, args)
            if args.config_subcmd == "trigger":
                self.engine_config_trigger(args.engine, args)
            if args.config_subcmd == "save":
                self.engine_config_save(args.engine, args)
        else:
            if args.select:
                for engine in args.select:
                    if engine not in self.loaded_engines:
                        if engine in self.engine_confs:
                            self.poutput(
                                f"Error: {engine} is not loaded. You can try to load it by running `engine load {engine}`."
                            )
                        else:
                            self.poutput(
                                f"Error: There is no engine named {engine}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                            )
                        return
                selected_engines: list[str] = args.select
            elif self.selected_engines:
                selected_engines = self.selected_engines
            else:
                self.poutput(
                    "Error: No engines selected. Consider selecting one with the `engine select` command."
                )
                return
            if args.subcmd == "quit":
                self.engine_quit(selected_engines, args)

    def engine_select(self, args) -> None:
        for engine in args.engines:
            if engine not in self.loaded_engines:
                if engine in self.engine_confs:
                    self.poutput(
                        f"Error: {engine} is not loaded. You can try to load it by running `engine load {engine}`."
                    )
                else:
                    self.poutput(
                        f"Error: There is no engine named {engine}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                    )
                return
        self.selected_engines = args.engines

    def show_engine(self, name: str, verbose: bool = False) -> None:
        conf: EngineConf = self.engine_confs[name]
        if name in self.selected_engines:
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
        if name in self.selected_engines:
            show_str += ", (selected)"
        self.poutput(show_str)
        if verbose:
            self.poutput(f"    Executable: {conf.path}")
            if name in self.loaded_engines:
                engine: chess.engine.SimpleEngine = self.loaded_engines[name]
                for key, val in engine.id.items():
                    if not key == "name":
                        self.poutput(f"   {key}: {val}")

    def engine_ls(self, args) -> None:
        for name in self.engine_confs.keys():
            if not args.configured and name not in self.loaded_engines:
                break
            if args.selected and name not in self.selected_engines:
                break
            self.show_engine(name, verbose=args.verbose)

    def load_engine(self, name: str) -> None:
        engine_conf: EngineConf = self.engine_confs[name]
        try:
            if engine_conf.protocol == "uci":
                engine = chess.engine.SimpleEngine.popen_uci(
                    engine_conf.path, timeout=120
                )
            elif engine_conf.protocol == "xboard":
                engine = chess.engine.SimpleEngine.popen_xboard(
                    engine_conf.path, timeout=120
                )
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
        except OSError as e:
            self.poutput(
                f"Error: While loading engine executable {engine_conf.path}: {e}"
            )
            raise e
        self.loaded_engines[name] = engine
        self.selected_engines.append(name)
        self.engine_confs[name] = engine_conf._replace(fullname=engine.id.get("name"))
        invalid_options: list[str] = []
        for opt_name, value in engine_conf.options.items():
            try:
                self.set_engine_option(name, opt_name, value)
            except ValueError as e:
                self.poutput(
                    f"Warning: Couldn't set {opt_name} to {value} as specified in the configuration."
                )
                self.poutput(f"    {e}")
                invalid_options.append(opt_name)
                self.poutput(f"  {opt_name} will be removed from the configuration.")
        for x in invalid_options:
            del engine_conf.options[x]
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
        except OSError as e:
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
        engine_conf: EngineConf = EngineConf(path=args.path, protocol=args.protocol)
        self.engine_confs[args.name] = engine_conf
        try:
            self.load_engine(args.name)
            self.poutput(f"Successfully imported, loaded and selected {args.name}.")
        except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError):
            del self.engine_confs[args.name]
            self.poutput(f"Importing of the engine {engine_conf.path} failed.")

    def engine_quit(self, selected_engines: list[str], _args) -> None:
        for engine in selected_engines:
            self.loaded_engines[engine].quit()
            del self.loaded_engines[engine]
            self.poutput(f"Quitted {engine} without any problems.")

    def show_engine_option(self, engine: str, name: str) -> None:
        opt: chess.engine.Option = self.loaded_engines[engine].options[name]
        configured_val: Optional[Union[str, int, bool]] = self.engine_confs[
            engine
        ].options.get(name)
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
        if opt.type == "button":
            show_str += ": (button)"
        else:
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
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        if args.name in options:
            self.show_engine_option(engine, args.name)
        else:
            self.poutput(
                f"Error: {engine} has no option named {args.name}. Consider listing availlable options with `engine configure ls`."
            )

    def engine_config_ls(self, engine: str, args) -> None:
        conf: EngineConf = self.engine_confs[engine]
        for name, opt in self.loaded_engines[engine].options.items():
            if (args.configured and name not in conf.options) or (
                args.not_configured and name in conf.options
            ):
                continue
            if opt.is_managed() and not args.include_auto and name not in conf.options:
                continue
            if args.regex:
                try:
                    pattern: re.Pattern = re.compile(args.regex)
                except re.error as e:
                    self.poutput(
                        f'Error: Invalid regular expression "{args.regex}": {e}'
                    )
                    return
                if not pattern.fullmatch(name):
                    continue
            if args.type and (
                (opt.type == "check" and not "checkbox" in args.type)
                or opt.type == "combo"
                and not "combobox" in args.type
                or opt.type == "spin"
                and not "integer" in args.type
                or opt.type in ["button", "reset", "save"]
                and not "button" in args.type
                or opt.type in ["string", "file", "path"]
                and not "string" in args.type
            ):
                continue
            self.show_engine_option(engine, name)

    def set_engine_option(
        self, engine: str, name: str, value: Union[str, int, bool, None]
    ) -> None:
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        if name not in options:
            raise ValueError(
                f"{name} is not a name for an option for {engine}. You can list availlable options with `engine config ls`."
            )
        option: chess.engine.Option = options[name]
        if option.type in ["string", "file", "path"]:
            if not isinstance(value, str):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is {type(value)} which doesn't match very well."
                )
        elif option.type == "combo":
            if not isinstance(value, str):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is {type(value)} which doesn't match very well."
                )

            if not option.var:
                raise ValueError(
                    f"There are no valid alternatives for {option.name}, so you cannot set it to any value. It's strange I know, but I'm probably not the engine's author so I can't do much about it."
                )
            if value not in option.var:
                raise ValueError(
                    f"{value} is not a valid alternative for the combobox {option.name}. The list of valid options is: {repr(option.var)}."
                )
        elif option.type == "spin":
            if not isinstance(value, int):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is {type(value)} which doesn't match very well."
                )
            if option.min is not None and value < option.min:
                raise ValueError(
                    f"The minimum value for {option.name} is {option.min}, you specified {value}."
                )
            if option.max is not None and value > option.max:
                raise ValueError(
                    f"The maximum value for {option.name} is {option.max}, you specified {value}."
                )
        elif option.type == "check":
            if not isinstance(value, bool):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is {type(value)} which doesn't match very well."
                )
        elif option.type in ["button", "reset", "save"]:
            if value is not None:
                raise ValueError(
                    f"{name} is a button according to the engine but the given value is a {type(value)} which doesn't really make any sence."
                )
        else:
            assert False, f"Unsupported option type: {option.type}"
        self.loaded_engines[engine].configure({option.name: value})

    def engine_config_set(self, engine: str, args) -> None:
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        conf: EngineConf = self.engine_confs[engine]
        if args.name not in options:
            self.poutput(
                f"Error: {args.name} is not a name for an option for {engine}. You can list availlable options with `engine config ls`."
            )
            return
        option: chess.engine.Option = options[args.name]
        if option.type in ["string", "combo", "file", "path"]:
            value: Union[str, int, bool, None] = args.value
        elif option.type == "spin":
            try:
                value = int(args.value)
            except ValueError:
                self.poutput(
                    f"Invalid integer: {args.value}. Note: This option expects an integer and nothing else."
                )
                return
        elif option.type == "check":
            if args.value.lower() in ["true", "check"]:
                value = True
            elif args.value in ["false", "uncheck"]:
                value = False
            else:
                self.poutput(
                    f"{option.name} is a checkbox and can only be set to true/check or false/uncheck, but you set it to {args.value}. Please go ahead and correct your mistake."
                )
                return
        elif option.type in ["button", "reset", "save"]:
            if not args.value.lower() == "trigger-on-startup":
                self.poutput(
                    f"{option.name} is a button and buttons can only be configured to 'trigger-on-startup', (which means what it sounds like). If you want to trigger {option.name}, please go ahead and run `engine config trigger {option.name}` instead. Or you might just have made a typo when you entered this command, if so, go ahead and run `engine config set {option.name} trigger-on-startup`."
                )
                return
            if not args.temporary:
                conf.options[option.name] = None
            return
        else:
            assert False, f"Unsupported option type: {option.type}"
        if not args.temporary:
            conf.options[option.name] = value
        self.set_engine_option(engine, option.name, value)

    def engine_config_unset(self, engine: str, args) -> None:
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
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
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
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
                name: conf._asdict() for (name, conf) in self.engine_confs.items()
            }
            items = {"engine-configurations": engine_confs}
            toml.dump(items, f)

    analysis_argparser = cmd2.Cmd2ArgumentParser()
    analysis_argparser.add_argument(
        "-s",
        "--select",
        action="append",
        help="Select a different engine for this command. The option can be repeated to select multiple engines.",
    )
    analysis_subcmds = analysis_argparser.add_subparsers(dest="subcmd")
    analysis_start_argparser = analysis_subcmds.add_parser(
        "start", help="Start to analyse current position."
    )
    analysis_start_argparser.add_argument(
        "moves", nargs="*", help="List of moves to analysis."
    )
    analysis_start_argparser.add_argument(
        "-n", "--number-of-moves", type=int, default=3, help="Show the n best moves."
    )
    analysis_start_argparser.add_argument(
        "--time", type=float, help="Analyse only the given number of seconds."
    )
    analysis_start_argparser.add_argument(
        "--depth", type=int, help="Analyse until the given depth is reached."
    )
    analysis_start_argparser.add_argument(
        "--nodes", type=int, help="Search only the given number of nodes."
    )
    analysis_start_argparser.add_argument(
        "--mate",
        type=int,
        help="Search for a mate in the given number of moves and stop then.",
    )
    analysis_stop_argparser = analysis_subcmds.add_parser(
        "stop", help="Stop analysing."
    )
    analysis_ls_argparser = analysis_subcmds.add_parser("ls", help="List analysis.")
    analysis_ls_argparser.add_argument(
        "-v", "--verbose", action="store_true", help="Print out more info."
    )
    analysis_ls_group = analysis_ls_argparser.add_mutually_exclusive_group()
    analysis_ls_group.add_argument(
        "-r", "--running", action="store_true", help="List only running analysis."
    )
    analysis_ls_group.add_argument(
        "-s", "--stopped", action="store_true", help="List only stopped analysis."
    )
    analysis_show_argparser = analysis_subcmds.add_parser(
        "show", help="Show all analysis performed at the current move."
    )
    analysis_rm_argparser = analysis_subcmds.add_parser(
        "rm",
        aliases="remove",
        help="Remove analysis made by the selected engines at this move. Useful if you want to rerun the analysis.",
    )
    analysis_auto_argparser = analysis_subcmds.add_parser(
        "auto", help="Start or stop automatic analysis at the current move."
    )
    analysis_auto_subcmds = analysis_auto_argparser.add_subparsers(dest="auto_subcmd")
    analysis_auto_start_argparser = analysis_auto_subcmds.add_parser(
        "start",
        help="Begin to auto analyse the current move (as it changes) with the currently selected engines.",
    )
    analysis_auto_start_argparser.add_argument(
        "-n",
        "--number-of-moves",
        type=int,
        default=5,
        help="Number of moves to analyse at every position.",
    )
    analysis_auto_stop_argparser = analysis_auto_subcmds.add_parser(
        "stop", help="Stop auto analysis."
    )
    analysis_auto_stop_argparser.add_argument(
        "-c",
        "--current",
        action="store_true",
        help="Stop the running analysis at the current move as well. Otherwise they'll continue running.",
    )
    analysis_auto_subcmds.add_parser(
        "ls", aliases=["list"], help="List the auto analysing engines."
    )

    @cmd2.with_argparser(analysis_argparser)  # type: ignore
    def do_analysis(self, args) -> None:
        """Manage analysis."""
        if args.subcmd == "ls":
            self.analysis_ls(args)
        elif args.subcmd == "show":
            self.analysis_show(args)
        elif args.subcmd == "auto" and args.auto_subcmd == "stop":
            self.analysis_auto_stop(args)
        elif args.subcmd == "auto" and args.auto_subcmd in ["ls", "list"]:
            self.analysis_auto_ls(args)
        else:
            if args.select:
                for engine in args.select:
                    if engine not in self.loaded_engines:
                        if engine in self.engine_confs:
                            self.poutput(
                                f"Error: {engine} is not loaded. You can try to load it by running `engine load {engine}`."
                            )
                        else:
                            self.poutput(
                                f"Error: There is no engine named {engine}. You can list all availlable engines with `engine ls` or import an engine with the `engine import` command."
                            )
                        return
                selected_engines: list[str] = args.select
            elif self.selected_engines:
                selected_engines = self.selected_engines
            else:
                self.poutput(
                    "Error: No engines selected. Consider selecting one with the `engine select` command."
                )
                return
            if args.subcmd == "start":
                self.analysis_start(selected_engines, args)
            elif args.subcmd == "stop":
                self.analysis_stop(selected_engines, args)
            elif args.subcmd in ["rm", "remove"]:
                self.analysis_rm(selected_engines, args)
            elif args.subcmd == "auto" and args.auto_subcmd == "start":
                self.analysis_auto_start(selected_engines, args)
            else:
                assert False, "Invalid command."

    def start_analysis(
        self,
        engine: str,
        number_of_moves: int,
        root_moves: list[chess.Move] = [],
        limit: Optional[chess.engine.Limit] = None,
    ) -> None:
        if engine in self.running_analysis:
            self.stop_analysis(engine)
        analysis: Analysis = Analysis(
            result=self.loaded_engines[engine].analysis(
                self.game_node.board(),
                root_moves=root_moves if root_moves != [] else None,
                limit=limit,
                multipv=number_of_moves,
                game="this",
            ),
            engine=engine,
            board=self.game_node.board(),
            san=(
                self.game_node.san()
                if isinstance(self.game_node, chess.pgn.ChildNode)
                else None
            ),
        )
        self.analysis.append(analysis)
        self.running_analysis[engine] = analysis
        self.analysis_by_node[self.game_node][engine] = analysis

    def analysis_start(self, selected_engines: list[str], args) -> None:
        intersection: set[str] = set(
            self.analysis_by_node[self.game_node].keys()
        ).intersection(set(selected_engines))
        if intersection:
            the_engine: str = intersection.pop()
            self.poutput(
                f"Error: There's allready an analysis made by {the_engine} at this move. Please select some other engine or remove it with `analysis rm {the_engine}`."
            )
            return
        root_moves: list[chess.Move] = []
        board: chess.Board = self.game_node.board()
        for san in args.moves:
            try:
                root_moves.append(board.parse_san(san))
            except ValueError as e:
                self.poutput(f"Error: {san} is not a valid move in this position: {e}")
                return
        limit = chess.engine.Limit(
            time=args.time, depth=args.depth, nodes=args.nodes, mate=args.mate
        )
        for engine in selected_engines:
            self.start_analysis(engine, args.number_of_moves, root_moves, limit)
            self.poutput(f"{engine} is now analysing.")

    def stop_analysis(self, engine: str) -> None:
        self.running_analysis[engine].result.stop()
        del self.running_analysis[engine]

    def analysis_stop(self, selected_engines: list[str], args) -> None:
        for engine in selected_engines:
            if engine not in self.running_analysis:
                self.poutput(
                    f"Error: {engine} is currently not running any analysis so nothing could be stopped."
                )
                return
            self.stop_analysis(engine)

    def update_auto_analysis(self) -> None:
        for engine in self.auto_analysis_engines:
            self.start_analysis(engine, self.auto_analysis_number_of_moves)

    def analysis_auto_start(self, selected_engines: list[str], args) -> None:
        self.auto_analysis_engines = selected_engines
        self.auto_analysis_number_of_moves = args.number_of_moves
        self.update_auto_analysis()

    def analysis_auto_stop(self, args) -> None:
        if args.current:
            for engine in self.auto_analysis_engines:
                self.stop_analysis(engine)
        self.auto_analysis_engines = []

    def analysis_auto_ls(self, args) -> None:
        if not self.auto_analysis_engines:
            self.poutput("There are no auto analysing engines.")
        else:
            for engine in self.auto_analysis_engines:
                self.poutput(engine)

    def show_analysis(self, analysis: Analysis, verbose: bool = False) -> None:
        show_str: str = analysis.engine + " @ "
        if analysis.san is not None:
            show_str += f"{MoveNumber.last(analysis.board)} {analysis.san}: "
        else:
            show_str += "starting position: "

        def score_and_wdl_str(info: chess.engine.InfoDict) -> str:
            res: str = ""
            if "pv" in info and info["pv"]:
                res += f"{analysis.board.san(info['pv'][0])}: "
            if "score" in info:
                score: chess.engine.Score = info["score"].relative
                res += score_str(score) + ", "
            if "wdl" in info:
                wdl: chess.engine.Wdl = info["wdl"].relative
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
                    "score",
                    "pv",
                    "multipv",
                    "currmove",
                    "currmovenumber",
                    "wdl",
                    "string",
                ]:
                    show_str += f"{key}: {val}, "
            for i, info in enumerate(analysis.result.multipv, 1):
                show_str += f"\n  {i}: {score_and_wdl_str(info)}"
                if info.get("pv") and len(info["pv"]) >= 2:
                    show_str += f"\n    {analysis.board.variation_san(info['pv'])}"
        self.poutput(show_str)

    def analysis_ls(self, args) -> None:
        for analysis in self.analysis:
            if args.running and not (
                analysis.engine in self.running_analysis
                and analysis == self.running_analysis[analysis.engine]
            ):
                continue
            if args.stopped and (
                analysis.engine in self.running_analysis
                and analysis == self.running_analysis[analysis.engine]
            ):
                continue
            self.show_analysis(analysis, verbose=args.verbose)

    def analysis_show(self, args) -> None:
        if not self.analysis_by_node[self.game_node]:
            self.poutput("No analysis at this move.")
            return
        for engine, analysis in self.analysis_by_node[self.game_node].items():
            self.poutput(f"({engine}): ", end="")
            self.show_analysis(analysis, verbose=True)

    def analysis_rm(self, selected_engines: list[str], _args) -> None:
        for engine in selected_engines:
            try:
                removed = self.analysis_by_node[self.game_node].pop(engine)
            except KeyError:
                continue
            if removed in self.running_analysis:
                self.stop_analysis(engine)
            self.analysis.remove(removed)
            self.poutput(f"Removed analysis made by {engine}.")

    game_argparser = cmd2.Cmd2ArgumentParser()
    game_subcmds = game_argparser.add_subparsers(dest="subcmd")
    game_ls_argparser = game_subcmds.add_parser("ls", help="List all games.")
    game_rm_argparser = game_subcmds.add_parser(
        "rm", aliases=["remove"], help="Remove a game."
    )
    game_rm_argparser.add_argument(
        "name", nargs="?", help="Name of game to remove. Defaults to the current game."
    )
    game_goto_argparser = game_subcmds.add_parser(
        "goto", aliases=["gt"], help="Goto a game."
    )
    game_goto_argparser.add_argument(
        "name", help="Name of the game to goto. List all games with `game ls`."
    )

    @cmd2.with_argparser(game_argparser)  # type: ignore
    def do_game(self, args) -> None:
        "Switch between, delete or create new games."
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

    save_argparser = cmd2.Cmd2ArgumentParser()
    save_argparser.add_argument(
        "file", nargs="?", help="File to save to. Defaults to the loaded file."
    )

    @cmd2.with_argparser(save_argparser)  # type: ignore
    def do_save(self, args) -> None:
        "Save the current game to a file."
        file_name = args.file or self.file_names.get(self.current_game)
        if file_name is None:
            self.poutput(
                f"Error: This game '{self.current_game}' isn't associated with any file name. Please provide a file name yourself."
            )
            return
        try:
            with open(file_name, mode="w") as file:
                print(self.game_node.game(), file=file)
        except OSError as e:
            self.poutput(f"Error: with {file_name}: {e}")
            return
        self.file_names[self.current_game] = file_name

    promote_argparser = cmd2.Cmd2ArgumentParser()
    promote_group = promote_argparser.add_mutually_exclusive_group()
    promote_group.add_argument(
        "-m",
        "--main",
        action="store_true",
        help="Promote this move to be main variation.",
    )
    promote_group.add_argument(
        "-n", "--steps", type=int, help="Promote this variation n number of steps."
    )

    @cmd2.with_argparser(promote_argparser)  # type: ignore
    def do_promote(self, args) -> None:
        "If current move is a side line, promote it so that it'll be closer to main variation."
        if not isinstance(self.game_node, chess.pgn.ChildNode):
            return
        if args.main:
            self.game_node.parent.variations.remove(self.game_node)
            self.game_node.parent.variations.insert(0, self.game_node)
        else:
            n = args.steps or 1
            for _ in range(n):
                self.game_node.parent.promote(self.game_node)

    demote_argparser = cmd2.Cmd2ArgumentParser()
    demote_group = demote_argparser.add_mutually_exclusive_group()
    demote_group.add_argument(
        "-l",
        "--last",
        action="store_true",
        help="Demote this move to be the last variation.",
    )
    demote_group.add_argument(
        "-n", "--steps", type=int, help="Demote this variation n number of steps."
    )

    @cmd2.with_argparser(demote_argparser)  # type: ignore
    def do_demote(self, args) -> None:
        "If current move is the main variation or if it isn't the last variation, demote it so it'll be far from the main variation."
        if not isinstance(self.game_node, chess.pgn.ChildNode):
            return
        if args.last:
            self.game_node.parent.variations.remove(self.game_node)
            self.game_node.parent.variations.append(self.game_node)
        else:
            n = args.steps or 1
            for _ in range(n):
                self.game_node.parent.demote(self.game_node)

    def show_variations(self, node: chess.pgn.GameNode) -> None:
        next = node.next()
        if next is not None:
            show_items = [move_str(next, include_sideline_arrows=False)]
            for variation in node.variations[1:]:
                show_items.append(
                    move_str(
                        variation,
                        include_move_number=False,
                        include_sideline_arrows=False,
                    )
                )
            self.poutput(", ".join(show_items))

    def do_variations(self, _) -> None:
        "Print all variations following this move."
        self.show_variations(self.game_node)

    def do_sidelines(self, _) -> None:
        "Show all sidelines to this move."
        if self.game_node.parent is not None:
            self.show_variations(self.game_node.parent)

    def engine_log(self, args) -> None:
        if args.log_subcmd == "clear":
            self.engines_saved_log.clear()
            try:
                while True:
                    self.engines_log_queue.get_nowait()
            except queue.Empty:
                pass
        elif args.log_subcmd == "show":
            try:
                while True:
                    self.engines_saved_log.append(self.engines_log_queue.get_nowait())
            except queue.Empty:
                pass
            for line in self.engines_saved_log:
                self.poutput(line)
        else:
            assert False, "Unrecognized command."


def run():
    argparser = argparse.ArgumentParser(
        description="A repl to edit and analyse chess games."
    )
    argparser.add_argument("pgn_file", nargs="?", help="Open the given pgn file.")
    args = argparser.parse_args()
    sys.exit(ChessCli(file_name=args.pgn_file).cmdloop())
