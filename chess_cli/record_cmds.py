import cmd2

from .record import Record
from .repl import argparse_command


class RecordCmds(Record):
    """Basic commands related to recordings."""

    record_argparser = cmd2.Cmd2ArgumentParser()
    record_subcmds = record_argparser.add_subparsers(dest="subcmd")
    record_start_argparser = record_subcmds.add_parser("start", help="Start a recording.")
    record_stop_argparser = record_subcmds.add_parser("stop", help="Stop an ongoing recording.")

    @argparse_command(record_argparser)
    async def do_record(self, args) -> None:
        """Actions related to recording chess videos."""
        match args.subcmd:
            case "start":
                await self.start_recording()
            case "stop":
                await self.stop_recording()
            case _:
                raise AssertionError("Unsupported subcommand.")
