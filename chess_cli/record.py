import asyncio
import sys
from asyncio import subprocess
from dataclasses import dataclass

import pyaudio

from . import repl
from .base import Base, CommandFailure, InitArgs

SAMPLE_FORMAT = pyaudio.paInt16
SAMPLE_FORMAT_TO_FFMPEG = f"s16{"le" if sys.byteorder == "little" else "be"}"
MAX_CHANNELS: int = 2


@dataclass
class Recording:
    """Data for an ongoing recording."""

    ffmpeg_process: subprocess.Process
    audio_stream: pyaudio.Stream
    shall_terminate: bool = False


class Record(Base):
    """An extention to chess-cli to record various things."""

    _curr_recording: Recording | None = None  # Currently active recording.

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

    @repl.command()
    async def do_start_recording(self, _) -> None:
        if self._curr_recording is not None:
            raise CommandFailure(f"A recording is already in progress.")
        audio: pyaudio.PyAudio = pyaudio.PyAudio()
        device_info = audio.get_default_input_device_info()
        device_index: int = device_info["index"]  # type: ignore
        name: str = device_info["name"]  # type: ignore
        sample_rate: int = int(device_info["defaultSampleRate"])
        channels: int = min(device_info["maxInputChannels"], MAX_CHANNELS)  # type: ignore
        print(f"Connecting to {name} with {sample_rate} kHz and {channels} channels.")
        ffmpeg_process = await asyncio.create_subprocess_exec(
            "ffmpeg",
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
            "-y",
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        ffmpeg_stdin: asyncio.StreamWriter | None = ffmpeg_process.stdin
        assert ffmpeg_stdin is not None

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
                    if self._curr_recording is not None and self._curr_recording.shall_terminate:
                        action = pyaudio.paComplete
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
        self._curr_recording = Recording(ffmpeg_process, audio_stream)

    @repl.command()
    async def do_terminate(self, _) -> None:
        if self._curr_recording is None:
            raise CommandFailure(f"No recording is in progress.")
        self._curr_recording.shall_terminate = True
        while not self._curr_recording.audio_stream.is_stopped():
            await asyncio.sleep(0.1)
        self._curr_recording.audio_stream.close()
