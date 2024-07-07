import re
from typing import override

import chess

from .base import Base, InitArgs

# Normal SAN regex but allowing the piece to be lower case as well.
SAN_REGEX = re.compile(
    r"^([nbkrqNBKRQ])?([a-h])?([1-8])?[\-x]?([a-h][1-8])(=?[nbrqkNBRQK])?[\+#]?\Z"
)
CASTLE_REGEX = re.compile(r"[Oo0]-?[Oo0](-?[Oo0])?")


class FastMoveInput(Base):
    """An extention to chess-cli to be able to type moves directly, without the play command."""

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)
        for cmd in self._cmds:
            assert not SAN_REGEX.fullmatch(cmd) and not CASTLE_REGEX.fullmatch(
                cmd
            ), f"The command {cmd} could be a SAN move."

    @override
    async def exec_cmd(self, prompt: str) -> None:
        prompt = prompt.strip()
        board = self.game_node.board()
        move: chess.Move | None = None
        try:
            if match := SAN_REGEX.fullmatch(prompt):
                # Solve ambiguities with the b-pawn and a bishop.
                piece, from_file = match[1], match[2]
                assert piece == "b" or from_file != "b"
                if piece == "b" and not from_file:
                    try:
                        move = board.parse_san(prompt)
                    except ValueError:
                        move = board.parse_san("B" + prompt[1:])
                elif piece:
                    assert len(piece) == 1
                    move = board.parse_san(piece.upper() + prompt[1:])
                else:
                    move = board.parse_san(prompt)
            elif match := CASTLE_REGEX.fullmatch(prompt):
                move = board.parse_san("O-O-O" if match[1] else "O-O")
        except (chess.IllegalMoveError, chess.AmbiguousMoveError) as e:
            self.perror(f"Invalid move {prompt}: {e}")
            return
        if move is not None:
            self.game_node = self.game_node.add_variation(move)
        else:
            await super().exec_cmd(prompt)
