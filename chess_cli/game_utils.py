from collections import deque
from collections.abc import Callable, Iterable

import chess
import chess.pgn
import more_itertools

from .base import Base
from .repl import CmdLoopContinue, CommandFailure
from .utils import MOVE_NUMBER_REGEX, MoveNumber, move_str


class GameUtils(Base):
    """More utility methods related to the game."""

    def find_move(
        self,
        move_str: str,
        search_sidelines: bool,
        recurse_sidelines: bool,
        search_forwards: bool = True,
        search_backwards: bool = True,
        break_search_forwards_at: Callable[[chess.pgn.ChildNode], bool] | None = None,
        break_search_backwards_at: Callable[[chess.pgn.ChildNode], bool] | None = None,
    ) -> chess.pgn.ChildNode | None:
        """Search for a move by a string of its move number and SAN.

        Like 'e4' '8.Nxe5' or 8...'.
        """
        move_number_match = MOVE_NUMBER_REGEX.match(move_str)
        if move_number_match is not None:
            move_number: MoveNumber | None = MoveNumber.from_regex_match(move_number_match)
            if len(move_str) > move_number_match.end():
                move: str | None = move_str[move_number_match.end() :]
            else:
                move = None
        else:
            move_number = None
            move = move_str

        def check(node: chess.pgn.ChildNode) -> bool:
            if node is self.game_node:
                return False
            if move is not None:
                try:
                    if node.move != node.parent.board().push_san(move):
                        return False
                except ValueError:
                    return False
            return not (move_number is not None and move_number != MoveNumber.last(node))

        if isinstance(self.game_node, chess.pgn.ChildNode):
            current_node: chess.pgn.ChildNode = self.game_node
        else:
            next = self.game_node.next()
            if next is not None and search_forwards:
                current_node = next
            else:
                return None

        search_queue: deque[chess.pgn.ChildNode] = deque()
        search_queue.append(current_node)
        if search_sidelines:
            sidelines = current_node.parent.variations
            search_queue.extend(x for x in sidelines if x is not current_node)

        if search_forwards and (
            move_number is None or move_number >= MoveNumber.last(current_node)
        ):
            while search_queue:
                node: chess.pgn.ChildNode = search_queue.popleft()
                if check(node):
                    return node
                if break_search_forwards_at is not None and break_search_forwards_at(node):
                    break
                if move_number is not None and move_number < MoveNumber.last(node):
                    break
                if node.is_main_variation() or recurse_sidelines or node is current_node:
                    if search_sidelines:
                        search_queue.extend(node.variations)
                    else:
                        next = node.next()
                        if next is not None:
                            search_queue.append(next)

        if search_backwards and (
            move_number is None or move_number < MoveNumber.last(current_node)
        ):
            node = current_node
            while isinstance(node.parent, chess.pgn.ChildNode):
                node = node.parent
                if check(node):
                    return node
                if break_search_backwards_at is not None and break_search_backwards_at(node):
                    break
                if move_number is not None and move_number > MoveNumber.last(node):
                    break
        return None

    def show_variations(self, node: chess.pgn.GameNode) -> None:
        next = node.next()
        if next is not None:
            show_items = [move_str(next, include_sideline_arrows=False)]
            for variation in node.variations[1:]:
                show_items.append(
                    move_str(variation, include_move_number=False, include_sideline_arrows=False)
                )
            self.poutput(", ".join(show_items))

    def display_game_segment(
        self,
        start_node: chess.pgn.ChildNode,
        end_node: chess.pgn.ChildNode,
        show_sidelines: bool,
        recurse_sidelines: bool,
        show_comments: bool,
    ) -> Iterable[str]:
        """Given a start and end node in this game, which must be connected, yield lines
        printing all moves between them (including endpoints).

        There are also options to toggle visibility of comments, show a short list of
        the sidelines at each move with sidelines, or even recurse and show the endire
        sidelines.
        """
        # Create a list of all moves that should be displayed following the
        # main line (I.E not recursing into sidelines).
        # The list is created in reversed order. This is important because we
        # want to display the moves from the start to the end, but we don't
        # know the path from the start to the end. Imagine for instance that we
        # are not following the main line, then we don't know what variation to
        # choose at a certain move number.
        moves_on_mainline: deque[chess.pgn.ChildNode] = deque()
        node: chess.pgn.ChildNode = end_node
        while True:
            moves_on_mainline.appendleft(node)
            if node is start_node:
                break
            if not isinstance(node.parent, chess.pgn.ChildNode):
                break
            node = node.parent
        return self.display_moves(
            moves_on_mainline,
            show_sidelines=show_sidelines,
            recurse_sidelines=recurse_sidelines,
            show_comments=show_comments,
        )

    def display_moves(
        self,
        moves: Iterable[chess.pgn.ChildNode],
        show_sidelines: bool,
        recurse_sidelines: bool,
        show_comments: bool,
        include_sidelines_at_first_move: bool = True,
    ) -> Iterable[str]:
        """Same as display_game_segment(), but this function takes an iterable of moves
        instead of a starting and ending game node."""
        moves_per_line: int = 6
        current_line: str = ""
        moves_at_current_line: int = 0

        # Just a very small method that should be called when we've yielded a line.
        def carriage_return():
            nonlocal current_line
            nonlocal moves_at_current_line
            current_line = ""
            moves_at_current_line = 0

        for i, node in enumerate(moves):
            if moves_at_current_line >= moves_per_line:
                yield current_line
                carriage_return()

            include_move_number = True if moves_at_current_line == 0 else node.turn() == chess.BLACK

            # Add a space if current_line is not empty.
            if current_line:
                current_line += " "
            current_line += move_str(
                node, include_move_number=include_move_number, include_sideline_arrows=True
            )
            if node.turn() == chess.BLACK:
                moves_at_current_line += 1

            if node.comment and show_comments:
                yield current_line
                carriage_return()
                yield f"   {node.comment}"
                # No carriage_return() is needed here.

            # If this move has any sidelines.
            if len(node.parent.variations) > 1 and (include_sidelines_at_first_move or i != 0):
                if recurse_sidelines:
                    # Flush the current line if needed.
                    if current_line:
                        yield current_line
                        carriage_return()

                    # Loop through the sidelines (siblings) to this node.
                    for sideline in node.parent.variations:
                        if sideline is node:
                            continue

                        # Display any possible starting_comment.
                        if show_comments and sideline.starting_comment:
                            yield f"     {sideline.starting_comment}"

                        # Call this method recursively with the mainline
                        # following the sideline as moves iterator.
                        for line in self.display_moves(
                            more_itertools.prepend(sideline, sideline.mainline()),
                            show_sidelines=show_sidelines,
                            recurse_sidelines=recurse_sidelines,
                            show_comments=show_comments,
                            include_sidelines_at_first_move=False,
                        ):
                            # Indent the sideline a bit.
                            yield f"  {line}"
                elif show_sidelines:
                    # Only show a short list of all sideline moves.

                    # Flush the current line if needed.
                    if current_line:
                        yield current_line
                        carriage_return()
                    current_line = (
                        "  ("
                        + "; ".join(
                            map(
                                lambda sideline: (
                                    move_str(
                                        sideline,
                                        include_move_number=False,
                                        include_sideline_arrows=False,
                                    )
                                ),
                                node.parent.variations,
                            )
                        )
                        + ")"
                    )
                    yield current_line
                    carriage_return()

        # A final flush!
        if current_line:
            yield current_line

    def delete_current_move(self) -> None:
        """Delete the current move if this is not the root of the game."""
        if isinstance(self.game_node, chess.pgn.ChildNode):
            parent = self.game_node.parent
            for i, node in enumerate(parent.variations):
                if node is self.game_node:
                    if i + 1 < len(parent.variations):
                        self.game_node = parent.variations[i + 1]
                    elif i > 0:
                        self.game_node = parent.variations[i - 1]
                    else:
                        self.game_node = parent
                    parent.variations = parent.variations[:i] + parent.variations[i + 1 :]

    async def set_position(self, board: chess.Board, may_remove_ep: bool = False) -> None:
        """Delete the current game and set the starting position.

        :param may_remove_ep: Remove en-passant square if that's what's needed
                              to make the position valid.
        """
        if board == self.game_node.board():
            return
        board.fullmove_number = 1
        status = board.status()
        if status != chess.STATUS_VALID:
            if (
                may_remove_ep
                and status & chess.STATUS_INVALID_EP_SQUARE
                and board.ep_square is not None
            ):
                print(f"Clearing en-passant square at {chess.square_name(board.ep_square)}")
                board.ep_square = None
                await self.set_position(board)
                return
            print("The position will become invalid:")
            if status & chess.STATUS_BAD_CASTLING_RIGHTS:
                # Try if we can remove some castling rights:
                original_castling_rights = board.castling_rights
                # Consider all subsets of the current castling rights:
                castling_subsets: list[chess.SquareSet] = [
                    chess.SquareSet(bb)
                    for bb in chess.SquareSet(board.castling_rights).carry_rippler()
                    if bb != original_castling_rights
                ]
                # Sort so that we remove as few castling rights as possible.
                castling_subsets.sort(key=len, reverse=True)
                for castling_rights in castling_subsets:
                    board.castling_rights = castling_rights.mask
                    if board.is_valid():
                        print("Position will become valid if ", end="")
                        diff = chess.SquareSet(original_castling_rights) - castling_rights
                        if not castling_rights:
                            print("we clear castling rights.")
                        elif len(diff) == 1:
                            print(
                                f"we remove castling rights for the rook at "
                                f"{chess.square_name(next(iter(diff)))}"
                            )
                        else:
                            print(
                                f"we remove castling rights for the rooks at "
                                f"{" and ".join(map(chess.square_name, diff))}"
                            )
                        ans: bool = await self.yes_no_dialog("Do you want to do that?")
                        if ans:
                            print("The castling rights has been updated.")
                            await self.set_position(board)
                        else:
                            print("Ok, nothing was changed.")
                        return
            status_descriptions: dict[int, str] = {
                chess.STATUS_NO_WHITE_KING: "No white king",
                chess.STATUS_NO_BLACK_KING: "No black king",
                chess.STATUS_TOO_MANY_KINGS: "Too many kings",
                chess.STATUS_TOO_MANY_WHITE_PAWNS: "Too many white pawns",
                chess.STATUS_TOO_MANY_BLACK_PAWNS: "Too many black pawns",
                chess.STATUS_PAWNS_ON_BACKRANK: "Pawns on backrank",
                chess.STATUS_TOO_MANY_WHITE_PIECES: "Too many white pieces",
                chess.STATUS_TOO_MANY_BLACK_PIECES: "Too many black pieces",
                chess.STATUS_BAD_CASTLING_RIGHTS: "Bad castling rights",
                chess.STATUS_INVALID_EP_SQUARE: "Invalid en-passant square",
                chess.STATUS_OPPOSITE_CHECK: "Opposite check",
                chess.STATUS_EMPTY: "The board cannot be empty",
                chess.STATUS_RACE_CHECK: "Racing kings: A king is in check which is not allowed",
                chess.STATUS_RACE_OVER: "Racing kings: Race is over",
                chess.STATUS_RACE_MATERIAL: "Racing kings: Invalid material",
                chess.STATUS_TOO_MANY_CHECKERS: "Too many checkers",
                chess.STATUS_IMPOSSIBLE_CHECK: "Impossible check",
            }
            for status_code, description in status_descriptions.items():
                if status & status_code:
                    print(f"- {description}")
            raise CommandFailure("The position is not changed.")
        if self.game_node.parent is not None or self.game_node.variations:
            ans: bool = await self.yes_no_dialog(
                "Changing the position will delete all moves in the game  "
                "and set the new position as start. Do you want to continue?"
            )
            if not ans:
                raise CmdLoopContinue()
        self.game_node = self.game_node.game()
        self.game_node.variations = []
        self.game_node.setup(board)
