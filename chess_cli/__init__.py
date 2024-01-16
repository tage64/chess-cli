from . import base
from . import nags

import argparse
from collections import deque, defaultdict
import contextlib
import copy
import datetime
import logging
import logging.handlers
import os
import platform
import queue
import re
import shutil
import sys
import tempfile
import threading
from typing import *
import urllib.request

import appdirs  # type: ignore
import chess
import chess.engine
import chess.pgn
import chess.svg
import cmd2
import more_itertools
import psutil
import toml  # type: ignore

__version__ = "0.1.0"

MOVE_NUMBER_REGEX: re.Pattern[str] = re.compile("(\d+)((\.{3})|\.?)")
COMMANDS_IN_COMMENTS_REGEX: re.Pattern[str] = re.compile("\[%.+?\]")


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
    # The directory where the engine is installed.
    # This will be removed if the engine is removed.
    install_dir: Optional[str] = None


class Analysis(NamedTuple):
    "Information about analysis."

    result: chess.engine.SimpleAnalysisResult
    engine: str
    board: chess.Board
    san: Optional[str]


def sizeof_fmt(num, suffix="B"):
    "Print byte size with correct prefix."
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


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
        if comment_text(game_node.starting_comment):
            res += "-"
        res += game_node.san()
        if game_node.nags:
            nag_strs = [nags.ascii_glyph(nag) for nag in game_node.nags]
            if len(nag_strs) == 1:
                res += nag_strs[0]
            else:
                res += f"[{', '.join(nag_strs)}]"
    if (
        comment_text(game_node.comment)
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


class GameHandle(NamedTuple):
    "The headers of a game together with either the offset of the game in a file or a node in the parsed game."
    headers: chess.pgn.Headers
    offset_or_game: int | chess.pgn.GameNode

    @property
    def offset(self) -> Optional[int]:
        if isinstance(self.offset_or_game, int):
            return self.offset_or_game
        return None

    @property
    def game_node(self) -> Optional[chess.pgn.GameNode]:
        if isinstance(self.offset_or_game, chess.pgn.GameNode):
            return self.offset_or_game
        return None


class CommandFailure(cmd2.exceptions.SkipPostcommandHooks):
    "An exception raised when a command fails and doesn't perform any updates to the game."
    pass


class ChessCli(cmd2.Cmd):
    """A repl to edit and analyse chess games."""

    def __init__(
        self, file_name: Optional[str] = None, config_file: Optional[str] = None
    ):
        self.init_cmd2()
        self.init_engines()
        self.load_config(config_file)
        self.init_analysis()
        self.init_games(file_name)
        self.set_prompt(None)  # type: ignore

    def init_cmd2(self) -> None:
        # Set cmd shortcuts
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts, include_py=True, allow_cli_args=False)
        self.self_in_py = True

        # Close engines when REPL is quit.
        self.register_postloop_hook(self.close_engines)  # type: ignore

        def update_auto_analysis(
            x: cmd2.plugin.PostcommandData,
        ) -> cmd2.plugin.PostcommandData:
            self.update_auto_analysis()
            return x

        self.register_postcmd_hook(update_auto_analysis)
        self.register_postcmd_hook(self.set_prompt)

    def init_engines(self) -> None:
        self.engine_confs: dict[str, EngineConf] = {}
        self.loaded_engines: dict[str, chess.engine.SimpleEngine] = {}
        self.engines_saved_log: deque[str] = deque()
        self.engines_log_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        log_handler = logging.handlers.QueueHandler(self.engines_log_queue)
        log_handler.setLevel(logging.WARNING)
        log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        chess.engine.LOGGER.addHandler(log_handler)

        # A list of the currently selected engines.
        self.selected_engine: Optional[str] = None
        self.running_analysis: dict[str, Analysis] = dict()

    def load_config(self, config_file: Optional[str]) -> None:
        self.config_file: str = config_file or os.path.join(
            appdirs.user_config_dir("chess-cli"), "config.toml"
        )
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
        else:
            if config_file is not None:
                self.poutput(f"Warning: Couldn't find config file at '{config_file}'.")

    def init_analysis(self) -> None:
        self.analysis: list[Analysis] = []
        self.analysis_by_node: defaultdict[
            chess.pgn.GameNode, dict[str, Analysis]
        ] = defaultdict(dict)
        self.auto_analysis_engines: Set[str] = set()

    def init_games(self, file_name: Optional[str]) -> None:
        # A list of all opened games.
        self.games: list[GameHandle] = []

        self.pgn_file: Optional[TextIO] = None
        self.current_game: int = 0
        if file_name is not None:
            self.load_games(file_name)
        else:
            self.add_new_game()

    def load_games(self, file_name: str) -> None:
        try:
            with open(file_name) as pgn_file:
                games: list[GameHandle] = []
                while True:
                    offset: int = pgn_file.tell()
                    headers = chess.pgn.read_headers(pgn_file)
                    if headers is None:
                        break
                    games.append(GameHandle(headers, offset))
                if not games:
                    self.poutput(f"Error: Couldn't find any game in {file_name}")
                    raise CommandFailure()
                # Reset analysis.
                self.stop_engines()
                self.init_analysis()
                self.games = games
                self.pgn_file = pgn_file
                self.select_game(0)
                self.poutput(f"Successfully loaded {len(self.games)} game(s).")
        except OSError as ex:
            self.poutput(f"Error: Loading of {file_name} failed: {ex}")

    def stop_engines(self) -> None:
        "Stop all running analysis."
        for _, analysis in self.running_analysis.items():
            analysis.result.stop()
        self.running_analysis.clear()

    def close_engines(self) -> None:
        "Stop and quit all engines."
        self.stop_engines()
        for engine in self.loaded_engines.values():
            engine.quit()
        self.loaded_engines.clear()

    def select_game(self, idx: int) -> None:
        assert 0 <= idx < len(self.games)
        game_handle = self.games[idx]
        if game_handle.offset is not None:
            assert self.pgn_file is not None
            self.pgn_file.seek(game_handle.offset)
            game_node = chess.pgn.read_game(self.pgn_file)
            assert game_node is not None
            self.games[idx] = GameHandle(game_node.headers, game_node)
        assert self.games[idx].game_node is not None
        self.current_game = idx

    def add_new_game(self, idx: Optional[int] = None) -> None:
        game: chess.pgn.Game = chess.pgn.Game()
        game_handle: GameHandle = GameHandle(game.headers, game)
        if idx is None:
            self.games.append(game_handle)
            self.current_game = len(self.games) - 1
        else:
            self.games.insert(idx, game_handle)
            self.current_game = idx

    @property
    def game_node(self) -> chess.pgn.GameNode:
        x = self.games[self.current_game].game_node
        assert x is not None
        return x

    @game_node.setter
    def game_node(self, val: chess.pgn.GameNode) -> None:
        self.games[self.current_game] = GameHandle(val.game().headers, val)

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
        help="If a variation already exists from the current move, add this new variation as the main line rather than a side line.",
    )
    play_argparser.add_argument(
        "-s",
        "--sideline",
        action="store_true",
        help="Add this new list of moves as a sideline to the current move.",
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

    game_argparser = cmd2.Cmd2ArgumentParser()
    game_argparser.add_argument(
        "-a", "--all", action="store_true", help="Print the entire game from the start."
    )

    @cmd2.with_argparser(game_argparser)  # type: ignore
    def do_game(self, args) -> None:
        "Print the rest of the game with sidelines and comments in a nice and readable format."
        if args.all:
            self.onecmd("moves -s -r -c")
        else:
            self.onecmd("moves -s -r -c --fc")

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
        if args._from is not None:
            # If the user has specified a given move as start.
            node = self.find_move(
                args._from,
                search_sidelines=args.sidelines,
                recurse_sidelines=args.recurse,
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
                args.to,
                search_sidelines=args.sidelines,
                recurse_sidelines=args.recurse,
                break_search_backwards_at=lambda x: x is start_node,
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

        for i, node in enumerate(moves):
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
                                    move_str(
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

    def show_evaluation(self) -> Optional[str]:
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
        text: str = "  a b c d e f g h  \n"
        for row in range(7, -1, -1):
            text += f"{row + 1} "
            for col in range(0, 8):
                try:
                    square_content: str = str(
                        self.game_node.board().piece_map()[8 * row + col]
                    )
                except KeyError:
                    square_content = "-"
                text += f"{square_content} "
            text += f"{row + 1}\n"
        text += "  a b c d e f g h  \n"
        return text

    def show_arrows(self) -> Optional[str]:
        arrows: list = self.game_node.arrows()
        if not arrows:
            return None
        return str(
            [
                f"{arrow.color} {chess.square_name(arrow.tail)}->{chess.square_name(arrow.head)}"
                for arrow in self.game_node.arrows()
            ]
        )

    def show_clock(self) -> Optional[str]:
        clock = self.game_node.clock()
        if clock is None:
            return None
        return str(datetime.timedelta(seconds=clock)).strip("0")

    show_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(show_argparser)  # type: ignore
    def do_show(self, args) -> None:
        "Show position, comments, NAGs and more about the current move."
        self.poutput(f"FEN: {self.show_fen()}")
        self.poutput(f"\n{self.show_board()}")
        starting_comment: str = comment_text(self.game_node.starting_comment)
        if isinstance(self.game_node, chess.pgn.ChildNode) and starting_comment:
            self.poutput(starting_comment)
            self.poutput(
                f"    {MoveNumber.last(self.game_node)} {self.game_node.san()}"
            )
        comment: str = comment_text(self.game_node.comment)
        if comment:
            self.poutput(comment)
        for nag in self.show_nags():
            self.poutput("NAG: {nag}")
        evaluation: Optional[str] = self.show_evaluation()
        if evaluation is not None:
            self.poutput("Evaluation: {evaluation}")
        arrows: Optional[str] = self.show_arrows()
        if arrows is not None:
            self.poutput(f"Arrows: {arrows}")
        clock: Optional[str] = self.show_clock()
        if clock is not None:
            self.poutput(f"Clock: {clock}")

    fen_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(fen_argparser)  # type: ignore
    def do_fen(self, args) -> None:
        "Show the position as FEN (Forsynth-Edwards Notation)."
        self.poutput(self.show_fen())

    board_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(board_argparser)  # type: ignore
    def do_board(self, args) -> None:
        "Show the current position as an ASCII chess board."
        self.poutput(self.show_board())

    comment_argparser = cmd2.Cmd2ArgumentParser()
    comment_argparser.add_argument(
        "-s",
        "--starting-comment",
        action="store_true",
        help="If this move is starting a new variation, act on the starting comment of that variation.",
    )
    comment_argparser.add_argument(
        "-r",
        "--raw",
        action="store_true",
        help="Act on the raw pgn comment which may override embedded commands like arrows and clocks.",
    )
    comment_subcmds = comment_argparser.add_subparsers(dest="subcmd")
    comment_subcmds.add_parser("show", help="Show the comment at the current move.")
    comment_subcmds.add_parser("rm", help="Remove the comment at the current move.")
    comment_subcmds.add_parser("edit", help="Open the comment in your editor.")
    comment_set_argparser = comment_subcmds.add_parser(
        "set", help="Set the comment for this move."
    )
    comment_set_argparser.add_argument("comment", help="The new text.")
    comment_append_argparser = comment_subcmds.add_parser(
        "append", help="Append text to the already existing comment."
    )
    comment_append_argparser.add_argument("comment", help="The text to append.")

    @cmd2.with_argparser(comment_argparser)  # type: ignore
    def do_comment(self, args) -> None:
        "Show, edit or remove the comment at the current move."

        if args.starting_comment and not self.game_node.starts_variation():
            self.poutput(
                f"Error: Starting comments can only exist on moves that starts a variation."
            )
            return

        comment: str = (
            self.game_node.comment
            if not args.starting_comment
            else self.game_node.starting_comment
        )
        comment = comment if args.raw else comment_text(comment)

        def set_comment(new_comment: str) -> None:
            new_comment = (
                new_comment if args.raw else update_comment_text(comment, new_comment)
            )
            new_comment = new_comment.strip()
            if args.starting_comment:
                self.game_node.starting_comment = new_comment
            else:
                self.game_node.comment = new_comment

        match args.subcmd:
            case "show" | None:
                self.poutput(comment)
            case "rm":
                set_comment("")
            case "set":
                set_comment(args.comment)
            case "append":
                set_comment(comment + " " + args.comment)
            case "edit":
                fd, file_name = tempfile.mkstemp(suffix=".txt", text=True)
                try:
                    with os.fdopen(fd, mode="w") as file:
                        file.write(comment)
                        file.flush()
                    self.poutput(f"Opening {file_name} in your editor.")
                    self.onecmd(f"edit '{file_name}'")
                    with open(file_name, mode="r") as file:
                        file.seek(0)
                        new_comment: str = file.read().strip()
                        set_comment(new_comment)
                        self.poutput("Successfully updated comment.")
                finally:
                    os.remove(file_name)
            case _:
                assert False, "Unknown subcommand."

    nag_argparser = cmd2.Cmd2ArgumentParser()
    nag_subcmds = nag_argparser.add_subparsers(dest="subcmd")
    nag_subcmds.add_parser("show", help="Show the NAGs at this move.")
    nag_add_argparser = nag_subcmds.add_parser(
        "add", help="Add a nag (numeric annotation glyph) to this move."
    )
    nag_add_argparser.add_argument(
        "nag",
        help="NAG: either a number like '$17' or an ascii glyph like '!' or '?!'.",
    )
    nag_rm_argparser = nag_subcmds.add_parser("rm", help="Remove an NAG at this move.")
    nag_rm_argparser.add_argument(
        "nag", help="NAG: either a number like '$17' or an ascii glyph like '!'."
    )
    nag_subcmds.add_parser("clear", help="Clear all NAGs at this move.")

    @cmd2.with_argparser(nag_argparser)  # type: ignore
    def do_nag(self, args) -> None:
        "Show, edit or remove NAGs (numeric annotation glyphs, E.G. '!?') at the current move."
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
                self.poutput(
                    f"Set NAG ({nags.ascii_glyph(nag)}): {nags.description(nag)}."
                )
            case "rm":
                try:
                    nag = nags.parse_nag(args.nag)
                except ValueError as e:
                    self.poutput(f"Error: invalid NAG {args.nag}: {e}")
                    return
                try:
                    self.game_node.nags.remove(nag)
                except KeyError:
                    self.poutput(
                        f"Error: NAG '{nags.ascii_glyph(nag)}' was not set on this move."
                    )
            case "clear":
                self.game_node.nags = set()
            case _:
                assert False, "Unknown subcommand."

    evaluation_argparser = cmd2.Cmd2ArgumentParser()
    evaluation_subcmds = evaluation_argparser.add_subparsers(dest="subcmd")
    evaluation_show_argparser = evaluation_subcmds.add_parser(
        "show",
        help="Show the evaluation at this move. (Note that this is the evaluation stored in the pgn comment and might neither come from an engine nore be correct.",
    )
    evaluation_rm_argparser = evaluation_subcmds.add_parser(
        "rm", help="Remove the evaluation at this move."
    )
    evaluation_set_argparser = evaluation_subcmds.add_parser(
        "set", help="Set an evaluation for this move."
    )
    evaluation_set_group = evaluation_set_argparser.add_mutually_exclusive_group(
        required=True
    )
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

    @cmd2.with_argparser(evaluation_argparser)  # type: ignore
    def do_evaluation(self, args) -> None:
        "Show, edit or remove evaluations at the current move."
        match args.subcmd:
            case "show":
                text = self.show_evaluation()
                if text is not None:
                    self.poutput(text)
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
                assert False, "Unknown subcommand."

    arrow_argparser = cmd2.Cmd2ArgumentParser()
    arrow_subcmds = arrow_argparser.add_subparsers(dest="subcmds")
    arrow_subcmds.add_parser("show", help="Show all arrows on the board.")
    arrow_subcmds.add_parser("clear", help="Clear all arrows on the board.")
    arrow_rm_argparser = arrow_subcmds.add_parser(
        "rm", help="Remove all arrows between two squares."
    )
    arrow_rm_argparser.add_argument(
        "_from",
        type=chess.parse_square,
        help="The square from which the arrow is drawn.",
    )
    arrow_rm_argparser.add_argument(
        "to", type=chess.parse_square, help="The square which the arrow is pointing to."
    )
    arrow_add_argparser = arrow_subcmds.add_parser(
        "add", help="Draw an arrow on the board."
    )
    arrow_add_argparser.add_argument(
        "_from",
        type=chess.parse_square,
        help="The square from which the arrow is drawn.",
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

    @cmd2.with_argparser(arrow_argparser)  # type: ignore
    def do_arrow(self, args) -> None:
        "Show, edit or remove arrows at the current move."

        color_abbreviations: dict[str, str] = {
            "g": "green",
            "y": "yellow",
            "r": "red",
            "b": "blue",
        }

        match args.subcmd:
            case "show":
                text = self.show_arrows()
                if text is not None:
                    self.poutput(text)
            case "add":
                if args.color in color_abbreviations:
                    color = color_abbreviations[args.color]
                else:
                    color = args.color
                self.game_node.set_arrows(
                    self.game_node.arrows()
                    + [chess.svg.Arrow(args._from, args.to, color=color)]
                )
            case "rm":
                self.game_node.set_arrows(
                    (
                        arr
                        for arr in self.game_node.arrows()
                        if not (args._from == arr.tail or args.to == arr.head)
                    )
                )
            case _:
                assert False, "Unknown subcommand."

    clock_argparser = cmd2.Cmd2ArgumentParser()
    clock_subcmds = clock_argparser.add_subparsers(dest="subcmd")
    clock_subcmds.add_parser(
        "show", help="Show the remaining time for the player making this move."
    )
    clock_subcmds.add_parser("rm", help="Remove the clock information at this move.")
    clock_set_argparser = clock_subcmds.add_parser(
        "set", help="Set the remaining time for the player making this move."
    )
    clock_set_argparser.add_argument("time", help="Remaining time.")

    @cmd2.with_argparser(clock_argparser)  # type: ignore
    def do_clock(self, args) -> None:
        "Show, edit or remove clock information at the current move."
        match args.subcmd:
            case "show":
                text = self.show_clock()
                if text is not None:
                    self.poutput(text)
            case "rm":
                self.game_node.set_clock(None)
            case "set":
                time_parsed = re.fullmatch(
                    "(\d+)(:(\d+))?(:(\d+))?([.,](\d+))?", args.time
                )
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
                assert False, "Unhandled subcommand."

    def find_move(
        self,
        move_str: str,
        search_sidelines: bool,
        recurse_sidelines: bool,
        search_forwards: bool = True,
        search_backwards: bool = True,
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
            if node is self.game_node:
                return False
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
                if (
                    node.is_main_variation()
                    or recurse_sidelines
                    or node is current_node
                ):
                    if search_sidelines:
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
        help="A move, move number or both. E.G. 'e4', '8...' or '9.dxe5+'. Or the"
        "string 'start'/'s' or 'end'/'e' for jumping to the start or end of the game.",
    )
    goto_sidelines_group = goto_argparser.add_mutually_exclusive_group()
    goto_sidelines_group.add_argument(
        "-r",
        "--recurse",
        action="store_true",
        help="Search sidelines recursively for the move.",
    )
    goto_sidelines_group.add_argument(
        "-m",
        "--mainline",
        action="store_true",
        help="Only search along the mainline and ignore all sidelines.",
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
        match args.move:
            case "s" | "start":
                self.game_node = self.game_node.game()
            case "e" | "end":
                self.game_node = self.game_node.end()
            case move:
                node = self.find_move(
                    move,
                    search_sidelines=not args.mainline,
                    recurse_sidelines=args.recurse,
                    search_forwards=not args.backwards_only,
                    search_backwards=not args.forwards_only,
                )
                if node is None:
                    self.poutput(f"Error: Couldn't find the move {move}")
                    return
                self.game_node = node

    delete_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(delete_argparser)  # type: ignore
    def do_delete(self, _args) -> None:
        "Delete the current move."
        if isinstance(self.game_node, chess.pgn.ChildNode):
            parent = self.game_node.parent
            new_node = parent
            for i, node in enumerate(parent.variations):
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

    engine_argparser = cmd2.Cmd2ArgumentParser()
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
        "-l", "--loaded", action="store_true", help="List only loaded engines."
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
    engine_rm_argparser = engine_subcmds.add_parser(
        "rm", aliases=["remove"], help="Remove an engine."
    )
    engine_rm_argparser.add_argument("engine", help="Name of engine to remove.")
    engine_install_argparser = engine_subcmds.add_parser(
        "install", help="Automaticly download and import some common engines."
    )
    engine_install_argparser.add_argument(
        "engine", choices=["stockfish", "lc0"], help="Which engine to install."
    )
    engine_quit_argparser = engine_subcmds.add_parser(
        "quit", help="Quit all selected engines."
    )
    engine_select_argparser = engine_subcmds.add_parser(
        "select",
        help="Select a loaded engine. The selected engine will be used for commands like `analysis start` or `engine config`.",
    )
    engine_select_argparser.add_argument("engine", help="Engine to select.")
    engine_config_argparser = engine_subcmds.add_parser(
        "config",
        aliases=["conf", "configure"],
        help="Set values for or get current values of different engine specific parameters.",
    )
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
        "-c",
        "--configured",
        action="store_true",
        help="Only list options that are already configured in some way.",
    )
    engine_config_ls_configured_group.add_argument(
        "-n",
        "--not-configured",
        action="store_true",
        help="Only list options that are not configured.",
    )
    engine_config_ls_argparser.add_argument(
        "--include-auto",
        "--include-automatically-managed",
        action="store_true",
        help="By default, some options like MultiPV or Ponder are managed automatically. There is no reason to change them so they are hidden by default. This option makes them vissable.",
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
        help="Set the value in the running engine but don't store it in the engine's configuration.",
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
        help="Unset the value in the running engine but keep it in the engine's configuration.",
    )
    engine_config_trigger_argparser = engine_config_subcmds.add_parser(
        "trigger", help="Trigger an option of type button."
    )
    engine_config_trigger_argparser.add_argument(
        "name", help="Name of the option to trigger."
    )
    engine_log_argparser = engine_subcmds.add_parser(
        "log", help="Show the logged things (like stderr) from the selected engine."
    )
    engine_log_subcmds = engine_log_argparser.add_subparsers(dest="log_subcmd")
    engine_log_subcmds.add_parser("clear", help="Clear the log.")
    engine_log_subcmds.add_parser("show", help="Show the log.")

    @cmd2.with_argparser(engine_argparser)  # type: ignore
    def do_engine(self, args: Any) -> None:
        "Everything related to chess engines. See subcommands for detailes"
        match args.subcmd:
            case "ls":
                self.engine_ls(args)
            case "import":
                self.engine_import(args)
            case "load":
                self.engine_load(args)
            case "rm" | "remove":
                self.engine_rm(args)
            case "install":
                self.engine_install(args)
            case "select":
                self.engine_select(args)
            case "log":
                self.engine_log(args)
            case "conf" | "config" | "configure":
                self.engine_config(args)
            case "quit":
                self.engine_quit(args)
            case _:
                assert False, "Unsupported subcommand."

    def engine_select(self, args) -> None:
        if args.engine not in self.loaded_engines:
            if args.engine in self.engine_confs:
                self.poutput(
                    f"Error: {args.engine} is not loaded. You can try to load it by running `engine load {args.engine}`."
                )
            else:
                self.poutput(
                    f"Error: There is no engine named {args.engine}. You can list all availlable engines with `engine ls -a`, import an engine with the `engine import` command, or install an engine with `engine install ...`."
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
            self.poutput(f"    Executable: {conf.path}")
            if name in self.loaded_engines:
                engine: chess.engine.SimpleEngine = self.loaded_engines[name]
                for key, val in engine.id.items():
                    if not key == "name":
                        self.poutput(f"   {key}: {val}")

    def engine_ls(self, args) -> None:
        if args.loaded:
            engines: Iterable[str] = self.loaded_engines.keys()
        else:
            engines = self.engine_confs.keys()
        for engine in engines:
            self.show_engine(engine, verbose=args.verbose)

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
        self.selected_engine = name
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
        self.save_engine_config()

    def engine_rm(self, args) -> None:
        if args.engine not in self.engine_confs:
            self.poutput(
                f"Error: There is no engine named {args.engine}, list all engines with `engine ls`."
            )
            return
        if args.engine in self.loaded_engines:
            self.poutput(
                f"Error: {args.engine} is loaded, please quit it before removing it."
            )
            return
        removed: EngineConf = self.engine_confs.pop(args.engine)
        if removed.install_dir is not None:
            shutil.rmtree(removed.install_dir)
        self.poutput(f"Successfully removed {args.engine}")

    def engine_install(self, args) -> None:
        match args.engine:
            case "stockfish":
                self.install_stockfish()
            case "lc0":
                self.poutput(
                    "The installation is not supported yet. Please talk to the authors of this application to get it implemented :)"
                )
            case _:
                assert False, "Invalid argument"

    def install_stockfish(self) -> None:
        dir: str = os.path.join(appdirs.user_data_dir("chess-cli"), "stockfish")
        os.makedirs(dir, exist_ok=True)
        match platform.system():
            case "Linux":
                url: str = "https://github.com/official-stockfish/Stockfish/releases/download/sf_16/stockfish-ubuntu-x86-64-avx2.tar"
                archive_format: str = "tar"
                executable: str = "stockfish/stockfish-ubuntu-x86-64-avx2"
            case "Windows":
                url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_16/stockfish-windows-x86-64-avx2.zip"
                archive_format = "zip"
                executable = "stockfish/stockfish-windows-x86-64-avx2.exe"
            case x:
                self.poutput(f"Error: Unsupported platform: {x}")
                return
        self.poutput(f"Downloading Stockfish...")
        engine_archive, _ = urllib.request.urlretrieve(url)
        self.poutput(f"Download complete. Unpacking...")
        shutil.unpack_archive(engine_archive, dir, archive_format)
        urllib.request.urlcleanup()
        if "stockfish" in self.engine_confs:
            self.poutput(f"Removing old stockfish")
            self.onecmd("engine rm stockfish")
        executable_path: str = os.path.join(dir, executable)
        self.onecmd(f'engine import "{executable_path}" stockfish')
        ncores: int = psutil.cpu_count()
        ncores_use: int = ncores - 1 if ncores > 1 else 1
        self.poutput(
            f"You seem to have {ncores} logical cores on your system. So the engine will use {ncores_use} of them."
        )
        self.onecmd(f"engine config set threads {ncores_use}")
        ram: int = psutil.virtual_memory().total
        ram_use_MiB: int = int(0.75 * ram / 2**20)
        ram_use: int = ram_use_MiB * 2**20
        self.poutput(
            f"You seem to have a RAM of {sizeof_fmt(ram)} bytes, so stockfish will be configured to use {sizeof_fmt(ram_use)} bytes (75 %) thereof for the hash."
        )
        self.onecmd(f"engine config set hash {ram_use_MiB}")
        self.poutput(
            "You can change these settings and more with the engine config command."
        )

    def engine_quit(self, _args) -> None:
        if self.selected_engine is None:
            self.poutput("Error: No engine to quit.")
        else:
            self.loaded_engines[self.selected_engine].quit()
            del self.loaded_engines[self.selected_engine]
            self.poutput(f"Quitted {self.selected_engine} without any problems.")
            try:
                self.selected_engine = next(iter(self.loaded_engines))
                self.poutput(f"{self.selected_engine} is now selected.")
            except StopIteration:
                self.selected_engine = None

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

    def engine_config(self, args) -> None:
        if not self.selected_engine:
            self.poutput("Error: No engine is loaded.")
            return
        match args.config_subcmd:
            case "ls":
                self.engine_config_ls(args)
            case "get":
                self.engine_config_get(args)
            case "set":
                self.engine_config_set(args)
            case "unset":
                self.engine_config_unset(args)
            case "trigger":
                self.engine_config_trigger(args)
            case _:
                assert False, "Invalid subcommand."

    def get_engine_opt_name(self, engine: str, name: str) -> str:
        "Case insensitively search for a name of an option on an engine. Raises CommandFailure if not found."
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        if name in options:
            return name
        try:
            return next(
                (name for name in options.keys() if name.lower() == name.lower())
            )
        except StopIteration:
            self.poutput(
                f"Error: No option named {name} in the engine {engine}. List all availlable options with `engine config ls`."
            )
            raise CommandFailure()

    def get_selected_engine(self) -> str:
        "Get the selected engine or raise CommandFailure."
        if self.selected_engine is None:
            self.poutput("Error: No engine is selected.")
            raise CommandFailure()
        return self.selected_engine

    def engine_config_get(self, args) -> None:
        engine: str = self.get_selected_engine()
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        self.show_engine_option(engine, opt_name)

    def engine_config_ls(self, args) -> None:
        engine: str = self.get_selected_engine()
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
                    pattern: re.Pattern = re.compile(args.regex, flags=re.IGNORECASE)
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

    def engine_config_set(self, args) -> None:
        engine: str = self.get_selected_engine()
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        conf: EngineConf = self.engine_confs[engine]
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        option: chess.engine.Option = options[opt_name]
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
            self.save_engine_config()
        self.set_engine_option(engine, option.name, value)

    def engine_config_unset(self, args) -> None:
        engine: str = self.get_selected_engine()
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        default = options[opt_name].default
        if default is None:
            if args.temporary:
                self.poutput(
                    f"Error: {opt_name} has no default value and wasn't changed. Try to set it to a custom value with `engine config set --temporary {args.name} <value>`."
                )
                return
            self.poutput(
                f"Warning: {opt_name} has no default value so it's unchanged in the running engine."
            )
        else:
            self.loaded_engines[engine].configure({opt_name: default})
            self.poutput(
                f"Successfully changed {opt_name} back to its default value: {default}."
            )

        if not args.temporary:
            conf: EngineConf = self.engine_confs[engine]
            conf.options.pop(opt_name, None)
            self.save_engine_config()

    def engine_config_trigger(self, args) -> None:
        engine: str = self.get_selected_engine()
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].options
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        if options[opt_name].type not in ["button", "reset", "save"]:
            self.poutput(f"Error: {opt_name} is not a button.")
            return
        self.loaded_engines[engine].configure({opt_name: None})

    def save_engine_config(self) -> None:
        os.makedirs(os.path.split(self.config_file)[0], exist_ok=True)
        with open(self.config_file, "w") as f:
            engine_confs = {
                name: conf._asdict() for (name, conf) in self.engine_confs.items()
            }
            items = {"engine-configurations": engine_confs}
            toml.dump(items, f)

    analysis_argparser = cmd2.Cmd2ArgumentParser()
    analysis_subcmds = analysis_argparser.add_subparsers(dest="subcmd")
    analysis_start_argparser = analysis_subcmds.add_parser(
        "start", help="Start to analyse with the selected engine."
    )
    analysis_start_argparser.add_argument(
        "-f",
        "--fixed",
        action="store_true",
        help="Fix the analysis to the current move. If not given, the analysis will be stopped and restarted as the current position changes.",
    )
    analysis_start_argparser.add_argument(
        "-n", "--number-of-moves", type=int, default=5, help="Show the n best moves."
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
    analysis_stop_argparser.add_argument(
        "-a", "--all", action="store_true", help="Stop all engines."
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
        help="Remove analysis made by the selected engine at this move. Useful if you want to rerun the analysis.",
    )
    analysis_rm_argparser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Remove all analysis made by all engines at this move.",
    )
    analysis_rm_argparser.add_argument(
        "engine",
        nargs="?",
        help="Remove analysis made by this engine at this move. Defaults to the currently selected engine.",
    )

    @cmd2.with_argparser(analysis_argparser)  # type: ignore
    def do_analysis(self, args) -> None:
        """Manage analysis."""
        match args.subcmd:
            case "ls":
                self.analysis_ls(args)
            case "show":
                self.analysis_show(args)
            case "start":
                self.analysis_start(args)
            case "stop":
                self.analysis_stop(args)
            case "rm" | "remove":
                self.analysis_rm(args)
            case _:
                assert False, "Invalid subcommand."

    def start_analysis(
        self,
        engine: str,
        number_of_moves: int,
        limit: Optional[chess.engine.Limit] = None,
    ) -> None:
        if engine in self.running_analysis:
            return
        analysis: Analysis = Analysis(
            result=self.loaded_engines[engine].analysis(
                self.game_node.board(),
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

    def update_auto_analysis(self) -> None:
        for engine in self.auto_analysis_engines:
            if (
                engine in self.running_analysis
                and self.running_analysis[engine].board != self.game_node.board()
            ):
                self.stop_analysis(engine)
            self.start_analysis(engine, self.auto_analysis_number_of_moves)

    def analysis_start(self, args) -> None:
        engine: str = self.get_selected_engine()
        if engine in self.analysis_by_node[self.game_node]:
            self.poutput(
                f"Error: There's allready an analysis made by {engine} at this move."
            )
            answer: str = self.read_input(
                "Do you want to remove it and restart the analysis? [Y/n] "
            )
            match answer.strip():
                case "y" | "Y":
                    self.onecmd("analysis rm")
                case "n" | "N":
                    return
                case _:
                    self.poutput("Expected Y or n.")
                    return
        if engine in self.running_analysis:
            self.poutput(
                f"Error: {engine} is already running an analysis, stop it with `analysis stop` before you can restart it."
            )
            return
        if args.fixed:
            self.start_analysis(engine, args.number_of_moves, args.limit)
        else:
            self.auto_analysis_engines.add(engine)
            self.auto_analysis_number_of_moves = args.number_of_moves
            self.update_auto_analysis()
        self.poutput(f"{engine} is now analysing.")

    def stop_analysis(self, engine: str) -> None:
        self.running_analysis[engine].result.stop()
        del self.running_analysis[engine]

    def analysis_stop(self, args) -> None:
        if args.all:
            engines: Iterable[str] = self.running_analysis.keys()
        else:
            engine: str = self.get_selected_engine()
            if engine not in self.running_analysis:
                self.poutput("Error: {engine} is not running any analysis.")
                return
            engines = [engine]
        for engine in engines:
            if engine not in self.running_analysis:
                continue
            self.stop_analysis(engine)
            with contextlib.suppress(KeyError):
                self.auto_analysis_engines.remove(engine)
            self.poutput(f"Successfully stopped {engine}")

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
            for key, val in analysis.result.info.items():
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

    def analysis_rm(self, args) -> None:
        if args.all:
            engines: Iterable[str] = self.engine_confs.keys()
        else:
            if args.engine:
                engine: str = args.engine
            else:
                engine = self.get_selected_engine()
            if engine not in self.analysis_by_node[self.game_node]:
                self.poutput(
                    f"Error: There is no analysis made by {engine} at this move."
                )
                return
            engines = [engine]
        for engine in engines:
            try:
                removed = self.analysis_by_node[self.game_node].pop(engine)
            except KeyError:
                continue
            if engine in self.running_analysis:
                self.stop_analysis(engine)
            self.analysis.remove(removed)
            self.poutput(f"Removed analysis made by {engine}.")

    games_argparser = cmd2.Cmd2ArgumentParser()
    games_subcmds = games_argparser.add_subparsers(dest="subcmd")
    games_ls_argparser = games_subcmds.add_parser("ls", help="List all games.")
    games_rm_argparser = games_subcmds.add_parser(
        "rm", aliases=["remove"], help="Remove the current game."
    )
    games_rm_subcmds = games_rm_argparser.add_subparsers(dest="subcmd")
    games_rm_subcmds.add_parser("this", help="Remove the currently selected game.")
    games_rm_subcmds.add_parser(
        "others", help="Remove all but the currently selected game."
    )
    games_rm_subcmds.add_parser(
        "all", help="Remove all games. Including the current game."
    )
    games_select_argparser = games_subcmds.add_parser(
        "select", aliases=["s", "sel"], help="Select another game in the file."
    )
    games_select_argparser.add_argument(
        "index",
        type=int,
        help="Index of the game to select. Use the `game ls` command to get the index of a particular game.",
    )
    games_add_argparser = games_subcmds.add_parser(
        "add", help="Add a new game to the file."
    )
    games_add_argparser.add_argument(
        "index",
        type=int,
        help="The index where the game should be inserted. Defaults to the end of the game list.",
    )

    @cmd2.with_argparser(games_argparser)  # type: ignore
    def do_games(self, args) -> None:
        "List, select, delete or create new games."
        match args.subcmd:
            case "ls":
                for i, game in enumerate(self.games):
                    show_str: str = f"{i+1}. "
                    if i == self.current_game:
                        show_str += "[*] "
                    show_str += f"{game.headers['White']} - {game.headers['Black']}"
                    if isinstance(game.game_node, chess.pgn.ChildNode):
                        show_str += f" @ {MoveNumber.last(game.game_node)} {game.game_node.san()}"
                    self.poutput(show_str)
            case "rm":
                self.games.pop(self.current_game)
                if self.current_game == len(self.games):
                    if self.games:
                        self.current_game -= 1
                    else:
                        self.add_new_game()
            case "s" | "sel" | "select":
                self.select_game(args.index)
            case "add":
                self.add_new_game(args.index)
            case _:
                assert False, "Unknown subcommand."

    save_argparser = cmd2.Cmd2ArgumentParser()
    save_argparser.add_argument(
        "file", nargs="?", help="File to save to. Defaults to the loaded file."
    )
    save_argparser.add_argument(
        "-t",
        "--this",
        action="store_true",
        help="Save only the current game and discard any changes in the other games.",
    )

    @cmd2.with_argparser(save_argparser)  # type: ignore
    def do_save(self, args) -> None:
        "Save the games to a PGN file."
        current_game: int = self.current_game
        if args.file is None:
            if self.pgn_file is None:
                self.poutput("Error: No file selected.")
                return
            file_name: str = self.pgn_file.name
        else:
            file_name = args.file
        file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        tempfile_name = file.name
        try:
            if args.this:
                if len(self.games) > 1 and any(
                    (
                        x.game_node is not None
                        for i, x in enumerate(self.games)
                        if i != self.current_game
                    )
                ):
                    while True:
                        answer: str = self.read_input(
                            "Warning: Any changes made to other games will be lost, do you want to continue? [Y/n] "
                        )
                        match answer.lower():
                            case "n":
                                return
                            case "y":
                                break
                            case _:
                                self.poutput("Expects Y / n for Yes / no.")
                print(self.game_node.game(), file=file)
            else:
                for i in range(len(self.games)):
                    self.select_game(i)
                    print(self.game_node.game(), file=file)
            file.close()
        except Exception:
            file.close()
            os.remove(tempfile_name)
        shutil.move(tempfile_name, file_name)

        if args.this:
            self.load_games(file_name)
        else:
            self.pgn_file = open(file_name)
            for i, game in enumerate(self.games):
                offset: int = self.pgn_file.tell()
                headers = chess.pgn.read_headers(self.pgn_file)
                assert headers is not None
                assert headers == game.headers
                if game.game_node is None:
                    self.games[i] = GameHandle(headers, offset)
            self.select_game(current_game)

    load_argparser = cmd2.Cmd2ArgumentParser()
    load_argparser.add_argument("file", help="PGN file to read.")

    @cmd2.with_argparser(load_argparser)  # type: ignore
    def do_load(self, args) -> None:
        "Load games from a PGN file. Note that the current game will be lost."
        self.load_games(args.file)

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
