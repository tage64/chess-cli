import asyncio
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Awaitable, Hashable
from contextlib import suppress
from typing import override

import chess
import chess.pgn
from chess.engine import PlayResult

from . import utils
from .base import InitArgs
from .clock import Clock, RemainingTime
from .repl import CmdLoopContinue


class Player(ABC, Hashable):
    """Abstract class for a player like a chess engine or an online opponent."""

    @abstractmethod
    def name(self) -> str:
        """A short name for the player."""

    @abstractmethod
    def play(self, pos: chess.pgn.GameNode, time: RemainingTime | None) -> Awaitable[PlayResult]:
        """Ask the player to make a move and return an Awaitable for it.

        :param pos: A game node for the position where the player should make a move.
            It is **important** that `pos.move` is the last move played by the opponent,
            and that `pos.parent` is the position after this player's last move.
            This doesn't apply on the first move.
        :param time: The remaining time for the player if a clock is set.
        """

    def __hash__(self) -> int:
        """All players must be hashable."""
        return id(self)


class PlayerError(Exception):
    """Something went wrong with a player."""


class Play(Clock):
    """An extention to chess-cli to play against an opponent."""

    # All players currently playing, together with the game node where they are
    # either waiting or thinking.
    players: dict[Player, chess.pgn.GameNode]
    # A subset of `players` consisting of all players that are waiting for a move.
    # The keys are the game node where there opponent should make a move.
    # (There might be multiple players waiting on the same move, hence a multimap.)
    waiting_players: defaultdict[chess.pgn.GameNode, set[Player]]
    # All thinking players together with a task waiting for their move.
    # waiting_players + thinking_players = players
    # (There might be multiple players thinking on the same move, hence a multimap.)
    thinking_players: defaultdict[chess.pgn.GameNode, dict[Player, asyncio.Task[None]]]

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)
        self.players = {}
        self.waiting_players = defaultdict(set)
        self.thinking_players = defaultdict(dict)

    def add_player(self, player: Player, color: chess.Color) -> None:
        """Add a player to play a game from the current position.

        :param player: The player.
        :param color: The color of the player.
            If it is the same as `self.game_node.turn()`, the player will start think immediately,
            otherwise it will wait for a move.
        """
        assert player not in self.players, "The player is already playing."
        self.players[player] = self.game_node
        if self.game_node.turn() == color:
            self._wait_for_player(player)
        else:
            self.waiting_players[self.game_node].add(player)

    @override
    async def pre_prompt(self) -> None:
        await super().pre_prompt()
        if (parent := self.game_node.parent) is not None:
            while self.waiting_players[parent]:
                player = self.waiting_players[parent].pop()
                # The opponent has made a move so we can call the player to think.
                self.players[player] = self.game_node
                self._wait_for_player(player)

    def _wait_for_player(self, player: Player) -> None:
        """Set player to think on this move."""
        game_node = self.players[player]
        result_awaitable = player.play(game_node, self.remaining_time())

        async def wait_for_player() -> None:
            result: PlayResult = await result_awaitable
            del self.thinking_players[game_node][player]
            if result.resigned:
                result_str = "0-1" if game_node.turn() == chess.WHITE else "1-0"
                comment = f"{player.name()} resigned: {result_str}"
                self._game_finished(player, result_str, comment)
                raise CmdLoopContinue
            if result.move is None:
                del self.players[player]
                raise PlayerError(
                    f"The player {player.name} neither provided a move nor resigned."
                    f"He/She/it has been nocked out from the game."
                )
            if not game_node.board().is_legal(result.move):
                del self.players[player]
                raise PlayerError(
                    f"Gaah! {player.name} played an illegal move: {result.move}."
                    f"He/She/it has been nocked out from the game."
                )
            self.game_node = game_node.add_variation(result.move)
            if (outcome := self.game_node.board().outcome()) is not None:
                self._game_finished(player, outcome.result(), str(outcome))
                raise CmdLoopContinue
            self.waiting_players[self.game_node].add(player)
            raise CmdLoopContinue

        wait_for_player_task = asyncio.create_task(wait_for_player())
        self.thinking_players[game_node][player] = wait_for_player_task
        self.add_task(wait_for_player_task)

    def _game_finished(self, player: Player, result: str, comment: str) -> None:
        """Called when the game has finished."""
        game_node = self.players[player]
        print(comment)
        self.game_node.comment = utils.add_to_comment_text(game_node.comment, comment)
        if game_node.is_mainline():
            # Set the result of the game.
            self.game_node.game().headers["Result"] = result
        del self.players[player]
        with suppress(KeyError):
            del self.thinking_players[game_node][player]
        with suppress(KeyError):
            self.waiting_players[game_node].remove(player)
