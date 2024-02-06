import enum

import cmd2

from .lichess_api import LichessApi


class LichessVariant(enum.StrEnum):
    "A chess variant on Lichess."
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
    "Basic commands related to the Lichess API."
    challenge_argparser = cmd2.Cmd2ArgumentParser()
    challenge_argparser.add_argument(
        "-t", "--time", type=int, help="Time limit for the game in seconds."
    )
    challenge_argparser.add_argument(
        "-i", "--increment", "--inc", type=int, help="Increment (in seconds) per move."
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

    @cmd2.with_argparser(challenge_argparser)  # type: ignore
    def do_challenge(self, args) -> None:
        """Create a challenge from the current position on Lichess."""
        challenge_data: dict = self.client.challenges.create_open(
            clock_limit=args.time,
            clock_increment=args.increment,
            rated=not args.not_rated,
            name=args.name,
            variant=args.variant,
            position=self.game_node.board().fen(),
        )
        challenge: dict = challenge_data["challenge"]
        self.poutput(challenge)
        self.poutput(
            f"Created {challenge['variant']['name']} game -- "
            f"{'rated' if challenge['rated'] else 'not rated'} {challenge['speed']} "
            f"{challenge['timeControl']['show'] if 'show' in challenge['timeControl'] else ''}"
        )
        self.poutput(f"URL:\n  {challenge['url']}")
        self.poutput(f"White URL:\n  {challenge_data['urlWhite']}")
        self.poutput(f"Black URL:\n  {challenge_data['urlBlack']}")
