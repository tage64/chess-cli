import asyncio
import os
import re
import sys
import tempfile
import threading
import time
import traceback
from asyncio import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePath
from typing import override

import chess
import chess.svg
import pyaudio

from .base import Base, InitArgs

SAMPLE_FORMAT = pyaudio.paInt16
SAMPLE_SIZE: int = pyaudio.get_sample_size(SAMPLE_FORMAT)
SAMPLE_FORMAT_TO_FFMPEG = f"s16{"le" if sys.byteorder == "little" else "be"}"
MAX_CHANNELS: int = 2
FRAMES_PER_BUFFER: int = 8192
ERROR_REGEX: re.Pattern = re.compile("error", flags=re.IGNORECASE)
PROCESS_TERMINATE_TIMEOUT: float = 3.0  # Timeout for a process to terminate before killing it.


@dataclass
class Recording:
    """Data for an ongoing recording."""

    ffmpeg_process: subprocess.Process
    audio_file: str  # Path to the temporary audio file.
    ffmpeg_output_file: str  # Path to a temporary file holding the stdout/stderr from ffmpeg.
    audio_stream: pyaudio.Stream
    # A list of (timestamp, board) pairs where the timestamp is the value returned by
    terminate: threading.Event
    # `time.perf_counter()` when board was visited.
    boards: list[tuple[float, chess.Board]]
    # The maximum duration for an audio buffer from portaudio.
    buffer_max_duration: float
    total_pause_time: float = 0.0  # The summed duration of all pauses in the recording.
    # is_paused_at = the time when the recording was paused if it is currently paused else None:
    is_paused_at: float | None = None

    def is_paused(self) -> bool:
        """Check if the recording is paused."""
        return self.is_paused_at is None

    def pause(self) -> None:
        """Pause the recording."""
        assert self.is_paused_at is None
        self.audio_stream.stop_stream()
        self.is_paused_at = self.audio_stream.get_time()

    def resume(self) -> None:
        """Resume the recording."""
        assert self.is_paused_at is not None
        self.audio_stream.start_stream()
        self.total_pause_time += self.audio_stream.get_time() - self.is_paused_at
        self.is_paused_at = None

    def set_board(self, board: chess.Board) -> None:
        """Set the current board in the video.

        Assumes that the recording is not paused.
        """
        assert not self.is_paused()
        if board != self.boards[-1][1]:
            self.boards.append((self.audio_stream.get_time() - self.total_pause_time, board))

    async def stop(self) -> None:
        """Stop the recording.

        No pause/resumes can take place after this.
        """
        self.terminate.set()
        # Wait for the audio stream to be closed.
        start_time: float = time.perf_counter()
        while (
            self.audio_stream.is_active()
            and time.perf_counter() - start_time < self.buffer_max_duration * 2
        ):
            await asyncio.sleep(self.buffer_max_duration / 10)
        if self.audio_stream.is_active():
            self.audio_stream.close()
            # The pipe to ffmpeg was not closed so we have to kill ffmpeg.
            self.ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(self.ffmpeg_process.wait(), PROCESS_TERMINATE_TIMEOUT)
            except TimeoutError:
                print("Warning: ffmpeg did not listen to SIGTERM so we have to kill it.")
                self.ffmpeg_process.kill()
                await self.ffmpeg_process.wait()
        else:
            self.audio_stream.close()
            try:
                await asyncio.wait_for(self.ffmpeg_process.wait(), PROCESS_TERMINATE_TIMEOUT)
            except TimeoutError:
                print("ffmpeg did not terminate properly so we have to kill it.")
                self.ffmpeg_process.kill()
                await self.ffmpeg_process.wait()
            with open(self.ffmpeg_output_file) as f:
                output: str = f.read()
            if (retcode := self.ffmpeg_process.returncode) != 0 or ERROR_REGEX.search(output):
                print(f"ffmpeg seem to have failed, return code: {retcode}")
                print(f"ffmpeg output: {output}")

    async def save(self) -> None:
        """Save the recording.

        May only be called after `self.stop()`.
        """
        with tempfile.TemporaryDirectory() as svg_dir:
            svg_dir_path: PurePath = PurePath(svg_dir)
            concat_file_lines: list[str] = []
            prev_timestamp: float = self.boards[0][0]
            for i, (timestamp, board) in enumerate(self.boards):
                duration: float = timestamp - prev_timestamp
                assert duration >= 0
                svg_file_path: PurePath = svg_dir_path / f"{i}.svg"
                with open(svg_file_path, "w+") as f:
                    f.write(chess.svg.board(board))
                concat_file_lines.append(f"file '{svg_file_path}'")
                concat_file_lines.append(f"duration {duration}")
            # Due to a quirk, the last image has to be specified twice,
            # the 2nd time without any duration directive.
            concat_file_lines.append(concat_file_lines[-2])
            with tempfile.NamedTemporaryFile(mode="w+") as concat_file:
                concat_file.writelines(concat_file_lines)
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    "a.opus",
                    "-f",
                    "concat",
                    "-i",
                    concat_file.name,
                    "-c:a",
                    "copy",
                    "-c:v",
                    "libx264",
                    "out.mp4",
                )
                await proc.wait()

    def cleanup(self) -> None:
        """Remove temporary files."""
        with suppress(FileNotFoundError):
            os.remove(self.audio_file)
        with suppress(FileNotFoundError):
            os.remove(self.ffmpeg_output_file)
            print("removed!")

    def __del__(self) -> None:
        self.cleanup()


