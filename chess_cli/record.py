import asyncio
import os
import re
import sys
import tempfile
import threading
import time
from asyncio import subprocess
from contextlib import suppress
from dataclasses import dataclass
from typing import override

import chess
import pyaudio

from .base import Base, CommandFailure, InitArgs

SAMPLE_FORMAT = pyaudio.paInt16
SAMPLE_FORMAT_TO_FFMPEG = f"s16{"le" if sys.byteorder == "little" else "be"}"
MAX_CHANNELS: int = 2
ERROR_REGEX: re.Pattern = re.compile("error", flags=re.IGNORECASE)


@dataclass
class Recording:
    """Data for an ongoing recording."""

    ffmpeg_process: subprocess.Process
    ffmpeg_stderr_file: str  # Path to a temporary file holding the stderr from ffmpeg.
    audio_stream: pyaudio.Stream
    # A list of (timestamp, board) pairs where the timestamp is the value returned by
    terminate: threading.Event
    # `time.perf_counter()` when board was visited.
    boards: list[tuple[float, chess.Board]]

    def __del__(self) -> None:
        with suppress(FileNotFoundError):
            os.remove(self.ffmpeg_stderr_file)
            print("removed!")


class Record(Base):
    """An extention to chess-cli to record various things."""

    _curr_recording: Recording | None = None  # Currently active recording.

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

    async def start_recording(self) -> None:
        if self._curr_recording is not None:
            raise CommandFailure("A recording is already in progress.")
        audio: pyaudio.PyAudio = pyaudio.PyAudio()
        device_info = audio.get_default_input_device_info()
        device_index: int = device_info["index"]  # type: ignore
        name: str = device_info["name"]  # type: ignore
        sample_rate: int = int(device_info["defaultSampleRate"])
        channels: int = min(device_info["maxInputChannels"], MAX_CHANNELS)  # type: ignore
        print(f"Connecting to {name} with {sample_rate} kHz and {channels} channels.")

        ffmpeg_stderr_fd, ffmpeg_stderr_file = tempfile.mkstemp()
        ffmpeg_process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-n",
            "-f",
            SAMPLE_FORMAT_TO_FFMPEG,
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            "-i",
            "pipe:",
            "-c:a",
            "libopus",
            "-b:a",
            "64k",
            "a.opus",
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=ffmpeg_stderr_fd,
        )

        ffmpeg_stdin: asyncio.StreamWriter | None = ffmpeg_process.stdin
        assert ffmpeg_stdin is not None
        terminate: threading.Event = threading.Event()

        def stream_callback(
            in_data: bytes | None, _frame_count: int, _time_info, _status_flags
        ) -> tuple[None, int]:
            assert in_data is not None
            if ffmpeg_stdin.is_closing():
                action = pyaudio.paAbort
            else:
                try:
                    ffmpeg_stdin.write(in_data)
                except Exception as e:
                    action = pyaudio.paAbort
                    print(f"{type(e)}: {e}")
                else:
                    if terminate.is_set():
                        action = pyaudio.paComplete
                        ffmpeg_stdin.close()
                    else:
                        action = pyaudio.paContinue
            return (None, action)

        audio_stream = audio.open(
            rate=sample_rate,
            channels=channels,
            format=SAMPLE_FORMAT,
            input=True,
            input_device_index=device_index,
            stream_callback=stream_callback,
        )
        self._curr_recording = Recording(
            ffmpeg_process,
            ffmpeg_stderr_file,
            audio_stream,
            terminate,
            boards=[(time.perf_counter(), self.game_node.board())],
        )

    @override
    async def prompt(self) -> None:
        if self._curr_recording is not None:
            boards = self._curr_recording.boards
            if (board := self.game_node.board()) != boards[-1][1]:
                boards.append((time.perf_counter(), board))
        await super().prompt()

    async def stop_recording(self) -> None:
        if self._curr_recording is None:
            raise CommandFailure("No recording is in progress.")
        recording: Recording = self._curr_recording
        recording.terminate.set()
        await asyncio.sleep(0.1)
        recording.audio_stream.close()
        self._curr_recording = None
        await recording.ffmpeg_process.wait()
        with open(recording.ffmpeg_stderr_file) as f:
            stderr: str = f.read()
        if (retcode := recording.ffmpeg_process.returncode) != 0 or ERROR_REGEX.search(stderr):
            self.perror(f"ffmpeg seem to have failed, return code: {retcode}")
            self.perror(f"ffmpeg stderr: {stderr}")
        del recording  # Make sure `recording.ffmpeg_stderr_file` is deleted.
