from .base import *

from typing import *

import chess
import chess.pgn
import cmd2


class GameCmds(Base):
    "Basic commands to view and alter the game."
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

    def show_variations(self, node: chess.pgn.GameNode) -> None:
        next = node.next()
        if next is not None:
            show_items = [move_str(next, include_sideline_arrows=False)]
            for variation in node.variations[1:]:
                show_items.append(
                    move_str(
                        variation,
                        include_move_number=False,
                        include_sideline_arrows=False,
                    )
                )
            self.poutput(", ".join(show_items))

    def do_variations(self, _) -> None:
        "Print all variations following this move."
        self.show_variations(self.game_node)

    def do_sidelines(self, _) -> None:
        "Show all sidelines to this move."
        if self.game_node.parent is not None:
            self.show_variations(self.game_node.parent)
