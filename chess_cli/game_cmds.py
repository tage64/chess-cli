from collections import deque
from typing import *

import chess
import chess.pgn
import cmd2

from .game_utils import *


class GameCmds(GameUtils):
    "Basic commands to view and alter the game."
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

    goto_argparser = cmd2.Cmd2ArgumentParser()
    goto_argparser.add_argument(
        "move",
        help=(
            "A move, move number or both. E.G. 'e4', '8...' or '9.dxe5+'. Or the string 'start'/'s'"
            " or 'end'/'e' for jumping to the start or end of the game."
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
        """Goto a move specified by a move number or a move in standard algibraic
        notation.

        If a move number is specified, it will follow the main line to that move if it does exist.
        If a move like "e4" or "Nxd5+" is specified as well, it will go to the specific move number
        and search between variations at that level for the specified move. If only a move but not a
        move number and no other constraints are given, it'll first search sidelines at the current
        move, then follow the mainline and check if any move or sideline matches, but not recurse
        into sidelines. Lastly, it'll search backwards in the game.
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

    games_argparser = cmd2.Cmd2ArgumentParser()
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

    @cmd2.with_argparser(games_argparser)  # type: ignore
    def do_games(self, args) -> None:
        "List, select, delete or create new games."
        match args.subcmd:
            case "ls":
                for i, game in enumerate(self.games):
                    show_str: str = f"{i+1}. "
                    if i == self.game_idx:
                        show_str += "[*] "
                    show_str += f"{game.headers['White']} - {game.headers['Black']}"
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
                assert False, "Unknown subcommand."

    save_argparser = cmd2.Cmd2ArgumentParser()
    save_argparser.add_argument(
        "file", nargs="?", help="File to save to. Defaults to the loaded file."
    )
    save_argparser.add_argument(
        "-t",
        "--this",
        action="store_true",
        help="Save only the current game and discard any changes in the other games.",
    )

    @cmd2.with_argparser(save_argparser)  # type: ignore
    def do_save(self, args) -> None:
        "Save the games to a PGN file."
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

    load_argparser = cmd2.Cmd2ArgumentParser()
    load_argparser.add_argument("file", help="PGN file to read.")

    @cmd2.with_argparser(load_argparser)  # type: ignore
    def do_load(self, args) -> None:
        "Load games from a PGN file. Note that the current game will be lost."
        self.load_games(args.file)

    promote_argparser = cmd2.Cmd2ArgumentParser()
    promote_group = promote_argparser.add_mutually_exclusive_group()
    promote_group.add_argument(
        "-m",
        "--main",
        action="store_true",
        help="Promote this move to be main variation.",
    )
    promote_group.add_argument(
        "-n", "--steps", type=int, help="Promote this variation n number of steps."
    )

    @cmd2.with_argparser(promote_argparser)  # type: ignore
    def do_promote(self, args) -> None:
        "If current move is a side line, promote it so that it'll be closer to main variation."
        if not isinstance(self.game_node, chess.pgn.ChildNode):
            return
        if args.main:
            self.game_node.parent.variations.remove(self.game_node)
            self.game_node.parent.variations.insert(0, self.game_node)
        else:
            n = args.steps or 1
            for _ in range(n):
                self.game_node.parent.promote(self.game_node)

    demote_argparser = cmd2.Cmd2ArgumentParser()
    demote_group = demote_argparser.add_mutually_exclusive_group()
    demote_group.add_argument(
        "-l",
        "--last",
        action="store_true",
        help="Demote this move to be the last variation.",
    )
    demote_group.add_argument(
        "-n", "--steps", type=int, help="Demote this variation n number of steps."
    )

    @cmd2.with_argparser(demote_argparser)  # type: ignore
    def do_demote(self, args) -> None:
        "If current move is the main variation or if it isn't the last variation, demote it so it'll be far from the main variation."
        if not isinstance(self.game_node, chess.pgn.ChildNode):
            return
        if args.last:
            self.game_node.parent.variations.remove(self.game_node)
            self.game_node.parent.variations.append(self.game_node)
        else:
            n = args.steps or 1
            for _ in range(n):
                self.game_node.parent.demote(self.game_node)

    def do_variations(self, _) -> None:
        "Print all variations following this move."
        self.show_variations(self.game_node)

    def do_sidelines(self, _) -> None:
        "Show all sidelines to this move."
        if self.game_node.parent is not None:
            self.show_variations(self.game_node.parent)
