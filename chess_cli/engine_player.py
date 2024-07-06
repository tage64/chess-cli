from datetime import timedelta
from typing import override

import chess
import chess.engine
import chess.pgn

from .clock import Clock
from .engine import Engine
from .match import Player, PlayerError, PlayResult


class EnginePlayer(Clock, Engine):
    """An extention to chess-cli which supports engines as players."""

    def mk_engine_player(
        self,
        engine: str,
        time: timedelta | None = None,
        depth: int | None = None,
        nodes: int | None = None,
    ) -> Player:
        """Create a player from a chess machine.

        :param engine: The engine, must be loaded hence exist in `self.loaded_engines`
        :param time: Fixed time to think per move.
        :param depth: Fixed depth per move.
        :param nodes: Fixed number of nodes per move.
        """
        loaded_engine = self.loaded_engines[engine]
        self_ = self

        class Player_(Player):
            """The actual player class."""

            @override
            def name(self) -> str:
                return loaded_engine.loaded_name

            @override
            async def play(self, pos: chess.pgn.GameNode) -> PlayResult:
                # Find out if any clocks are used. This should maybe be done more elegant,
                # but it works for now.
                white_clock, black_clock = self_.get_clocks()
                white_time, white_inc = (
                    (
                        white_clock.remaining_time().total_seconds(),
                        white_clock.increment.total_seconds(),
                    )
                    if white_clock is not None
                    else (None, None)
                )
                black_time, black_inc = (
                    (
                        black_clock.remaining_time().total_seconds(),
                        black_clock.increment.total_seconds(),
                    )
                    if black_clock is not None
                    else (None, None)
                )
                limit = chess.engine.Limit(
                    white_clock=white_time,
                    black_clock=black_time,
                    white_inc=white_inc,
                    black_inc=black_inc,
                    time=time.total_seconds() if time is not None else None,
                    depth=depth,
                    nodes=nodes,
                )
                result = await loaded_engine.engine.play(pos.board(), limit, game=pos.game())
                if result.resigned:
                    return "resigned"
                if result.move is None:
                    raise PlayerError(
                        "The engine {self.name()} neither played a move nor resigned."
                    )
                return result.move

        return Player_()
