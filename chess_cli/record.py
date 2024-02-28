import asyncio
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from asyncio import subprocess
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import override

import chess
import chess.svg
import more_itertools
import pyaudio

from .base import Base, InitArgs

SAMPLE_FORMAT = pyaudio.paInt16
SAMPLE_SIZE: int = pyaudio.get_sample_size(SAMPLE_FORMAT)
SAMPLE_FORMAT_TO_FFMPEG = f"s16{"le" if sys.byteorder == "little" else "be"}"
MAX_CHANNELS: int = 2
AUDIO_BITRATE: str = "48k"
# For reference on ffmpeg filters, see: <https://ffmpeg.org/ffmpeg-filters.html>.
AUDIO_FILTER: str = (
    "afftdn=noise_reduction=40:noise_floor=-70:track_noise=true,"
    "dynaudnorm=gausssize=21:correctdc=1:maxgain=4:altboundary=true"
)
VIDEO_INPUT_OPTS: list[str] = ["-width", "400", "-height", "400"]
# See <https://trac.ffmpeg.org/wiki/Encode/H.264> for more information on these parameters.
CRF: int = 28
PRESET: str = "slower"
TUNE: str = "stillimage"

FRAMES_PER_BUFFER: int = 8192
ERROR_REGEX: re.Pattern = re.compile("error", flags=re.IGNORECASE)
PROCESS_TERMINATE_TIMEOUT: float = 3.0  # Timeout for a process to terminate before killing it.

_ffmpeg_exe = shutil.which(b"ffmpeg") or shutil.which(
    b"ffmpeg", path=PurePath(".") / "ffmpeg" / "bin"
)
assert _ffmpeg_exe is not None, "ffmpeg was not found in your path or ./ffmpeg/bin/"
print(f"ffmpeg: {_ffmpeg_exe}")
FFMPEG_EXE: bytes = _ffmpeg_exe


class _StreamInfo:
    """Realtime information about an audio stream.

    This is shared in the stream callback to portaudio which runs in a separate thread,
    so all accesses must be protected by the lock.
    """

    sample_rate: int  # The sample rate for the stream.
    _received_frames: int = 0  # Number of received frames.
    _last_frame_timestamp: float | None = None  # The time when the last frame was recorded.
    # If the stream is paused, then this is the timestamp when the pause began, None otherwise.
    _is_paused_at: float | None = None
    # The total duration of all **completed** pauses since the last frame was received.
    # A **complete** pause is a pause that has been resumed.
    _total_pause_duration: float = 0.0
    _lock: threading.Lock  # A lock for this class.

    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._timestamp = time.perf_counter()

    def set_time(self, timestamp: float) -> None:
        """Call this as soon as the stream is started.

        It will set self._last_frame_timestamp to this value and in case no buffer has
        been received yet. It is important to call this before calling self.pause() or
        self.elapsed_time().
        """
        with self._lock:
            if self._last_frame_timestamp is None:
                self._last_frame_timestamp = timestamp

    def buffer_received(self, frames: int, input_buffer_adc_time: float) -> None:
        """A new buffer was received from portaudio.

        frames is the number of frames in the buffer and input_buffer_adc_time is the
        timestamp when the first sample was captured at the ADC input (as in portaudio's
        PaStreamCallbackTimeInfo).
        """
        with self._lock:
            self._last_frame_timestamp = input_buffer_adc_time + frames / self.sample_rate
            self._received_frames += frames
            if self._is_paused_at is not None:
                # A buffer was received after the pause began.
                self._is_paused_at = max(self._is_paused_at, self._last_frame_timestamp)
            self._total_pause_duration = 0.0

    def pause(self, current_timestamp: float) -> None:
        """Pause the stream."""
        with self._lock:
            assert self._last_frame_timestamp is not None
            assert self._is_paused_at is None
            assert current_timestamp >= self._last_frame_timestamp
            self._is_paused_at = current_timestamp

    def resume(self, current_timestamp: float) -> None:
        """Resume a paused stream."""
        with self._lock:
            assert self._is_paused_at is not None
            assert self._is_paused_at <= current_timestamp
            self._total_pause_duration += self._is_paused_at - current_timestamp
            self._is_paused_at = None

    def is_paused(self) -> bool:
        """Check whether the stream is paused."""
        with self._lock:
            return self._is_paused_at is not None

    def elapsed_time(self, current_timestamp: float) -> float:
        """Get an estimate of the elapsed time in the audio stream (excluding pauses).

        Note that although this is a good estimate, it may not be monotonically
        increasing.
        """
        with self._lock:
            assert self._last_frame_timestamp is not None
            received_time: float = self._received_frames / self.sample_rate
            t: float = current_timestamp if self._is_paused_at is None else self._is_paused_at
            return received_time + t - self._last_frame_timestamp


