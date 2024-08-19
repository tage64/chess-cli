from argparse import ArgumentParser

import chess

from .base import CommandFailure
from .explore import ExploreBoard, LocateAttackers, LocatePieceTypes
from .repl import argparse_command


class ExploreCmds(ExploreBoard):
    """Commands to explore/scan the chess board."""

    scan_argparser = ArgumentParser()
    scan_argparser.add_argument("scan", help="A file, rank, or two squares on the same diagonal.")

    @argparse_command(scan_argparser, alias="s")
    def do_scan(self, args) -> None:
        """Print the pieces on a certain rank, file or diagonal.

        You can either provide:
        - A rank like '1' or '6'.
        - A file like 'a' or 'f'.
        - A diagonal by two squares, like 'a2b3' or 'h1a8'.
        """
        try:
            self.scan(args.scan)
        except ValueError as e:
            raise CommandFailure(str(e)) from e

    piece_argparser = ArgumentParser()
    piece_argparser.add_argument(
        "pieces", help="One or more piece symbols. Like 'pnq' for pawn, knight and queen."
    )

    @argparse_command(piece_argparser, alias="p")
    def do_piece(self, args) -> None:
        """Locate pieces on the chess board."""
        try:
            locator = LocatePieceTypes(args.pieces.lower())
        except ValueError as e:
            raise CommandFailure(str(e)) from e
        self.print_locate_pieces(locator)

    attackers_argparser = ArgumentParser()
    attackers_argparser.add_argument(
        "square", type=chess.parse_square, help="The square to be attacked."
    )

    @argparse_command(attackers_argparser, alias="at")
    def do_attackers(self, args) -> None:
        """Locate pieces attacking a specific square."""
        self.print_locate_pieces(LocateAttackers(args.square))
