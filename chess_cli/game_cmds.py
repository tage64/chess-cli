import argparse
import os
import textwrap
from argparse import ArgumentParser
from collections.abc import Iterable
from typing import assert_never

import chess
import chess.pgn

from .base import CommandFailure
from .game_utils import GameUtils
from .repl import argparse_command, command
from .utils import MoveNumber, piece_name


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
    save_argparser.add_argument(
        "file", nargs="?", help="File to save to. Defaults to the loaded file."
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
        if args.file is None:
            if self.pgn_file_name is None:
                self.poutput("Error: No file selected.")
                return
            self.save_games(args.file)
        else:
            if self.pgn_file_name is not None and os.path.samefile(args.file, self.pgn_file_name):
                self.save_games(args.file)
            else:
                self.save_games_to_file(args.file)

    load_argparser = ArgumentParser()
    load_argparser.add_argument("file", help="PGN file to read.")

    @argparse_command(load_argparser, alias="ld")
    def do_load(self, args) -> None:
        """Load games from a PGN file.

        Note that the current game will be lost.
        """
        self.load_games(args.file)

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

    setup_argparser = ArgumentParser()
    pos_group = setup_argparser.add_mutually_exclusive_group(required=True)
    pos_group.add_argument("fen", nargs="?", help="The position as an FEN string.")
    pos_group.add_argument("-e", "--empty", action="store_true", help="Setup an empty board.")
    pos_group.add_argument(
        "-s", "--start", action="store_true", help="Setup the starting position."
    )

    @argparse_command(setup_argparser, alias="st")
    async def do_setup(self, args) -> None:
        """Setup a starting position."""
        board: chess.Board
        if args.fen:
            try:
                board = chess.Board(args.fen)
            except ValueError as e:
                raise CommandFailure(f"Bad FEN: {e}") from None
        elif args.empty:
            board = chess.Board.empty()
        elif args.start:
            board = chess.Board()
        else:
            assert_never(args)
        await self.set_position(board)

    clear_argparser = ArgumentParser()
    clear_argparser.add_argument("square", type=chess.parse_square, help="The square to clear.")

    @argparse_command(clear_argparser)
    async def do_clear(self, args) -> None:
        """Clear a square on the chess board."""
        square_name: str = chess.square_name(args.square)
        board: chess.Board = self.game_node.board()
        removed: chess.Piece | None = board.remove_piece_at(args.square)
        if removed is None:
            print(f"There is no piece at {square_name}")
            return
        print(f"Removed {piece_name(removed)} at {square_name}")
        if self.game_node.parent is not None:
            print("Setting this as the starting position of the game.")
        await self.set_position(board)

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

    @argparse_command(put_argparser)
    async def do_put(self, args) -> None:
        """Put pieces on the chess board."""
        board: chess.Board = self.game_node.board()
        for piece_squares in args.piece_squares:
            try:
                piece: chess.Piece = chess.Piece.from_symbol(piece_squares[0])
                squares: list[chess.Square] = [
                    chess.parse_square(s) for s in piece_squares[1:].split(",")
                ]
            except (IndexError, ValueError) as e:
                raise CommandFailure(f"Bad piece-squares expression: {piece_squares}") from e
            for square in squares:
                if (p := board.piece_at(square)) is not None:
                    print(f"Replacing {piece_name(p)} at {chess.square_name(square)}")
                board.set_piece_at(square, piece)
        await self.set_position(board)

    turn_argparser = ArgumentParser()
    turn_argparser.add_argument(
        "set_color",
        choices=["white", "black"],
        nargs="?",
        help="Set the turn to play. " "Note that this will reset the current game.",
    )

    @argparse_command(turn_argparser, alias="t")
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

        def castling_descr(color: chess.Color) -> str:
            if board.has_kingside_castling_rights(color):
                if board.has_queenside_castling_rights(color):
                    return "can castle on both sides"
                return "can castle kingside"
            if board.has_queenside_castling_rights(color):
                return "can castle queenside"
            return "is not allowed to castle"

        white_descr = castling_descr(chess.WHITE)
        black_descr = castling_descr(chess.BLACK)
        if white_descr == black_descr:
            if white_descr == "is not allowed to castle":
                print("Neither white nor black is allowed to castle.")
            else:
                print(f"White and black {white_descr}")
        else:
            print(f"White {white_descr} and black {black_descr}.")

    en_passant_argparser = ArgumentParser()
    en_passant_subcmds = en_passant_argparser.add_subparsers(dest="subcmd")
    en_passant_subcmds.add_parser(
        "get",
        help="Get the possible en passant square if any. "
        "(This is the default if no arguments are supplied.)",
    )
    en_passant_set_argparser = en_passant_subcmds.add_parser(
        "set", help="Set a valid en passant square."
    )
    en_passant_set_argparser.add_argument(
        "square",
        type=chess.parse_square,
        help="The square to which the capturing pawn will move, I.E. on the 6th or 3rd rank.",
    )
    en_passant_subcmds.add_parser(
        "clear", help="Remove en passant possibilities in the current position."
    )

    @argparse_command(en_passant_argparser, alias="ep")
    async def do_en_passant(self, args) -> None:
        """Get, set or clear en passant square in the current position."""
        board: chess.Board = self.game_node.board()
        match args.subcmd:
            case "get" | None:
                pass  # It will be printed in any case.
            case "set":
                ep_rank_idx: int = 5 if board.turn == chess.WHITE else 2
                if not chess.square_rank(args.square) == ep_rank_idx:
                    raise CommandFailure("The en passant square must be on the 3rd/6th rank.")
                board.ep_square = args.square
                await self.set_position(board)
            case "clear":
                board.ep_square = None
                await self.set_position(board)
            case x:
                assert_never(x)
        if board.ep_square is not None:
            print(f"En passant is possible at {chess.square_name(board.ep_square)}.")
        else:
            print("En passant is not possible in this position.")
