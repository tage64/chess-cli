from abc import ABC, abstractmethod
from datetime import timedelta
from time import perf_counter
from typing import override

import chess.pgn

from .base import Base, InitArgs
from .utils import show_time


class ChessClock(ABC):
    """Abstract interface for a chess clock."""

    @abstractmethod
    def show(self) -> str:
        """A short string describing the time control."""
        ...

    @abstractmethod
    def my_time(self) -> timedelta:
        """The currently thinking players remaining time."""
        ...

    @abstractmethod
    def opponents_time(self) -> timedelta:
        """The player which is currently not to move's remaining time."""
        ...

    def is_timeout(self) -> bool:
        return self.my_time() <= timedelta(0) or self.opponents_time() <= timedelta(0)

    @abstractmethod
    def start(self) -> None:
        """Start the clock. By default, it should not be started after running the constructor."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Pause the clock without resetting the time."""
        ...

    @abstractmethod
    def is_started(self) -> bool:
        """Return True iff the clock has been started."""
        ...

    @abstractmethod
    def move(self) -> None:
        """Make a move so the clock flips."""
        ...

    @abstractmethod
    def set_my_time(self, time: timedelta) -> None:
        """Set the currently thinking players time."""
        ...

    @abstractmethod
    def set_opponents_time(self, time: timedelta) -> None:
        """Set player not to move's time."""
        ...


class SimpleClock(ChessClock):
    """A very simple chess clock with no increment."""

    my_init_time: timedelta
    opponents_init_time: timedelta
    opponents_time_: timedelta
    # my_last_time - (<CURRENT_TIME> - time_reference) should be the current `self.my_time`
    my_last_time: timedelta
    # reference_time is None if the clock is not started.
    reference_time: float | None = None

    def __init__(self, my_time: timedelta, opponents_time: timedelta | None = None) -> None:
        self.my_init_time = my_time
        self.opponents_init_time = my_time
        self.my_last_time = my_time
        self.opponents_time_ = opponents_time if opponents_time is not None else my_time

    def _update_my_last_time(self) -> None:
        if self.reference_time is not None:
            new_reference_time = perf_counter()
            self.my_last_time -= timedelta(seconds=new_reference_time - self.reference_time)
            self.reference_time = new_reference_time

    @override
    def my_time(self) -> timedelta:
        self._update_my_last_time()
        return max(self.my_last_time, timedelta(0))

    @override
    def opponents_time(self) -> timedelta:
        return max(self.opponents_time_, timedelta(0))

    @override
    def start(self) -> None:
        if self.reference_time is None:
            self.reference_time = perf_counter()

    @override
    def is_started(self) -> bool:
        return self.reference_time is not None

    @override
    def stop(self) -> None:
        self._update_my_last_time()
        self.reference_time = None

    @override
    def move(self) -> None:
        self.my_last_time, self.opponents_time_ = self.opponents_time_, self.my_time()
        self.my_init_time, self.opponents_init_time = (self.opponents_init_time, self.my_init_time)

    def set_my_time(self, time: timedelta) -> None:
        self._update_my_last_time()
        self.my_last_time = time

    def set_opponents_time(self, time: timedelta) -> None:
        self.opponents_time_ = time

    @override
    def show(self) -> str:
        res = show_time(self.my_init_time)
        if self.my_init_time != self.opponents_init_time:
            res += " against " + show_time(self.opponents_init_time)
        return res


class IncrementalClock(ChessClock):
    """A chess clock with increment."""

    base_clock: ChessClock
    increment: timedelta

    def __init__(self, base_clock: ChessClock, increment: timedelta) -> None:
        self.base_clock = base_clock
        self.increment = increment

    @override
    def my_time(self) -> timedelta:
        return self.base_clock.my_time()

    @override
    def opponents_time(self) -> timedelta:
        return self.base_clock.opponents_time()

    @override
    def start(self) -> None:
        self.base_clock.start()

    @override
    def is_started(self) -> bool:
        return self.base_clock.is_started()

    @override
    def stop(self) -> None:
        self.base_clock.stop()

    @override
    def move(self) -> None:
        self.base_clock.move()
        self.set_opponents_time(self.opponents_time() + self.increment)

    @override
    def set_my_time(self, time: timedelta) -> None:
        self.base_clock.set_my_time(time)

    @override
    def set_opponents_time(self, time: timedelta) -> None:
        self.base_clock.set_opponents_time(time)

    @override
    def show(self) -> str:
        return f"{self.base_clock.show()}; {show_time(self.increment)} increment"


class Clock(Base):
    """An extention to chess-cli to add a chess clock."""

    clocks: dict[chess.pgn.Game, ChessClock]
    # The last node when the clock was used in a certain game.
    clock_nodes: dict[chess.pgn.Game, chess.pgn.GameNode]

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)
        self.clocks = {}
        self.clock_nodes = {}

    @property
    def clock(self) -> ChessClock | None:
        """The clock for the current game if any."""
        return self.clocks.get(self.game_node.game())

    @property
    def clock_node(self) -> chess.pgn.GameNode | None:
        """The last node where the clock was ticking in this game."""
        return self.clock_nodes.get(self.game_node.game())

    @clock_node.setter
    def clock_node(self, node: chess.pgn.GameNode) -> None:
        self.clock_nodes[node.game()] = node

    def set_clock(self, time: timedelta, increment: timedelta | None) -> None:
        clock = SimpleClock(time)
        if increment is not None:
            clock = IncrementalClock(clock, increment)
        self.clocks[self.game_node.game()] = clock

    def pgn_write_clock(self) -> None:
        """Write the current remaining time to a [%clk ...] annotation in the PGN comment."""
        if self.clock is not None:
            self.game_node.set_clock(self.clock.my_time().total_seconds())

    def start_clock(self) -> None:
        """Start the clock; set_clock() must have been called before."""
        assert self.clock is not None
        self.clock_node = self.game_node
        self.pgn_write_clock()
        self.clock.start()

    async def pre_prompt(self) -> None:
        await super().pre_prompt()
        if self.clock is not None and self.clock.is_started():
            if self.clock.is_timeout():
                print("The time is out!!")
                self.clock.stop()
            else:
                if self.game_node.parent is self.clock_node and self.game_node.parent is not None:
                    # One move has been played.
                    self.clock_node = self.game_node
                    self.clock.move()
                    self.pgn_write_clock()
