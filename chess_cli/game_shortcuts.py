import chess
import chess.pgn

from .game_utils import GameUtils
from .repl import CmdLoopContinue, key_binding
from .utils import MoveNumber


class GameShortcuts(GameUtils):
    """Some key bindings to quickly navigate and edit the game."""

    @key_binding("c-up")
    def kb_up(self, _) -> None:
        """Go to the previous move in the game."""
        if isinstance(self.game_node, chess.pgn.ChildNode):
            self.game_node = self.game_node.parent
            raise CmdLoopContinue

    @key_binding("c-down")
    def kb_down(self, _) -> None:
        """Go to the next move (following the main line) in the game."""
        if (next := self.game_node.next()) is not None:
            self.game_node = next
            raise CmdLoopContinue

    @key_binding("c-left")
    def kb_left(self, _) -> None:
        """Go to the previous variation (if any)."""
        if isinstance(self.game_node, chess.pgn.ChildNode):
            parent: chess.pgn.GameNode = self.game_node.parent
            my_idx: int = parent.variations.index(self.game_node)
            if my_idx > 0:
                self.game_node = parent.variations[my_idx - 1]
                raise CmdLoopContinue

    @key_binding("c-right")
    def kb_right(self, _) -> None:
        """Go to the next variation (if any)."""
        if isinstance(self.game_node, chess.pgn.ChildNode):
            parent: chess.pgn.GameNode = self.game_node.parent
            my_idx: int = parent.variations.index(self.game_node)
            if my_idx + 1 < len(parent.variations):
                self.game_node = parent.variations[my_idx + 1]
                raise CmdLoopContinue

    @key_binding("delete")
    def kb_delete(self, _) -> None:
        """Delete the current move if this is not the root of the game."""
        self.delete_current_move()
        raise CmdLoopContinue()
