from argparse import ArgumentParser
from pathlib import PurePath
from typing import assert_never

from .record import Record
from .repl import CommandFailure, argparse_command
from .utils import show_time


class RecordCmds(Record):
    """Basic commands related to recordings."""

    record_argparser = ArgumentParser()
    record_subcmds = record_argparser.add_subparsers(dest="subcmd")
    record_start_argparser = record_subcmds.add_parser("start", help="Start a recording.")
    record_pause_argparser = record_subcmds.add_parser(
        "pause", aliases=["p", "stop"], help="Pause/stop an ongoing recording."
    )
    record_resume_argparser = record_subcmds.add_parser(
        "resume", aliases=["r"], help="Resume a paused recording."
    )
    record_save_argparser = record_subcmds.add_parser("save", help="Finish and save a recording.")
    record_save_argparser.add_argument(
        "output_file", type=PurePath, help="The name of the output file."
    )
    record_save_argparser.add_argument(
        "-y",
        "--override",
        action="store_true",
        help="Override the output file if it already exists.",
    )
    record_save_argparser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't remove any created temporary files. Mostly useful for debugging.",
    )
    record_delete_argparser = record_subcmds.add_parser(
        "delete", help="Delete the ongoing recording."
    )

    @argparse_command(record_argparser)
    async def do_record(self, args) -> None:
        """Actions related to recording chess videos."""
        match args.subcmd:
            case "start":
                if self.recording is not None:
                    raise CommandFailure(
                        "A recording is already in progress. Please save it with 'record save' or"
                        " delete it with 'record delete'."
                    )
                await self.start_recording()
                print("Recording successfully started.")
            case "pause" | "p" | "stop":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                if self.recording.is_paused():
                    self.perror("The recording is already paused.")
                else:
                    self.recording.pause()
                    time: float = self.recording.stream_info.elapsed_time()
                    print(f"Paused recording at {show_time(time)}")
            case "resume" | "r":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                if not self.recording.is_paused():
                    self.perror("The recording is not paused.")
                else:
                    time: float = self.recording.stream_info.elapsed_time()
                    self.recording.resume()
                    print(f"Resumed recording at {show_time(time)}")
            case "save":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                duration: float = await self.save_recording(
                    output_file=args.output_file,
                    override_output_file=args.override,
                    no_cleanup=args.no_cleanup,
                )
                print(f"Recording successfully saved to {args.output_file}")
                print(f"It was {show_time(duration)} long.")
            case "delete":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                await self.delete_recording()
                print("Recording deleted.")
            case x:
                assert_never(x)
