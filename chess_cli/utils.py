import re
from typing import *

import chess.engine
import chess.pgn
from chess.engine import Score

from . import nags


def sizeof_fmt(num, suffix="B"):
    "Print byte size with correct prefix."
    for unit in ("", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


COMMANDS_IN_COMMENTS_REGEX: re.Pattern[str] = re.compile(r"\[%.+?\]")


def commands_in_comment(raw_comment: str) -> str:
    "Get a string with all embedded commands in a pgn comment."
    return " ".join(COMMANDS_IN_COMMENTS_REGEX.findall(raw_comment))


def comment_text(raw_comment: str) -> str:
    """Strip out all commands like [%cal xxx] or [%clk xxx] from a comment."""
    return " ".join(COMMANDS_IN_COMMENTS_REGEX.split(raw_comment)).strip()


def update_comment_text(original_comment: str, new_text: str) -> str:
    "Return a new comment with the same embedded commands but with the text replaced."
    return f"{commands_in_comment(original_comment)}\n{new_text}"


MOVE_NUMBER_REGEX: re.Pattern[str] = re.compile(r"(\d+)((\.{3})|\.?)")


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

        Plain numbers without any dots at the end will be parsed as if it was white who moved. Will
        raise ValueError if the parsing failes.
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


def score_str(score: Score) -> str:
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
