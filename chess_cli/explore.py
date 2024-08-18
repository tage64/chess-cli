from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import override

import chess

from .base import Base
from .utils import piece_name


class ExploreValueError(ValueError):
    """An error raised when parsing invalid, but not totally wrong, explore strings.

    For instance, when parsing a rank, the string 'Hello there!' will raise a normal `ValueError`,
    while the string '9' will raise an 'ExploreValueError'.
    """


class Scan(ABC):
    """Abstract class to explore a set of squares on a chess board."""

    @abstractmethod
    def squares(self) -> Iterable[chess.Square]:
        """The squares to explore."""

    def pieces(self, board: chess.Board) -> Iterable[tuple[chess.Square, chess.Piece]]:
        """Get the pieces on `self.squares()`."""
        return ((sq, p) for sq in self.squares() if (p := board.piece_at(sq)) is not None)


class ScanRank(Scan):
    """Explore a rank on the chess board."""

    rank_idx: int

    def __init__(self, rank_str: str) -> None:
        """Parse the str as a rank on the chess board (like '5' or '8').

        Raises `ValueError` upon failure.
        """
        rank_num: int = int(rank_str)
        if not 1 <= rank_num <= 8:
            raise ExploreValueError("The rank index must be in the range [1, 8]")
        self.rank_idx = rank_num - 1

    @override
    def squares(self) -> Iterable[chess.Square]:
        return (chess.square(file_index=f, rank_index=self.rank_idx) for f in range(8))


class ScanFile(Scan):
    """Explore a file on the chess board."""

    file_idx: int

    def __init__(self, file_str: str) -> None:
        """Parse the str as a file on the chess board (like 'a' or 'e').

        Raises `ValueError` upon failure.
        """
        if len(file_str) != 1:
            raise ValueError("Expected a single character.")
        file_idx: int = ord(file_str) - ord("a")
        if not 0 <= file_idx < 8:
            raise ExploreValueError("The file must be a character between 'a' and 'h'.")
        self.file_idx = file_idx

    @override
    def squares(self) -> Iterable[chess.Square]:
        return (chess.square(rank_index=r, file_index=self.file_idx) for r in range(8))


class ScanDiagonal(Scan):
    """Explore a diagonal on the chess board."""

    # Two squares liing on the diagonal.
    two_squares: tuple[chess.Square, chess.Square]

    def __init__(self, diagonal_str: str) -> None:
        """Parse the str as two squares (with no spaces) on a diagonal.

        Example: 'a2b3', 'h8a1', 'g5f4', and 'e6f7'.
        Raises `ValueError` upon failure.
        """
        if len(diagonal_str) != 4:
            raise ValueError("Expected a string with exactly four characters.")
        squares = (chess.parse_square(diagonal_str[:2]), chess.parse_square(diagonal_str[2:]))
        if abs(chess.square_file(squares[0]) - chess.square_file(squares[1])) != abs(
            chess.square_rank(squares[0]) - chess.square_rank(squares[1])
        ):
            raise ExploreValueError("The squares must be on the same diagonal.")
        self.two_squares = squares

    @override
    def squares(self) -> Iterable[chess.Square]:
        return chess.SquareSet.ray(*self.two_squares)


class LocatePieces(ABC):
    """An abstract class to locate pieces on a chess board."""

    @abstractmethod
    def pieces(self) -> Iterable[chess.Piece]:
        """The pieces to locate."""

    def squares(self, board: chess.Board) -> Iterable[tuple[chess.Piece, Iterable[chess.Square]]]:
        """All squares for the pieces from `self.pieces()`, skipping pieces which does not exist."""
        for p in self.pieces():
            squares = board.pieces(p.piece_type, p.color)
            if squares:
                yield p, squares


class ExploreBoard(Base):
    """Methods to explore the board."""

    def scan(self, text: str) -> None:
        """Scan a file, rank or diagonal like 'a', '8' or 'a2b3' and print the pieces on it.

        Raises `ExploreValueError` (a subclass to `ValueError`) if the string looks
        right but is invalid. Like '9' or 'm', but not 'Hello' or 'a5'.
        Raises `ValueError` if the string looks completely wrong.
        """
        text = text.lower().replace(" ", "")
        scan: Scan
        ExploreClasses: list[type] = [ScanRank, ScanFile, ScanDiagonal]
        for ExploreClass in ExploreClasses:
            try:
                scan = ExploreClass(text)
                break
            except ValueError as e:
                if isinstance(e, ExploreValueError):
                    raise e
                continue
        else:
            raise ValueError(
                "Expected a file like 'a', a rank like '8', "
                "or two squares on a diagonal like 'c4f7'"
            )
        empty: bool = True
        for sq, p in scan.pieces(self.game_node.board()):
            empty = False
            print(f"{chess.square_name(sq)}: {piece_name(p, capital=True)}")
        if empty:
            print("Empty")
