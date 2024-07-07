import asyncio
import traceback
from abc import ABC, abstractmethod
from collections.abc import Hashable
from typing import Literal, override

import chess
import chess.pgn

from . import utils
from .base import Base, InitArgs
from .repl import CmdLoopContinue
from .utils import show_outcome

# When a player is asked to play a move, one of the following options may be returned.
type PlayResult = chess.Move | Literal["resigned", "timeout"]


class Player(ABC, Hashable):
    """Abstract class for a player-ish, may be a chess engine or online opponent,
    but also a clock which doesn't make any move but signals timeout."""

    @abstractmethod
    def name(self) -> str:
        """A short name for the player."""

    @abstractmethod
    async def play(self, pos: chess.pgn.GameNode) -> PlayResult:
        """Ask the player to make a move and return an Awaitable for it.

        The returned awaitable must be cancellable.

        :param pos: A game node for the position where the player should make a move.
            If `next_move()` was not called since the last call to `play()`, `pos` should be
            same as in the previous call to `play()`.
        """

    def next_move(self) -> None:
        """Called for every played full move.

        So for a chess clock this would add the increment.
        """
        pass

    def __hash__(self) -> int:
        """All players must be hashable."""
        return id(self)


class PlayerError(Exception):
    """Something went wrong with a player."""


class Match(Base):
    """An extention to chess-cli to play chess matches."""

    # All players together with their color.
    players: dict[Player, chess.Color]
    # The current game node in the match. This is None before the match is started.
    match_node: chess.pgn.GameNode | None = None
    match_paused: bool = False
    match_result: str | None = None
    # Asyncio tasks to handle the result of all thinking players.
    thinking_players: dict[Player, asyncio.Task[None]]

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)
        self.players = {}
        self.thinking_players = {}

    def match_started(self) -> bool:
        """Returns True iff a match is ongoing or paused."""
        return self.match_node is not None

    def match_ongoing(self) -> bool:
        """Returns True if a match is ongoing, that is, started and not paused or finished."""
        return self.match_started() and not self.match_paused and self.match_result is None

    def add_player(self, player: Player, color: chess.Color) -> None:
        """Add a player with the specified color.

        The start_match() function must be called for the player to start.

        :param player: The player.
        :param color: The color of the player.
        """
        assert not self.match_started(), "Can't add players when a match is already started."
        self.players[player] = color

    async def start_match(self) -> None:
        """Start a match with all added players at the current move."""
        assert not self.match_started(), "Can't start a match when a match is already started."
        assert not self.match_paused and self.match_result is None
        self.match_node = self.game_node
        await self._start_thinking_players()

    @override
    async def pre_prompt(self) -> None:
        await super().pre_prompt()
        if self.match_ongoing() and self.match_node == self.game_node.parent:
            # A new move has been played in the match.
            self.match_node = self.game_node
            await self._cancel_thinking_players()
            for player, color in self.players.items():
                if color != self.match_node.turn():
                    player.next_move()
            await self._start_thinking_players()

    async def pause_match(self) -> None:
        """Pause an ongoing match."""
        assert self.match_ongoing()
        await self._cancel_thinking_players()
        self.match_paused = True

    async def resume_match(self) -> None:
        """Resume a paused match.

        `self.match_paused` and `self.match_started()` must both be True.
        """
        assert self.match_started(), "No match is started."
        assert self.match_paused, "The match is not paused."
        self.match_paused = False
        await self._start_thinking_players()

    async def delete_match(self) -> None:
        """Delete all players in the current match. If the match is ongoing it will be cancelled."""
        await self._cancel_thinking_players()
        self.match_node = None
        self.match_paused = False
        self.match_finished = None
        self.players.clear()

    async def _start_thinking_players(self) -> None:
        assert self.match_ongoing()
        assert self.match_node is not None
        for player, color in self.players.items():
            if self.match_node.turn() == color:
                await self._wait_for_player(player)

    async def _cancel_thinking_players(self) -> None:
        for player, think_task in self.thinking_players.items():
            try:
                think_task.cancel()
                await think_task
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                traceback.print_exception(ex)
                print(f"Error: {player.name()} failed with exception: {ex}")
                del self.players[player]
                print(f"{player.name()} has been nocked out from the game.")
        self.thinking_players.clear()

    async def _wait_for_player(self, player: Player) -> None:
        """Set player to think on self.match_node."""
        match_node = self.match_node
        assert match_node is not None
        if (outcome := match_node.board().outcome()) is not None:
            await self._game_finished(player, outcome.result(), show_outcome(outcome))
            return

        result_awaitable = player.play(match_node)

        async def wait_for_player() -> None:
            assert match_node == self.match_node
            result: PlayResult = await result_awaitable
            del self.thinking_players[player]
            if result == "resigned":
                result_str = "0-1" if match_node.turn() == chess.WHITE else "1-0"
                comment = f"{player.name()} resigned: {result_str}"
                await self._game_finished(player, result_str, comment)
            elif result == "timeout":
                result_str = "0-1" if match_node.turn() == chess.WHITE else "1-0"
                color_str = "White" if match_node.turn() == chess.WHITE else "Black"
                comment = f"{color_str} lost on time: {result_str}"
                await self._game_finished(player, result_str, comment)
            else:
                assert isinstance(result, chess.Move)
                move = result
                if not match_node.board().is_legal(move):
                    del self.players[player]
                    raise PlayerError(
                        f"Gaah! {player.name()} played an illegal move: {move}."
                        f"He/She/it has been nocked out from the game."
                    )
                self.game_node = match_node.add_variation(move)
                if (outcome := self.game_node.board().outcome()) is not None:
                    await self._game_finished(player, outcome.result(), show_outcome(outcome))
            raise CmdLoopContinue

        wait_for_player_task = asyncio.create_task(wait_for_player())
        self.thinking_players[player] = wait_for_player_task
        self.add_task(wait_for_player_task)

    async def _game_finished(self, player: Player, result: str, comment: str) -> None:
        """Called when the game has finished."""
        assert self.match_ongoing()
        assert self.match_node is not None
        if comment not in self.match_node.comment:
            self.match_node.comment = utils.add_to_comment_text(self.match_node.comment, comment)
            print(comment)
        if self.match_node.is_mainline():
            # Set the result of the game.
            self.match_node.game().headers["Result"] = result
        await self._cancel_thinking_players()
        self.match_result = result
