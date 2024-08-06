import argparse
import io
import textwrap
from argparse import ArgumentParser
from collections import defaultdict
from collections.abc import Iterable

import chess
import chess.pgn
import pyperclip

from .base import CommandFailure
from .game_utils import GameUtils
from .repl import argparse_command, command
from .utils import MoveNumber, castling_descr, piece_name


class GameCmds(GameUtils):
    """Basic commands to view and alter the game."""

    play_argparser = ArgumentParser()
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

    @argparse_command(play_argparser, alias="p")
    def do_play(self, args) -> None:
        """Play a sequence of moves from the current position."""
        if args.sideline:
            if not isinstance(self.game_node, chess.pgn.ChildNode):
                self.poutput("Cannot add a sideline to the root of the game.")
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

    game_argparser = ArgumentParser()
    game_argparser.add_argument(
        "-a", "--all", action="store_true", help="Print the entire game from the start."
    )

    @argparse_command(game_argparser, alias="gm")
    async def do_game(self, args) -> None:
        """Print the rest of the game with sidelines and comments in a nice and readable
        format."""
        if args.all:
            await self.exec_cmd("moves -s -r -c")
        else:
            await self.exec_cmd("moves -s -r -c --fc")

    moves_argparser = ArgumentParser()
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

    @argparse_command(moves_argparser, alias="m")
    def do_moves(self, args) -> None:
        if args._from is not None:
            # If the user has specified a given move as start.
            node = self.find_move(
                args._from, search_sidelines=args.sidelines, recurse_sidelines=args.recurse
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

    goto_argparser = ArgumentParser()
    goto_argparser.add_argument(
        "move",
        help=(
            "A move, move number or both. E.G. 'e4', '8...' or '9.dxe5+'. Or the string 'start'/'s'"
            " or 'end'/'e' for jumping to the start or end of the game."
        ),
    )
    goto_sidelines_group = goto_argparser.add_mutually_exclusive_group()
    goto_sidelines_group.add_argument(
        "-r", "--recurse", action="store_true", help="Search sidelines recursively for the move."
    )
    goto_sidelines_group.add_argument(
        "-m",
        "--mainline",
        action="store_true",
        help="Only search along the mainline and ignore all sidelines.",
    )
    _goto_direction_group = goto_argparser.add_mutually_exclusive_group()
    _goto_direction_group.add_argument(
        "-b", "--backwards-only", action="store_true", help="Only search the game backwards."
    )
    _goto_direction_group.add_argument(
        "-f", "--forwards-only", action="store_true", help="Only search the game forwards."
    )

    @argparse_command(goto_argparser, alias="g")
    def do_goto(self, args) -> None:
        """Goto a move specified by a move number or a move in standard algibraic
        notation.

        If a move number is specified, it will follow the main line to that move if it
        does exist. If a move like "e4" or "Nxd5+" is specified as well, it will go to
        the specific move number and search between variations at that level for the
        specified move. If only a move but not a move number and no other constraints
        are given, it'll first search sidelines at the current move, then follow the
        mainline and check if any move or sideline matches, but not recurse into
        sidelines. Lastly, it'll search backwards in the game.
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

    @command(alias="del")
    def do_delete(self, _) -> None:
        """Delete the current move if this is not the root of the game."""
        self.delete_current_move()

    games_argparser = ArgumentParser()
    games_subcmds = games_argparser.add_subparsers(dest="subcmd")
    games_ls_argparser = games_subcmds.add_parser("ls", help="List all games.")
    games_rm_argparser = games_subcmds.add_parser(
        "rm", aliases=["remove"], help="Remove the current game."
    )
    games_rm_subcmds = games_rm_argparser.add_subparsers(dest="subcmd")
    games_rm_subcmds.add_parser("this", help="Remove the currently selected game.")
    games_rm_subcmds.add_parser("others", help="Remove all but the currently selected game.")
    games_rm_subcmds.add_parser("all", help="Remove all games. Including the current game.")
    games_select_argparser = games_subcmds.add_parser(
        "select", aliases=["s", "sel"], help="Select another game in the file."
    )
    games_select_argparser.add_argument(
        "index",
        type=int,
        help=(
            "Index of the game to select. Use the `game ls` command to get the index of a"
            " particular game."
        ),
    )
    games_add_argparser = games_subcmds.add_parser("add", help="Add a new game to the file.")
    games_add_argparser.add_argument(
        "index",
        type=int,
        help="The index where the game should be inserted. Defaults to the end of the game list.",
    )

    @argparse_command(games_argparser, alias="gs")
    def do_games(self, args) -> None:
        """List, select, delete or create new games."""
        match args.subcmd:
            case "ls":
                for i, game in enumerate(self.games):
                    show_str: str = f"{i + 1}. "
                    if i == self.game_idx:
                        show_str += "[*] "
                    show_str += f"{game.headers["White"]} - {game.headers["Black"]}"
                    if isinstance(game.game_node, chess.pgn.ChildNode):
                        show_str += f" @ {MoveNumber.last(game.game_node)} {game.game_node.san()}"
                    self.poutput(show_str)
            case "rm":
                self.rm_game(self.game_idx)
            case "s" | "sel" | "select":
                self.select_game(args.index)
            case "add":
                self.add_new_game(args.index)
            case _:
                raise AssertionError("Unknown subcommand.")

    save_argparser = ArgumentParser()
    save_arggroup = save_argparser.add_mutually_exclusive_group()
    save_arggroup.add_argument(
        "-f", "--file", nargs="?", help="File to save to. Defaults to the loaded file."
    )
    save_arggroup.add_argument(
        "-c", "--clipboard", action="store_true", help="Copy the games to the clipboard."
    )
    save_argparser.add_argument(
        "-t",
        "--this",
        action="store_true",
        help="Save only the current game and discard any changes in the other games.",
    )

    @argparse_command(save_argparser, alias="sv")
    def do_save(self, args) -> None:
        """Save the games to a PGN file."""
        games: Iterable[int] = [self.game_idx] if args.this else range(len(self.games))
        if args.clipboard:
            pgn_io = io.StringIO()
            self.write_games(pgn_io, games)
            pyperclip.copy(pgn_io.getvalue())
        elif args.file is None:
            if self.pgn_file_name is None:
                self.poutput("Error: No file selected.")
                return
            self.save_games(args.file, games)
        else:
            self.save_games(args.file, games)

    load_argparser = ArgumentParser()
    load_arggroup = load_argparser.add_mutually_exclusive_group(required=True)
    load_arggroup.add_argument("-f", "--file", help="Path to a PGN file.")
    load_arggroup.add_argument(
        "-c",
        "--clipboard",
        action="store_true",
        help="Load a PGN or FEN from the clipboard. The file argument will be ignored.",
    )

    @argparse_command(load_argparser, alias="ld")
    async def do_load(self, args) -> None:
        """Load games from a PGN file.

        Note that the current game will be lost.
        """
        if args.file:
            self.load_games_from_file(args.file)
        if args.clipboard:
            clip: str = pyperclip.paste()
            if not clip:
                raise CommandFailure("The clipboard is empty.")
            try:
                # Try to parse as FEN:
                board = chess.Board(clip)
            except ValueError:
                print("Reading clipboard as PGN.")
                self.load_games_from_pgn_str(clip)
            else:
                print("Successfully read clipboard as FEN, which is set to the starting position.")
                self.add_new_game()
                await self.set_position(board)

    promote_argparser = ArgumentParser()
    promote_group = promote_argparser.add_mutually_exclusive_group()
    promote_group.add_argument(
        "-m", "--main", action="store_true", help="Promote this move to be main variation."
    )
    promote_group.add_argument(
        "-n", "--steps", type=int, help="Promote this variation n number of steps."
    )

    @argparse_command(promote_argparser, alias="pr")
    def do_promote(self, args) -> None:
        """If current move is a side line, promote it so that it'll be closer to main
        variation."""
        if not isinstance(self.game_node, chess.pgn.ChildNode):
            return
        if args.main:
            self.game_node.parent.variations.remove(self.game_node)
            self.game_node.parent.variations.insert(0, self.game_node)
        else:
            n = args.steps or 1
            for _ in range(n):
                self.game_node.parent.promote(self.game_node)

    demote_argparser = ArgumentParser()
    demote_group = demote_argparser.add_mutually_exclusive_group()
    demote_group.add_argument(
        "-l", "--last", action="store_true", help="Demote this move to be the last variation."
    )
    demote_group.add_argument(
        "-n", "--steps", type=int, help="Demote this variation n number of steps."
    )

    @argparse_command(demote_argparser, alias="de")
    def do_demote(self, args) -> None:
        """If current move is the main variation or if it isn't the last variation,
        demote it so it'll be far from the main variation."""
        if not isinstance(self.game_node, chess.pgn.ChildNode):
            return
        if args.last:
            self.game_node.parent.variations.remove(self.game_node)
            self.game_node.parent.variations.append(self.game_node)
        else:
            n = args.steps or 1
            for _ in range(n):
                self.game_node.parent.demote(self.game_node)

    @command(alias="v")
    def do_variations(self, _) -> None:
        """Print all variations following this move."""
        self.show_variations(self.game_node)

    @command(alias="sl")
    def do_sidelines(self, _) -> None:
        """Show all sidelines to this move."""
        if self.game_node.parent is not None:
            self.show_variations(self.game_node.parent)

    @command(alias="st")
    async def do_setup(self, args: str) -> None:
        """Setup a starting position for the game.

        The position can either be the string "start" ("or "s"), a FEN,
        or a list of piece-square identifiers like Kg1 or bb8.
        - `setup start` sets up the starting position.
        - `setup 4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w KQkq - 0`
           sets up a position by a FEN string.
        - `setup Kg1 Pa2,b2,c2 ke8 qd8`
           sets a position by piece square identifiers, (see the put command for more details)
        To set the turn, castling rights, or en passant,
        see the "turn", "castling", or "en-passant" commands respectively.
        """
        board: chess.Board
        board: chess.Board
        if args in ["start", "s"]:
            board = chess.Board()
        else:
            try:
                board = chess.Board(args)
            except ValueError:
                board = chess.Board.empty()
                await self._put_pieces(board, args.split())
        await self.set_position(board)

    clear_argparser = ArgumentParser()
    clear_argparser.add_argument(
        "squares", type=chess.parse_square, nargs="+", help="The squares to clear."
    )

    @argparse_command(clear_argparser)
    async def do_clear(self, args) -> None:
        """Clear squares on the chess board."""
        board: chess.Board = self.game_node.board()
        removed: dict[chess.Piece, list[chess.Square]] = defaultdict(list)
        for square in args.squares:
            square_name: str = chess.square_name(square)
            piece: chess.Piece | None = board.remove_piece_at(square)
            if piece is None:
                print(f"There is no piece at {square_name}")
            else:
                removed[piece].append(square)
        if removed:
            print("Removing:")
            for piece, squares in removed.items():
                piece_name_ = piece_name(piece, capital=True)
                print(
                    f"- {self.p.plural_noun(piece_name_, len(squares))} "  # type: ignore
                    f"at {self.p.join([chess.square_name(sq) for sq in squares])}"  # type: ignore
                )

        await self.set_position(board, may_remove_ep=True)

    put_argparser = ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.fill(
            "The piece-squares identifier begins with a letter for the piece type "
            "like K for white king or q for black queen. A capital letter means a white "
            "piece and vice versa. "
            "Then follows a comma seperated list of squares like e4 or a7,b7,c6. "
            "So to put a white king on e4, a black king on e6 and white pawns on e2 and e3, "
            "you can type:"
        )
        + "\n    put Ke4 ke6 Pe2,e3",
    )
    put_argparser.add_argument(
        "piece_squares",
        nargs="+",
        help="A piece-squares identifier like Kg1 for white king at g1, "
        "or ra8,e8 for black rooks at a8 and e8.",
    )
    put_argparser.add_argument(
        "-p", "--promoted", action="store_true", help="Set the added pieces as promoted pieces."
    )

    @argparse_command(put_argparser)
    async def do_put(self, args) -> None:
        """Put pieces on the chess board."""
        board: chess.Board = self.game_node.board()
        await self._put_pieces(board, args.piece_squares, args.promoted)

    async def _put_pieces(
        self, board: chess.Board, pieces_squares: list[str], promoted: bool = False
    ) -> None:
        """Put pieces on the board and set the current position.

        The pieces_squares strings are parsed as described by the put command.
        """
        # The following two dicts are each others inverse.
        squares_of: dict[chess.Piece, list[chess.Square]] = defaultdict(list)
        piece_at: dict[chess.Square, chess.Piece] = {}

        for piece_squares in pieces_squares:
            try:
                piece: chess.Piece = chess.Piece.from_symbol(piece_squares[0])
                squares: list[chess.Square] = [
                    chess.parse_square(s) for s in piece_squares[1:].split(",")
                ]
            except (IndexError, ValueError) as e:
                raise CommandFailure(f"Bad piece-squares expression: {piece_squares}") from e
            for square in squares:
                if square in piece_at:
                    raise CommandFailure(
                        f"You cannot put multiple pieces on {chess.square_name(square)}"
                    )
                squares_of[piece].append(square)
                piece_at[square] = piece
        for color in (chess.WHITE, chess.BLACK):
            king = chess.Piece(chess.KING, color)
            if not promoted and (king_squares := squares_of[king]):
                if len(king_squares) > 1:
                    raise CommandFailure(
                        "You cannot put multiple kings on the board "
                        "unless you use the `--promoted` flag."
                    )
                [king_square] = king_squares
                if (old_king_sq := board.king(color)) is not None and old_king_sq != king_square:
                    print(
                        f"Moving king from {chess.square_name(old_king_sq)} "
                        f"to {chess.square_name(king_square)}"
                    )
                    removed_king = board.remove_piece_at(old_king_sq)
                    assert removed_king == king
        for square, piece in piece_at.items():
            if (p := board.piece_at(square)) is not None:
                print(f"Replacing {piece_name(p)} at {chess.square_name(square)}")
            board.set_piece_at(square, piece, promoted)
        print("Putting:")
        for piece, squares in squares_of.items():
            if not squares:
                continue
            piece_name_ = piece_name(piece, capital=True)
            print(
                f"- {self.p.plural_noun(piece_name_, len(squares))} "  # type: ignore
                f"at {self.p.join([chess.square_name(sq) for sq in squares])}"  # type: ignore
            )
        await self.set_position(board)

    turn_argparser = ArgumentParser()
    turn_argparser.add_argument(
        "set_color",
        choices=["white", "black"],
        nargs="?",
        help="Set the turn to play. " "Note that this will reset the current game.",
    )

    @argparse_command(turn_argparser, alias="tu")
    async def do_turn(self, args) -> None:
        """Get or set the turn to play."""
        if args.set_color is None:
            print("white" if self.game_node.turn() == chess.WHITE else "black")
            return
        board: chess.Board = self.game_node.board()
        color: chess.Color = chess.WHITE if args.set_color == "white" else chess.BLACK
        if board.turn == color:
            print(f"It is already {args.set_color} to play.")
            return
        board.turn = color
        await self.set_position(board)
        print(f"It is now {args.set_color} to play.")

    castling_argparser = ArgumentParser(
        epilog="For example: You can get the current castling rights by entering "
        "'castling' with no arguments. To set white to be able to castle kingside "
        "and black to castle queenside, enter 'castling Kq'. To clear all castling rights "
        "simply type 'castling clear'."
    )
    castling_argparser.add_argument(
        "set_rights",
        nargs="?",
        help="Set castling rights by a short string which is either 'clear' "
        "or a combination of the letters 'K', 'k', 'Q' or 'q' "
        "where each letter denotes king- or queenside castling for white or black respectively.",
    )

    @argparse_command(castling_argparser, alias=["csl"])
    async def do_castling(self, args) -> None:
        """Get or set castling rights."""
        board: chess.Board = self.game_node.board()
        if args.set_rights is not None:
            if args.set_rights == "clear":
                args.set_rights = ""
            try:
                board.set_castling_fen(args.set_rights)
            except ValueError as e:
                raise CommandFailure(str(e)) from e
            await self.set_position(board)
        print(castling_descr(board))

    en_passant_argparser = ArgumentParser()
    en_passant_argparser.add_argument(
        "set",
        nargs="?",
        help='Either clear en-passant rights with "clear" (or "c"), or set en-passant possibility '
        "by providing the target square for the capturing pawn, that is on the 3rd or 6th rank.",
    )

    @argparse_command(en_passant_argparser, alias="ep")
    async def do_en_passant(self, args) -> None:
        """Get, set or clear en passant square in the current position."""
        board: chess.Board = self.game_node.board()
        if args.set is not None:
            if args.set in ["clear", "c"]:
                board.ep_square = None
            else:
                try:
                    square = chess.parse_square(args.set)
                except ValueError as e:
                    raise CommandFailure(
                        f'{args.set}: Must be "clear", "c", or a chess square like "d6".'
                    ) from e
                ep_rank_idx: int = 5 if board.turn == chess.WHITE else 2
                if not chess.square_rank(square) == ep_rank_idx:
                    raise CommandFailure("The en passant square must be on the 3rd/6th rank.")
                board.ep_square = square
            await self.set_position(board)
        if board.ep_square is not None:
            print(f"En passant is possible at {chess.square_name(board.ep_square)}.")
        else:
            print("En passant is not possible in this position.")
