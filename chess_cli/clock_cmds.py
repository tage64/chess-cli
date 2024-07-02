import argparse
from typing import assert_never

from .clock import Clock
from .repl import argparse_command, command
from .utils import parse_time, show_time


class ClockCmds(Clock):
    """Basic commands related to clocks."""

    @command(alias="t")
    def do_time(self, _) -> None:
        """Get the current time."""
        if (clock := self.clock) is not None:
            print(
                f"{show_time(clock.my_time(), short=True)} "
                f"-- {show_time(clock.opponents_time(), short=True)}"
            )

    clock_argparser = argparse.ArgumentParser()
    clock_subcmds = clock_argparser.add_subparsers(dest="subcmd")
    clock_set_argparser = clock_subcmds.add_parser(
        "set", help="Set the clock in [clk ...] annotations in the PGN comment."
    )
    clock_set_argparser.add_argument(
        "time", type=parse_time, help="The initial time for each player."
    )
    clock_set_argparser.add_argument(
        "increment", type=parse_time, nargs="?", help="Increment per move."
    )
    clock_subcmds.add_parser("show", help="Show the time control.")
    clock_subcmds.add_parser("start", help="Start the clock.")
    clock_subcmds.add_parser("stop", aliases=["pause", "p"], help="Pause the clock.")

    @argparse_command(clock_argparser, alias=["cl", "clk"])
    def do_clock(self, args) -> None:
        """Set and get clock settings."""
        match args.subcmd:
            case "set":
                self.set_clock(args.time, args.increment)
            case "show" | None:
                if (clock := self.clock) is not None:
                    print(clock.show())
            case "start":
                if (clock := self.clock) is not None:
                    if clock.is_timeout():
                        print("The time is out.")
                    elif clock.is_started():
                        print("The clock is already started.")
                    else:
                        self.start_clock()
                        print("Clock started!")
                else:
                    print("No time control set. Set with `clock set <TIME> <INC>`")
            case x:
                assert_never(x)
