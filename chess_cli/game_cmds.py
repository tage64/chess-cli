from .base import *

from collections import deque
from typing import *

import chess
import chess.pgn
import cmd2
import more_itertools


class GameCmds(Base):
    "Commands related to altering or reading the actual chess game."
    play_argparser = cmd2.Cmd2ArgumentParser()
    play_argparser.add_argument(
        "moves", nargs="+", help="A list of moves in standard algibraic notation."
    )
    play_argparser.add_argument(
        "-c",
        "--comment",
        help="Add a comment for the move (or the last move if more than one is supplied.",
    )
    play_argparser.add_argument(
        "-m",
        "--main-line",
        action="store_true",
        help=(
            "If a variation already exists from the current move, add this new variation as the"
            " main line rather than a side line."
        ),
    )
    play_argparser.add_argument(
        "-s",
        "--sideline",
        action="store_true",
        help="Add this new list of moves as a sideline to the current move.",
    )

    @cmd2.with_argparser(play_argparser)  # type: ignore
    def do_play(self, args) -> None:
        """Play a sequence of moves from the current position."""
        if args.sideline:
            if not isinstance(self.game_node, chess.pgn.ChildNode):
                self.poutput(f"Cannot add a sideline to the root of the game.")
                return
            self.game_node = self.game_node.parent

        for move_text in args.moves:
            try:
                move: chess.Move = self.game_node.board().parse_san(move_text)
            except ValueError:
                self.poutput(f"Error: Illegal move: {move_text}")
                break
            if args.main_line:
                self.game_node = self.game_node.add_main_variation(move)
            else:
                self.game_node = self.game_node.add_variation(move)
        if args.comment is not None:
            self.game_node.comment = args.comment

    game_argparser = cmd2.Cmd2ArgumentParser()
    game_argparser.add_argument(
        "-a", "--all", action="store_true", help="Print the entire game from the start."
    )

    @cmd2.with_argparser(game_argparser)  # type: ignore
    def do_game(self, args) -> None:
        "Print the rest of the game with sidelines and comments in a nice and readable format."
        if args.all:
            self.onecmd("moves -s -r -c")
        else:
            self.onecmd("moves -s -r -c --fc")

    def find_move(
        self,
        move_str: str,
        search_sidelines: bool,
        recurse_sidelines: bool,
        search_forwards: bool = True,
        search_backwards: bool = True,
        break_search_forwards_at: Optional[Callable[[chess.pgn.ChildNode], bool]] = None,
        break_search_backwards_at: Optional[Callable[[chess.pgn.ChildNode], bool]] = None,
    ) -> Optional[chess.pgn.ChildNode]:
        """Search for a move by a string of its move number and SAN.
        Like 'e4' '8.Nxe5' or 8...'.
        """
        move_number_match = MOVE_NUMBER_REGEX.match(move_str)
        if move_number_match is not None:
            move_number: Optional[MoveNumber] = MoveNumber.from_regex_match(move_number_match)
            if len(move_str) > move_number_match.end():
                move: Optional[str] = move_str[move_number_match.end() :]
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
                    if not node.move == node.parent.board().push_san(move):
                        return False
                except ValueError:
                    return False
            if move_number is not None and not move_number == MoveNumber.last(node):
                return False
            return True

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
            search_queue.extend((x for x in sidelines if not x is current_node))

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

    moves_argparser = cmd2.Cmd2ArgumentParser()
    moves_argparser.add_argument(
        "-c",
        "--comments",
        action="store_true",
        help=(
            'Show all comments. Otherwise just a dash ("-") will be shown at each move with a'
            " comment."
        ),
    )
    _moves_from_group = moves_argparser.add_mutually_exclusive_group()
    _moves_from_group.add_argument(
        "--fc",
        "--from-current",
        dest="from_current",
        action="store_true",
        help="Print moves from the current move.",
    )
    _moves_from_group.add_argument(
        "-f", "--from", dest="_from", help="Print moves from the given move number."
    )
    _moves_to_group = moves_argparser.add_mutually_exclusive_group()
    _moves_to_group.add_argument(
        "--tc",
        "--to-current",
        dest="to_current",
        action="store_true",
        help="Print only moves upto and including the current move.",
    )
    _moves_to_group.add_argument("-t", "--to", help="Print moves to the given move number.")
    moves_argparser.add_argument(
        "-s",
        "--sidelines",
        action="store_true",
        help="Print a short list of the sidelines at each move with variations.",
    )
    moves_argparser.add_argument(
        "-r", "--recurse", action="store_true", help="Recurse into sidelines."
    )

    @cmd2.with_argparser(moves_argparser)  # type: ignore
    def do_moves(self, args) -> None:
        if args._from is not None:
            # If the user has specified a given move as start.
            node = self.find_move(
                args._from,
                search_sidelines=args.sidelines,
                recurse_sidelines=args.recurse,
            )
            if node is None:
                self.poutput(f"Error: Couldn't find the move {args._from}")
                return
            start_node: chess.pgn.ChildNode = node
        elif args.from_current:
            # Start printing at the current move.
            if isinstance(self.game_node, chess.pgn.ChildNode):
                start_node = self.game_node
            else:
                # If `self.game_node` is the root node.
                next = self.game_node.next()
                if next is None:
                    return
                start_node = next
        else:
            # Print moves from the start of the game.
            first_move = self.game_node.game().next()
            if first_move is None:
                return
            start_node = first_move

        if args.to is not None:
            node = self.find_move(
                args.to,
                search_sidelines=args.sidelines,
                recurse_sidelines=args.recurse,
                break_search_backwards_at=lambda x: x is start_node,
            )
            if node is None:
                self.poutput(f"Error: Couldn't find the move {args.to}")
                return
            end_node = node
        elif args.to_current:
            if isinstance(self.game_node, chess.pgn.ChildNode):
                end_node = self.game_node
            else:
                return
        else:
            # Print moves until the end of the game.
            end = self.game_node.end()
            if not isinstance(end, chess.pgn.ChildNode):
                return
            end_node = end

        lines: Iterable[str] = self.display_game_segment(
            start_node,
            end_node,
            show_sidelines=args.sidelines,
            recurse_sidelines=args.recurse,
            show_comments=args.comments,
        )

        for line in lines:
            self.poutput(f"  {line}")

    def display_game_segment(
        self,
        start_node: chess.pgn.ChildNode,
        end_node: chess.pgn.ChildNode,
        show_sidelines: bool,
        recurse_sidelines: bool,
        show_comments: bool,
    ) -> Iterable[str]:
        """Given a start and end node in this game, which must be connected,
        yield lines printing all moves between them (including endpoints).
        There are also options to toggle visibility of comments, show a short
        list of the sidelines at each move with sidelines, or even recurse and
        show the endire sidelines.
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
        """Same as display_game_segment(), but this function takes an iterable
        of moves instead of a starting and ending game node.
        """

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
                node,
                include_move_number=include_move_number,
                include_sideline_arrows=True,
            )
            if node.turn() == chess.BLACK:
                moves_at_current_line += 1

            if node.comment and show_comments:
                yield current_line
                carriage_return()
                yield f"   {node.comment}"
                # No carriage_return() is needed here.

            # If this move has any sidelines.
            if len(node.parent.variations) > 1 and (include_sidelines_at_first_move or not i == 0):
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

    goto_argparser = cmd2.Cmd2ArgumentParser()
    goto_argparser.add_argument(
        "move",
        help=(
            "A move, move number or both. E.G. 'e4', '8...' or '9.dxe5+'. Or the"
            "string 'start'/'s' or 'end'/'e' for jumping to the start or end of the game."
        ),
    )
    goto_sidelines_group = goto_argparser.add_mutually_exclusive_group()
    goto_sidelines_group.add_argument(
        "-r",
        "--recurse",
        action="store_true",
        help="Search sidelines recursively for the move.",
    )
    goto_sidelines_group.add_argument(
        "-m",
        "--mainline",
        action="store_true",
        help="Only search along the mainline and ignore all sidelines.",
    )
    _goto_direction_group = goto_argparser.add_mutually_exclusive_group()
    _goto_direction_group.add_argument(
        "-b",
        "--backwards-only",
        action="store_true",
        help="Only search the game backwards.",
    )
    _goto_direction_group.add_argument(
        "-f",
        "--forwards-only",
        action="store_true",
        help="Only search the game forwards.",
    )

    @cmd2.with_argparser(goto_argparser)  # type: ignore
    def do_goto(self, args) -> None:
        """Goto a move specified by a move number or a move in standard algibraic notation.
        If a move number is specified, it will follow the main line to that move if it does exist. If a move like "e4" or "Nxd5+" is specified as well, it will go to the specific move number and search between variations at that level for the specified move. If only a move but not a move number and no other constraints are given, it'll first search sidelines at the current move, then follow the mainline and check if any move or sideline matches, but not recurse into sidelines. Lastly, it'll search backwards in the game.
        """
        match args.move:
            case "s" | "start":
                self.game_node = self.game_node.game()
            case "e" | "end":
                self.game_node = self.game_node.end()
            case move:
                node = self.find_move(
                    move,
                    search_sidelines=not args.mainline,
                    recurse_sidelines=args.recurse,
                    search_forwards=not args.backwards_only,
                    search_backwards=not args.forwards_only,
                )
                if node is None:
                    self.poutput(f"Error: Couldn't find the move {move}")
                    return
                self.game_node = node

    delete_argparser = cmd2.Cmd2ArgumentParser()

    @cmd2.with_argparser(delete_argparser)  # type: ignore
    def do_delete(self, _args) -> None:
        "Delete the current move."
        if isinstance(self.game_node, chess.pgn.ChildNode):
            parent = self.game_node.parent
            new_node = parent
            for i, node in enumerate(parent.variations):
                if node is self.game_node:
                    if i + 1 < len(parent.variations):
                        self.game_node = parent.variations[i + 1]
                    elif i > 0:
                        self.game_node = parent.variations[i - 1]
                    else:
                        self.game_node = parent
                    parent.variations = parent.variations[:i] + parent.variations[i + 1 :]