class Record(Base):
    """An extention to chess-cli to record various things."""

    recording: Recording | None = None  # Currently active recording.

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

    async def start_recording(self) -> None:
        assert self.recording is None
        audio: pyaudio.PyAudio = pyaudio.PyAudio()
        device_info = audio.get_default_input_device_info()
        device_index: int = device_info["index"]  # type: ignore
        name: str = device_info["name"]  # type: ignore
        sample_rate: int = int(device_info["defaultSampleRate"])
        channels: int = min(device_info["maxInputChannels"], MAX_CHANNELS)  # type: ignore
        print(f"Connecting to {name} with {sample_rate} kHz and {channels} channels.")

        _, audio_file = tempfile.mkstemp(suffix=".opus")
        ffmpeg_output_fd, ffmpeg_output_file = tempfile.mkstemp()
        ffmpeg_process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-v",
            "error",
            "-y",
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
            audio_file,
            stdin=subprocess.PIPE,
            stdout=ffmpeg_output_fd,
            stderr=subprocess.STDOUT,
        )

        ffmpeg_stdin: asyncio.StreamWriter | None = ffmpeg_process.stdin
        assert ffmpeg_stdin is not None
        terminate: threading.Event = threading.Event()

        def stream_callback(
            in_data: bytes | None, frame_count: int, _time_info, _status_flags
        ) -> tuple[None, int]:
            try:
                assert in_data is not None
                assert (
                    len(in_data) == frame_count * channels * SAMPLE_SIZE
                ), f"frames={frame_count}, bytes={len(in_data)}, channels={channels}"
                assert frame_count <= FRAMES_PER_BUFFER
                if ffmpeg_stdin.is_closing():
                    action = pyaudio.paAbort
                    print("is closing")
                else:
                    ffmpeg_stdin.write(in_data)
                    if terminate.is_set():
                        action = pyaudio.paComplete
                        ffmpeg_stdin.close()
                        print("closing")
                    else:
                        action = pyaudio.paContinue
            except Exception as e:
                action = pyaudio.paAbort
                print(f"Exception in portaudio callback: {type(e)}.__name__ {e}:", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
                with suppress(Exception):
                    ffmpeg_stdin.close()
            return (None, action)

        audio_stream = audio.open(
            rate=sample_rate,
            channels=channels,
            format=SAMPLE_FORMAT,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=FRAMES_PER_BUFFER,
            stream_callback=stream_callback,
        )
        self.recording = Recording(
            ffmpeg_process,
            audio_file,
            ffmpeg_output_file,
            audio_stream,
            terminate,
            boards=[(time.perf_counter(), self.game_node.board())],
            buffer_max_duration=FRAMES_PER_BUFFER / sample_rate,
        )

    @override
    async def prompt(self) -> None:
        if self.recording is not None:
            self.recording.set_board(self.game_node.board())
        await super().prompt()

    async def save_recording(self) -> None:
        assert self.recording is not None
        await self.recording.stop()
        await self.recording.save()
        self.delete_recording()

    def delete_recording(self) -> None:
        assert self.recording is not None
        self.recording.cleanup()
        self.recording = None
