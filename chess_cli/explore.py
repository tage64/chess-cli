from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import override

import chess

from .base import Base
from .utils import piece_name

PIECE_TYPES: list[chess.PieceType] = [
    chess.PAWN,
    chess.KNIGHT,
    chess.BISHOP,
    chess.ROOK,
    chess.QUEEN,
    chess.KING,
]
PIECE_BY_SYMBOL: dict[str, chess.PieceType] = {chess.piece_symbol(p): p for p in PIECE_TYPES}


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
    def pieces_and_squares(
        self, board: chess.Board
    ) -> Iterable[tuple[chess.Piece, chess.SquareSet]]:
        """All squares for the pieces from `self.pieces()`, skipping pieces which does not exist."""


class LocatePieceTypes(LocatePieces):
    """Locate one or more piece types of any color."""

    piece_types: list[chess.PieceType]

    def __init__(self, pattern: str) -> None:
        """Parse a pattern of piece chars, like 'bnp' for bishop, knight and pawn."""
        piece_types = []
        for c in pattern:
            try:
                piece_types.append(PIECE_BY_SYMBOL[c])
            except KeyError as e:
                raise ValueError(f"Invalid piece symbol '{c}'") from e
        self.piece_types = piece_types

    def pieces(self, board: chess.Board) -> Iterable[chess.Piece]:
        """The pieces to locate."""
        return (
            chess.Piece(piece_type=p, color=c)
            for p in self.piece_types
            for c in (chess.WHITE, chess.BLACK)
        )

    @override
    def pieces_and_squares(
        self, board: chess.Board
    ) -> Iterable[tuple[chess.Piece, chess.SquareSet]]:
        for p in self.pieces(board):
            squares = board.pieces(p.piece_type, p.color)
            if squares:
                yield p, squares


@dataclass
class LocateAttackers(LocatePieces):
    """Locate all pieces attacking a specific square."""

    square: chess.Square

    @override
    def pieces_and_squares(
        self, board: chess.Board
    ) -> Iterable[tuple[chess.Piece, chess.SquareSet]]:
        pieces_and_squares: dict[chess.Piece, chess.SquareSet] = defaultdict(chess.SquareSet)
        for color in (chess.WHITE, chess.BLACK):
            for square in board.attackers(color, self.square):
                piece = board.piece_at(square)
                assert piece is not None
                pieces_and_squares[piece].add(square)
        return sorted(
            pieces_and_squares.items(),
            key=lambda x: (0 if (p := x[0]).color == board.turn else len(PIECE_TYPES))
            + p.piece_type,
        )


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

    def print_locate_pieces(self, locate_pieces: LocatePieces) -> None:
        """Print a `LocatePieces`."""
        nothing: bool = True
        for piece, squares in locate_pieces.pieces_and_squares(self.game_node.board()):
            nothing = False
            piece_name_ = piece_name(piece, capital=True)
            print(
                f"{self.p.plural_noun(piece_name_, len(squares))}: "  # type: ignore
                f"{self.p.join([chess.square_name(sq) for sq in squares])}"  # type: ignore
            )
        if nothing:
            print("None")
