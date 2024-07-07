import asyncio
from datetime import timedelta
from typing import override

import chess.pgn

from .match import Match, Player, PlayResult
from .utils import show_time


class ChessClock(Player):
    """A chess clock for one player."""

    # The initial time.
    init_time: timedelta
    # Increment per move.
    increment: timedelta
    # Last known remaining time.
    last_known_time: float
    # A reference to the current eventloop.
    loop: asyncio.AbstractEventLoop
    # The loop.time() when last_known_time was set.
    reference_time: float | None = None
    # When this timer is finished, I lost on time.
    timer_handle: asyncio.TimerHandle | None = None
    # This event will be set when the time is out.
    timeout_event: asyncio.Event

    def __init__(self, time: timedelta, increment: timedelta = timedelta(0)) -> None:
        self.init_time = time
        self.increment = increment
        self.last_known_time = time.total_seconds()
        self.loop = asyncio.get_running_loop()
        self.timeout_event = asyncio.Event()

    def _update_last_known_time(self) -> None:
        if self.reference_time is not None:
            new_reference_time = self.loop.time()
            self.last_known_time -= new_reference_time - self.reference_time
            self.reference_time = new_reference_time

    def _start_timer(self) -> None:
        if self.reference_time is not None:
            self.timer_handle = self.loop.call_at(
                self.reference_time + self.last_known_time, self.timeout_event.set
            )

    def _stop_timer(self) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None

    def remaining_time(self) -> timedelta:
        self._update_last_known_time()
        return timedelta(seconds=max(self.last_known_time, 0))

    @override
    async def play(self, _) -> PlayResult:
        if self.reference_time is None:
            self.reference_time = self.loop.time()
            self._start_timer()
        try:
            await self.timeout_event.wait()
        finally:
            self._stop_timer()
            self._update_last_known_time()
            self.reference_time = None
        return "timeout"

    @override
    def next_move(self) -> None:
        self.last_known_time += self.increment.total_seconds()
    def set_time(self, time: timedelta) -> None:
        self._stop_timer()
        self._update_last_known_time()
        self.init_time += time - self.remaining_time()
        self.last_known_time = time.total_seconds()
        self._start_timer()

    def show(self) -> str:
        if (secs := self.init_time.total_seconds()) % 60 == 0:
            minutes = int(secs) // 60
            return f"{minutes}+{show_time(self.increment, short=True)}"
        return f"{show_time(self.init_time)}  +  {show_time(self.increment)} increment"

    @override
    def name(self) -> str:
        return f"Chess clock: {self.show()}"


class Clock(Match):
    """An extention to chess-cli to play chess matches with a chess clock."""

    def get_clocks(self) -> tuple[ChessClock | None, ChessClock | None]:
        """Return the chess clocks for white and black respectively if any."""
        white_clock, black_clock = None, None
        for player, color in self.players.items():
            if isinstance(player, ChessClock):
                if color == chess.WHITE:
                    white_clock = player
                else:
                    black_clock = player
            if white_clock is not None and black_clock is not None:
                break
                return player
        return white_clock, black_clock

    def get_clock_by(self, color: chess.Color) -> ChessClock | None:
        """Get the chess clock for the specified color if any."""
        return self.get_clocks()[0 if color == chess.WHITE else 1]

    def add_clock(self, clock: ChessClock, color: chess.Color) -> None:
        """Add the clock to the match. Only one clock can exist per color."""
        assert self.get_clock_by(color) is None, "A clock of the specified color is already set."
        self.add_player(clock, color)

    def delete_clocks(self) -> None:
        """Delete the clocks if any. Assumes that the match is not started."""
        assert not self.match_started()
        clocks: list[ChessClock] = []
        for player in self.players:
            if isinstance(player, ChessClock):
                clocks.append(player)
        for clock in clocks:
            del self.players[clock]
