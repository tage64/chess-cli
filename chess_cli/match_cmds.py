import argparse
from typing import assert_never

import chess

from .clock import ChessClock
from .engine_player import EnginePlayer
from .match import Match
from .repl import CommandFailure, argparse_command, command
from .utils import parse_time, parse_time_control, show_rounded_time


class MatchCmds(EnginePlayer, Match):
    """Commands to play a chess match."""

    @command("time", alias="t")
    def do_time(self, _) -> None:
        """Sho the remaining time if a chess clock is active."""
        white_clock, black_clock = self.get_clocks()
        if white_clock is None and black_clock is None:
            print("No clock set.")
            return
        res: str = ""
        if white_clock is not None:
            res += show_rounded_time(white_clock.remaining_time(), trailing_zeros=False)
        res += " -- "
        if black_clock is not None:
            res += show_rounded_time(black_clock.remaining_time(), trailing_zeros=False)
        print(res)

    clock_argparser = argparse.ArgumentParser()
    clock_subcmds = clock_argparser.add_subparsers(dest="subcmd")
    clock_subcmds.add_parser(
        "show",
        aliases=["sh"],
        help="Show the time control.  "
        "To quickly check the remaining time you can use the `time` (or shorter `t`) command.",
    )
    clock_set_argparser = clock_subcmds.add_parser(
        "set", aliases=["s"], help="Configure the chess clock for a match."
    )
    clock_set_argparser.add_argument(
        "time_control",
        type=parse_time_control,
        nargs="?",
        help="The time control in the format minutes+seconds, (e.g. 90+30 or 3+2).  ",
    )
    clock_set_argparser.add_argument(
        "--white-time", "--wt", type=parse_time, help="Set the remaining time for white."
    )
    clock_set_argparser.add_argument(
        "--black-time", "--bt", type=parse_time, help="Set the remaining time for black."
    )
    clock_set_argparser.add_argument(
        "--white-inc", "--wi", type=parse_time, help="Set the increment per move for white."
    )
    clock_set_argparser.add_argument(
        "--black-inc", "--bi", type=parse_time, help="Set the increment per move for black."
    )

    @argparse_command(clock_argparser, alias=["cl", "clk"])
    def do_clock(self, args) -> None:
        """Configure or show the chess clock for a match."""
        match args.subcmd:
            case "show" | "sh" | None:
                white_clock, black_clock = self.get_clocks()
                if white_clock is not None and black_clock is not None:
                    show_white = white_clock.show()
                    show_black = black_clock.show()
                    if show_white == show_black:
                        print(show_white)
                    else:
                        print(f"{show_white} -- {show_black}")
                elif white_clock is not None:
                    print(f"White clock: {white_clock.show()}")
                elif black_clock is not None:
                    print(f"Black clock: {black_clock.show()}")
                else:
                    print("No clocks set.")
            case "set" | "s":
                white_clock, black_clock = self.get_clocks()
                if args.time_control:
                    if self.match_started():
                        raise CommandFailure("A match is already started.")
                    if white_clock is not None or black_clock is not None:
                        self.delete_clocks()
                    time, inc = args.time_control
                    white_clock = ChessClock(time, inc)
                    self.add_clock(white_clock, chess.WHITE)
                    black_clock = ChessClock(time, inc)
                    self.add_clock(black_clock, chess.BLACK)
                if args.white_time:
                    if white_clock is None:
                        white_clock = ChessClock(args.white_time)
                        self.add_clock(white_clock, chess.WHITE)
                    else:
                        white_clock.set_time(args.white_time)
                if args.black_time:
                    if black_clock is None:
                        black_clock = ChessClock(args.black_time)
                        self.add_clock(black_clock, chess.BLACK)
                    else:
                        black_clock.set_time(args.black_time)
                if args.white_inc:
                    if white_clock is None:
                        raise CommandFailure("There is no clock set for White.")
                    white_clock.increment = args.white_inc
                if args.black_inc:
                    if black_clock is None:
                        raise CommandFailure("There is no clock set for Black.")
                    black_clock.increment = args.black_inc
            case x:
                assert_never(x)

    player_argparser = argparse.ArgumentParser()
    player_subcmds = player_argparser.add_subparsers(dest="subcmd")
    player_subcmds.add_parser("ls", aliases=["list"], help="List all players.")
    player_add_argparser = player_subcmds.add_parser("add", aliases=["a"], help="Add a player.")
    player_add_argparser.add_argument("engine", help="Name of the chess engine.")
    player_add_argparser.add_argument(
        "color", choices=["white", "w", "black", "b"], help="The color for the machine to play."
    )
    player_add_limit_group = player_add_argparser.add_mutually_exclusive_group()
    player_add_limit_group.add_argument(
        "--time", type=parse_time, help="The machine should think this fixed time per move."
    )
    player_add_limit_group.add_argument(
        "--depth", type=int, help="The machine should think to this depth every move."
    )
    player_add_limit_group.add_argument(
        "--nodes", type=int, help="The machine should use exactly this number of nodes per move."
    )

    @argparse_command(player_argparser, alias="pl")
    def do_player(self, args) -> None:
        """Commands to add or list players to play against, such as chess machines."""
        match args.subcmd:
            case "ls" | "list" | None:
                self._list_players()
            case "add" | "a":
                if not args.engine in self.loaded_engines:
                    raise CommandFailure(f"The engine {args.engine} is not loaded.")
                player = self.mk_engine_player(
                    args.engine, time=args.time, depth=args.depth, nodes=args.nodes
                )
                color: chess.Color
                match args.color:
                    case "white" | "w":
                        color = chess.WHITE
                    case "black" | "b":
                        color = chess.BLACK
                    case x:
                        assert_never(x)
                self.add_player(player, color)
            case x:
                assert_never(x)

    def _list_players(self, line_prefix: str = "") -> None:
        """Print a list of all players and clocks."""
        for player, color in self.players.items():
            color_str = "White" if color == chess.WHITE else "Black"
            if isinstance(player, ChessClock):
                print(f"{line_prefix}{color_str} clock: {player.show()}")
            else:
                print(f"{line_prefix}{color_str}: {player.name()}")

    match_argparser = argparse.ArgumentParser()
    match_subcmds = match_argparser.add_subparsers(dest="subcmd")
    match_subcmds.add_parser("show", aliases=["sh"], help="Show details about the current match.")
    match_start_argparser = match_subcmds.add_parser(
        "start",
        aliases=["s"],
        help="Start a new chess match on this move.  If a clock is set, "
        "it will be started and used in the match unless the --no-clock argument is given.",
    )
    match_subcmds.add_parser("pause", aliases=["p"], help="Pause an ongoing match.")
    match_subcmds.add_parser("resume", aliases=["r"], help="Resume a paused match.")
    match_subcmds.add_parser("reset", help="Remove all players and clocks.")

    @argparse_command(match_argparser, alias="ma")
    async def do_match(self, args) -> None:
        """Start a chess match against a player or between two players."""
        match args.subcmd:
            case "show" | "sh" | None:
                if not self.players:
                    if self.match_started():
                        print("There are no players in the match.")
                    else:
                        print("No match in progress.")
                        return
                else:
                    print("Players:")
                    self._list_players(line_prefix="  ")
                if not self.match_started():
                    print("The match is not started.")
                elif self.match_paused:
                    print("The match is paused.")
                elif self.match_result is not None:
                    print(f"The match is finished with result {self.match_result}")
                else:
                    print("The match is currently in progress.")
            case "start" | "s":
                if self.match_started():
                    raise CommandFailure(
                        "A match is already started.  "
                        "You can delete it with the `match reset` command."
                    )
                await self.start_match()
            case "pause" | "p":
                if not self.match_ongoing():
                    print("The match is not in progress.")
                else:
                    await self.pause_match()
                    print("The match has been paused.")
            case "resume" | "r":
                if self.match_paused:
                    await self.resume_match()
                    print("The match has been resumed.")
                else:
                    raise CommandFailure("The match is not paused.")
            case "reset":
                if self.match_ongoing():
                    print("The match is currently in progress.")
                    ans: bool = await self.yes_no_dialog("Do you want to reset anyway?")
                    if not ans:
                        print("Nothing has been reset.")
                        return
                await self.delete_match()
            case x:
                assert_never(x)
