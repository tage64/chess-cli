from argparse import ArgumentParser

from .base import CommandFailure
from .explore import ExploreBoard
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