@dataclass
class Recording:
    """Data for an ongoing recording."""

    ffmpeg_process: subprocess.Process
    audio_file: str  # Path to the temporary audio file.
    ffmpeg_output_file: str  # Path to a temporary file holding the stdout/stderr from ffmpeg.
    audio_stream: pyaudio.Stream
    stream_info: _StreamInfo
    # A list of (timestamp, board) pairs where the timestamp is the value returned by
    terminate: threading.Event
    boards: list[chess.Board]  # A list of the positions to show in the video.
    timestamps: list[float]  # Timestamps for all boards.
    _is_cleaned: bool = False  # Set to true after self.cleanup() is called.

    def is_paused(self) -> bool:
        """Check if the recording is paused."""
        return self.stream_info.is_paused()

    def pause(self) -> None:
        """Pause the recording."""
        self.audio_stream.stop_stream()
        self.stream_info.pause(self.audio_stream.get_time())

    def resume(self) -> None:
        """Resume the recording."""
        self.audio_stream.start_stream()
        self.stream_info.resume(self.audio_stream.get_time())

    def elapsed_time(self) -> float:
        """Get the number of seconds in this recording without pauses."""
        return self.stream_info.elapsed_time(self.audio_stream.get_time())

    def set_board(self, board: chess.Board) -> None:
        """Set the current board in the video.

        Assumes that the recording is not paused.
        """
        assert not self.is_paused()
        timestamp: float = self.elapsed_time()
        with self.stream_info._lock:
            print(f"Received: {self.stream_info._received_frames / self.stream_info.sample_rate}")
            print(f"Elapsed {timestamp}")
            print(f"Latency: {self.audio_stream.get_input_latency()}")
        if timestamp < self.timestamps[-1]:
            print(
                "Warning: Recording.set_board(): timestamps are not monotone: "
                f"current timestamp: {timestamp}, previous timestamp: {self.timestamps[-1]}"
            )
            return
        if board != self.boards[-1]:
            self.boards.append(board)
            self.timestamps.append(timestamp)

    async def stop(self) -> None:
        """Stop the recording.

        No pause/resumes can take place after this.
        """
        self.terminate.set()
        # Wait for the audio stream to be closed.
        start_time: float = time.perf_counter()
        buffer_duration: float = FRAMES_PER_BUFFER / self.stream_info.sample_rate
        while (
            self.audio_stream.is_active() and time.perf_counter() - start_time < buffer_duration * 2
        ):
            await asyncio.sleep(buffer_duration / 10)
        was_active: bool = self.audio_stream.is_active()
        self.audio_stream.close()
        if was_active:
            # The pipe to ffmpeg was not closed so we have to kill ffmpeg.
            self.ffmpeg_process.terminate()
            try:
                await asyncio.wait_for(self.ffmpeg_process.wait(), PROCESS_TERMINATE_TIMEOUT)
            except TimeoutError:
                print("Warning: ffmpeg did not listen to SIGTERM so we have to kill it.")
                self.ffmpeg_process.kill()
                await self.ffmpeg_process.wait()
        else:
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

    async def save(
        self, output_file: PurePath, override_output_file: bool = False, no_cleanup: bool = False
    ) -> None:
        """Save the recording.

        May only be called after `self.stop()`.
        """
        assert len(self.boards) == len(self.timestamps)
        svg_dir: Path = Path(tempfile.mkdtemp())
        try:
            svg_files: list[PurePath] = [svg_dir / f"{i}.svg" for i in range(len(self.boards))]
            for board, svg_file_path in zip(self.boards, svg_files, strict=False):
                with open(svg_file_path, "w+") as f:
                    f.write(chess.svg.board(board))
            durations: Iterable[float] = map(
                lambda x: x[1] - x[0], more_itertools.pairwise(self.timestamps)
            )
            concat_file_lines: Iterable[str] = more_itertools.interleave_longest(
                (f"file '{f}'\n" for f in svg_files), (f"duration {d}\n" for d in durations)
            )
            concat_fd, concat_file_name = tempfile.mkstemp(suffix=".txt", text=True)
            try:
                with os.fdopen(concat_fd, mode="w") as concat_file:
                    concat_file.writelines(concat_file_lines)
                ffmpeg_args: list[str] = [
                    FFMPEG_EXE,
                    "-hide_banner",
                    "-v",
                    "error",
                    "-i",
                    self.audio_file,
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    *VIDEO_INPUT_OPTS,
                    "-i",
                    concat_file_name,
                    "-c:a",
                    "copy",
                    "-c:v",
                    "libx264",
                    "-crf",
                    str(CRF),
                    "-preset",
                    PRESET,
                    "-tune",
                    TUNE,
                    "-pix_fmt",
                    "yuv420p",
                    str(output_file),
                ]
                if override_output_file:
                    ffmpeg_args.append("-y")
                proc = await asyncio.create_subprocess_exec(*ffmpeg_args)
                await proc.wait()
            finally:
                if no_cleanup:
                    print(f"Forgetting concat file: {concat_file_name}")
                else:
                    os.remove(concat_file_name)
        finally:
            if no_cleanup:
                print(f"Forgetting directory with SVG files: {svg_dir}")
            else:
                shutil.rmtree(svg_dir)

    def cleanup(self, dry_run: bool = False) -> None:
        """Remove temporary files."""
        if dry_run:
            print(f"Forgetting audio file: {self.audio_file}")
        else:
            with suppress(FileNotFoundError):
                os.remove(self.audio_file)
        with suppress(FileNotFoundError):
            os.remove(self.ffmpeg_output_file)
        self._is_cleaned = True

    def __del__(self) -> None:
        if not self._is_cleaned:
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

        _, audio_file = tempfile.mkstemp(suffix=".aac")
        ffmpeg_output_fd, ffmpeg_output_file = tempfile.mkstemp()
        ffmpeg_process = await asyncio.create_subprocess_exec(
            FFMPEG_EXE,
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
            "-af",
            AUDIO_FILTER,
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            audio_file,
            stdin=subprocess.PIPE,
            stdout=ffmpeg_output_fd,
            stderr=subprocess.STDOUT,
        )

        ffmpeg_stdin: asyncio.StreamWriter | None = ffmpeg_process.stdin
        assert ffmpeg_stdin is not None
        terminate: threading.Event = threading.Event()
        stream_info: _StreamInfo = _StreamInfo(sample_rate)

        def stream_callback(
            in_data: bytes | None, frame_count: int, time_info: Mapping[str, float], _status_flags
        ) -> tuple[None, int]:
            try:
                assert in_data is not None
                assert (
                    len(in_data) == frame_count * channels * SAMPLE_SIZE
                ), f"frames={frame_count}, bytes={len(in_data)}, channels={channels}"
                assert frame_count <= FRAMES_PER_BUFFER
                stream_info.buffer_received(frame_count, time_info["input_buffer_adc_time"])
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
        stream_info.set_time(audio_stream.get_time())
        self.recording = Recording(
            ffmpeg_process,
            audio_file,
            ffmpeg_output_file,
            audio_stream,
            stream_info,
            terminate=terminate,
            boards=[self.game_node.board()],
            timestamps=[0.0],
        )

    @override
    async def prompt(self) -> None:
        if self.recording is not None and not self.recording.is_paused():
            self.recording.set_board(self.game_node.board())
        await super().prompt()

    async def save_recording(
        self, output_file: PurePath, override_output_file: bool = False, no_cleanup: bool = False
    ) -> None:
        assert self.recording is not None
        await self.recording.stop()
        await self.recording.save(
            output_file=output_file.with_suffix(".mp4"),
            override_output_file=override_output_file,
            no_cleanup=no_cleanup,
        )
        self.recording.cleanup(dry_run=no_cleanup)
        self.recording = None

    async def delete_recording(self) -> None:
        assert self.recording is not None
        await self.recording.stop()
        self.recording.cleanup()
        self.recording = None

    # Close recording on exit.
    @override
    async def cmd_loop(self) -> None:
        try:
            await super().cmd_loop()
        finally:
            if self.recording is not None and not self.recording.terminate.is_set():
                print("Warning: Cancelling recording.")
                await self.delete_recording()
