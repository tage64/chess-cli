import math
import re
from contextlib import suppress
from datetime import datetime, timedelta
from typing import NamedTuple, assert_never, override

import chess.engine
import chess.pgn
from chess.engine import Score

from . import nags


def sizeof_fmt(num, suffix="B"):
    """Print byte size with correct prefix."""
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


COMMANDS_IN_COMMENTS_REGEX: re.Pattern[str] = re.compile(r"\[%.+?\]")


def commands_in_comment(raw_comment: str) -> str:
    """Get a string with all embedded commands in a pgn comment."""
    return " ".join(COMMANDS_IN_COMMENTS_REGEX.findall(raw_comment))


def comment_text(raw_comment: str) -> str:
    """Strip out all commands like [%cal xxx] or [%clk xxx] from a comment."""
    return " ".join(COMMANDS_IN_COMMENTS_REGEX.split(raw_comment)).strip()


def update_comment_text(original_comment: str, new_text: str) -> str:
    """Return a new comment with the same embedded commands but with the text
    replaced."""
    if cmds := commands_in_comment(original_comment):
        return cmds + "\n" + new_text
    return new_text


def add_to_comment_text(original_comment: str, add_text: str) -> str:
    """Add some text to a comment without touching existing text or commands."""
    if text := comment_text(original_comment):
        return update_comment_text(original_comment, text + "\n" + add_text)
    return update_comment_text(original_comment, add_text)


MOVE_NUMBER_REGEX: re.Pattern[str] = re.compile(r"(\d+)((\.{3})|\.?)")


class MoveNumber(NamedTuple):
    """A move number is a fullmove number and the color that made the move.

    E.G. "1." would be move number 1 and color white while "10..." would be
    move number 10 and color black.
    """

    move_number: int
    color: chess.Color

    @staticmethod
    def last(pos: chess.Board | chess.pgn.ChildNode):
        """Get the move number from the previously executed move."""
        board = pos.board() if isinstance(pos, chess.pgn.ChildNode) else pos
        return MoveNumber(board.fullmove_number, board.turn).previous()

    @staticmethod
    def from_regex_match(match: re.Match):
        """Create a move number from a regex match."""
        number: int = int(match.group(1))
        color = chess.BLACK if match.group(3) is not None else chess.WHITE
        return MoveNumber(number, color)

    @staticmethod
    def parse(move_text: str):
        """Parse a chess move number like "3." or "5...".

        Plain numbers without any dots at the end will be parsed as if it was white who
        moved. Will raise ValueError if the parsing failes.
        """
        match = MOVE_NUMBER_REGEX.fullmatch(move_text)
        if match is None:
            raise ValueError(f"Invalid move number {move_text}")
        return MoveNumber.from_regex_match(match)

    def previous(self):
        """Get previous move."""
        if self.color == chess.WHITE:
            return MoveNumber(self.move_number - 1, chess.BLACK)
        else:
            return MoveNumber(self.move_number, chess.WHITE)

    def next(self):
        """Get next move."""
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
                res += f"[{", ".join(nag_strs)}]"
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
        and game_node.parent.variations[-1] != game_node
    ):
        res += ">"
    return res


def score_str(score: Score) -> str:
    if score == chess.engine.MateGiven:
        return "mate"
    if score.is_mate():
        mate: int = score.mate()  # type: ignore
        if mate > 0:
            return f"Mate in {mate}"
        return f"Mated in {-mate}"
    cp: int = score.score()  # type: ignore
    if cp > 0:
        return f"+{cp / 100} pawns"
    return f"{cp / 100} pawns"


def show_time(
    time: float | timedelta,
    decimals: int | None = None,
    short: bool = False,
    trailing_zeros: bool = False,
) -> str:
    """Make a human friendly string representation of a timestamp."""
    secs = time.total_seconds() if isinstance(time, timedelta) else time
    if secs < 0:
        negative = True
        secs = -secs
    else:
        negative = False
    hours: int = math.floor(secs / 3600)
    secs %= 3600
    minutes: int = math.floor(secs / 60)
    secs %= 60
    secs_str: str
    if trailing_zeros:
        if decimals is not None:
            secs_str = f"{secs:.{decimals}f}"
        else:
            raise ValueError(
                "Both trailing_zeros and decimals=None cannot be set at the same time."
            )
    else:
        secs_str = f"{round(secs, decimals):g}" if decimals is not None else f"{secs:g}"

    res = ""
    if short:
        if negative:
            res += "-"
        if hours != 0:
            res += f"{hours:02d}:"
        if minutes != 0 or hours != 0:
            res += f"{minutes:02d}:"
        res += secs_str
    else:
        if negative:
            res += "minus "
        if hours != 0:
            res += f"{hours} hour"
            if hours != 1:
                res += "s"
            if minutes != 0 and (secs != 0 or trailing_zeros):
                res += ", "
            elif minutes != 0 or secs != 0 or trailing_zeros:
                res += " and "
        if minutes != 0:
            res += f"{minutes} minute"
            if minutes != 1:
                res += "s"
            if secs != 0 or trailing_zeros:
                res += " and "
        if secs != 0 or trailing_zeros or (hours == 0 and minutes == 0):
            res += secs_str + " seconds"
    return res


