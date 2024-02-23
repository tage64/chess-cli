from argparse import ArgumentParser

from .record import Record
from .repl import CommandFailure, argparse_command


class RecordCmds(Record):
    """Basic commands related to recordings."""

    record_argparser = ArgumentParser()
    record_subcmds = record_argparser.add_subparsers(dest="subcmd")
    record_start_argparser = record_subcmds.add_parser("start", help="Start a recording.")
    record_pause_argparser = record_subcmds.add_parser(
        "pause", aliases=["p", "stop"], help="Pause/stop an ongoing recording."
    )
    record_resume_argparser = record_subcmds.add_parser("resume", help="Resume a paused recording.")
    record_save_argparser = record_subcmds.add_parser("save", help="Finish and save a recording.")

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
            case "pause":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                if self.recording.is_paused():
                    self.perror("The recording is already paused.")
                else:
                    self.recording.pause()
            case "resume":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                if not self.recording.is_paused():
                    self.perror("The recording is not paused.")
                else:
                    self.recording.resume()
            case "save":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                await self.save_recording()
            case "delete":
                if self.recording is None:
                    raise CommandFailure("No recording in progress.")
                self.delete_recording()
            case _:
                raise AssertionError("Unsupported subcommand.")
