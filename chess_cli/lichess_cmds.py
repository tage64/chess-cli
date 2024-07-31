import enum
from argparse import ArgumentParser

from .lichess_api import LichessApi
from .repl import argparse_command
from .utils import parse_time_control


class LichessVariant(enum.StrEnum):
    """A chess variant on Lichess."""

    STANDARD = "standard"
    CHESS960 = "chess960"
    CRAZYHOUSE = "crazyhouse"
    ANTICHESS = "antichess"
    ATOMIC = "atomic"
    HORDE = "horde"
    KING_OF_THE_HILL = "kingOfTheHill"
    RACING_KINGS = "racingKings"
    THREE_CHECK = "threeCheck"


class LichessCmds(LichessApi):
    """Basic commands related to the Lichess API."""

    challenge_argparser = ArgumentParser()
    challenge_argparser.add_argument(
        "time_control",
        type=parse_time_control,
        help="A time control like 3+2 for 3 minutes and 2 seconds increment.",
    )
    challenge_argparser.add_argument(
        "--not-rated", action="store_true", help="The game should not be rated."
    )
    challenge_argparser.add_argument("--name", help="An optional name for the challenge.")
    challenge_argparser.add_argument(
        "-v",
        "--variant",
        type=LichessVariant,
        choices=[v.value for v in LichessVariant],
        default=LichessVariant.STANDARD,
        help="Chess variant for the game.",
    )

    @argparse_command(challenge_argparser)
    def do_challenge(self, args) -> None:
        """Create a challenge from the current position on Lichess."""
        time, inc = args.time_control
        challenge: dict = self.client.challenges.create_open(
            clock_limit=int(time.total_seconds()),
            clock_increment=int(inc.total_seconds()),
            rated=not args.not_rated,
            name=args.name,
            variant=args.variant,
            position=self.game_node.board().fen(),
        )
        self.poutput(
            f"Created {challenge["variant"]["name"]} game -- "
            f"{"rated" if challenge["rated"] else "not rated"} {challenge["speed"]} "
            f"{challenge["timeControl"].get("show", "")}"
        )
        self.poutput(f"URL:\n  {challenge["url"]}")
        self.poutput(f"White URL:\n  {challenge["urlWhite"]}")
        self.poutput(f"Black URL:\n  {challenge["urlBlack"]}")
