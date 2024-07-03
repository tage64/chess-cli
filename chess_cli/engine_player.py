from collections.abc import Awaitable
from typing import override

import chess
import chess.engine
import chess.pgn
from chess.engine import PlayResult

from .clock import RemainingTime
from .engine import Engine
from .match import Player


class EnginePlayer(Engine):
    """An extention to chess-cli which supports engines as players."""

    def mk_engine_player(self, engine: str) -> Player:
        loaded_engine = self.loaded_engines[engine]
        """Create a player from a specific engine."""

        class Player_(Player):
            """The actual player class."""

            @override
            def name(self) -> str:
                return loaded_engine.loaded_name

            @override
            def play(
                self, pos: chess.pgn.GameNode, time: RemainingTime | None
            ) -> Awaitable[PlayResult]:
                limit = chess.engine.Limit(time=1.0)  # TODO: Improve this
                return loaded_engine.engine.play(pos.board(), limit, game=pos.game())
        return Player_()