def show_rounded_time(
    time: float | timedelta,
    decimals: int | None = 1,
    short: bool = False,
    trailing_zeros: bool = True,
) -> str:
    return show_time(time=time, decimals=decimals, short=short, trailing_zeros=trailing_zeros)


def parse_time(time_str: str) -> timedelta:
    formats = ["%H:%M:%S", "%M:%S", "%S"]
    for fmt in formats:
        try:
            dt: datetime = datetime.strptime(time_str, fmt)
            return dt - datetime.strptime("0", "%S")
        except ValueError:
            pass
    with suppress(ValueError):
        return timedelta(seconds=float(time_str))
    raise ValueError(f"Failed to parse {time_str} by any of the formats: {formats}")


def parse_time_control(text: str) -> tuple[timedelta, timedelta]:
    """Parse a string on the form time+increment, where time is minutes and increment is seconds."""
    parts = text.split("+")
    time = timedelta(minutes=float(parts[0]))
    if len(parts) == 2:
        inc = timedelta(seconds=float(parts[1]))
    elif len(parts) > 2:
        raise ValueError("The time control should be on the form time+increment.")
    else:
        inc = timedelta(0)
    return time, inc


def piece_name(piece: chess.Piece, capital: bool = False) -> str:
    """Return a full name (like "white king" or "black pawn") for a piece."""
    color_str: str
    if capital:
        color_str = "White" if piece.color == chess.WHITE else "Black"
    else:
        color_str = "white" if piece.color == chess.WHITE else "black"
    piece_name: str = chess.piece_name(piece.piece_type)
    return f"{color_str} {piece_name}"


def show_outcome(outcome: chess.Outcome) -> str:
    """A human friendly representation of an outcome."""
    res: str
    match outcome.termination:
        case chess.Termination.CHECKMATE:
            res = "Checkmate"
        case chess.Termination.STALEMATE:
            res = "Stalemate"
        case chess.Termination.INSUFFICIENT_MATERIAL:
            res = "Insufficient material"
        case chess.Termination.SEVENTYFIVE_MOVES:
            res = "Seventyfive moves"
        case chess.Termination.FIFTY_MOVES:
            res = "Fifty moves"
        case chess.Termination.FIVEFOLD_REPETITION:
            res = "Fivefold repetition"
        case chess.Termination.THREEFOLD_REPETITION:
            res = "Threefold repetition"
        case chess.Termination.VARIANT_WIN:
            res = "Variant specific win"
        case chess.Termination.VARIANT_DRAW:
            res = "Variant specific draw"
        case chess.Termination.VARIANT_LOSS:
            res = "Variant specific loss"
        case x:
            assert_never(x)
    res += ": "
    match outcome.winner:
        case chess.WHITE:
            res += "White wins!"
        case chess.BLACK:
            res += "Black wins!"
        case None:
            res += "It's a draw"
    return res


def castling_descr(board: chess.Board) -> str:
    """Return a human readable string for the castling rights on the board."""

    def for_color(color: chess.Color) -> str:
        if board.has_kingside_castling_rights(color):
            if board.has_queenside_castling_rights(color):
                return "can castle on both sides"
            return "can castle kingside"
        if board.has_queenside_castling_rights(color):
            return "can castle queenside"
        return "is not allowed to castle"

    white_descr = for_color(chess.WHITE)
    black_descr = for_color(chess.BLACK)
    if white_descr == black_descr:
        if white_descr == "is not allowed to castle":
            return "Neither White nor Black is allowed to castle."
        else:
            return f"White and Black {white_descr}"
    else:
        return f"White {white_descr} and Black {black_descr}."


class BoardSearcher(chess.pgn.BaseVisitor[None]):
    """Search for a particular position in a game.

    Raises `BoardFoundException` if the board is found.
    """

    search_pos: chess.Board

    def __init__(self, search_pos: chess.Board) -> None:
        self.search_pos = search_pos

    @override
    def begin_game(self) -> None:
        self.skip_variation_depth = 0

    @override
    def begin_variation(self) -> chess.pgn.SkipType:
        self.skip_variation_depth += 1
        return chess.pgn.SKIP

    @override
    def end_variation(self) -> None:
        self.skip_variation_depth = max(self.skip_variation_depth - 1, 0)

    @override
    def visit_board(self, board: chess.Board) -> None:
        if not self.skip_variation_depth:
            if self.search_pos == board:
                raise BoardFoundException
            if not chess.SquareSet(self.search_pos.castling_rights).issubset(board.castling_rights):
                raise BoardNotFoundException
            if len(board.pieces(chess.PAWN, chess.BLACK)) < len(
                self.search_pos.pieces(chess.PAWN, chess.BLACK)
            ) or len(board.pieces(chess.PAWN, chess.WHITE)) < len(
                self.search_pos.pieces(chess.PAWN, chess.WHITE)
            ):
                raise BoardNotFoundException

    @override
    def result(self) -> None:
        pass


class BoardFoundException(Exception):
    """Raised when the board is found."""


class BoardNotFoundException(Exception):
    """Raised when it is proven that the board cannot be found in this game.
    May never be raised.
    """
