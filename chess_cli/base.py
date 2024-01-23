from .utils import *

from dataclasses import dataclass, field
from typing import *
import os

import appdirs  # type: ignore
import chess
import chess.pgn
import cmd2
import toml  # type: ignore


@dataclass
class InitArgs:
    "Arguments to the __init__() method of most ChessCli-classes."
    pgn_file: Optional[str] = None
    config_file: str = field(
        default_factory=lambda: os.path.join(
            appdirs.user_config_dir("chess-cli"), "config.toml"
        )
    )


@dataclass
class _GameHandle:
    "The headers of a game together with either the offset of the game in a file or a node in the parsed game."
    headers: chess.pgn.Headers
    offset_or_game: int | chess.pgn.GameNode

    @property
    def offset(self) -> Optional[int]:
        if isinstance(self.offset_or_game, int):
            return self.offset_or_game
        return None

    @property
    def game_node(self) -> Optional[chess.pgn.GameNode]:
        if isinstance(self.offset_or_game, chess.pgn.GameNode):
            return self.offset_or_game
        return None


class CommandFailure(cmd2.exceptions.SkipPostcommandHooks):
    "An exception raised when a command fails and doesn't perform any updates to the game."
    pass


class Base(cmd2.Cmd):
    _config_file: str  # The path to the currently open config file.
    config: dict  # The current configuration as a dictionary.
    _games: list[_GameHandle]  # A list of all currentlyopen games.
    _pgn_file: Optional[TextIO]  # The currently open PGN file.
    _game_idx: int  # The index of the currently selected game.

    def __init__(self, args: InitArgs):
        ## Initialize cmd2:
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts, include_py=True, allow_cli_args=False)
        self.self_in_py = True

        self.load_config(args.config_file)

        ## Read the PGN file or initialize a new game:
        self._games = []
        self._pgn_file = None
        self._game_idx = 0
        if args.pgn_file is not None:
            self.load_games(args.pgn_file)
        else:
            self.add_new_game()

    def load_config(self, config_file: str) -> None:
        self._config_file = config_file
        try:
            with open(self._config_file) as f:
                self.config = toml.load(f)
                if not isinstance(self.config, dict):
                    raise Exception("Failed to parse configuration")
        except Exception as ex:
            self.config = {}
            self.poutput(
                f"Error while processing config file at '{self._config_file}': {repr(ex)}"
            )
            self.poutput("This session will be started with an empty configuration.")

    def load_games(self, file_name: str) -> None:
        "Load games from a PGN file. Upon success, all previous games will be discarded."
        try:
            with open(file_name) as pgn_file:
                games: list[_GameHandle] = []
                while True:
                    offset: int = pgn_file.tell()
                    headers = chess.pgn.read_headers(pgn_file)
                    if headers is None:
                        break
                    games.append(_GameHandle(headers, offset))
                if not games:
                    self.poutput(f"Error: Couldn't find any game in {file_name}")
                    raise CommandFailure()
                # Reset analysis.
                # TODO:
                # self.stop_engines()
                # self.init_analysis()
                self._games = games
                self._pgn_file = pgn_file
                self.select_game(0)
                self.poutput(f"Successfully loaded {len(self._games)} game(s).")
        except OSError as ex:
            self.poutput(f"Error: Loading of {file_name} failed: {ex}")

    def add_new_game(self, idx: Optional[int] = None) -> None:
        "Add a new game in the game list. If idx is None, append to the end of the game list."
        game: chess.pgn.Game = chess.pgn.Game()
        game_handle: _GameHandle = _GameHandle(game.headers, game)
        if idx is None:
            self._games.append(game_handle)
            self._game_idx = len(self._games) - 1
        else:
            self._games.insert(idx, game_handle)
            self._game_idx = idx

    def select_game(self, idx: int) -> None:
        "Select a game from the game list."
        assert 0 <= idx < len(self._games)
        game_handle = self._games[idx]
        if game_handle.offset is not None:
            assert self._pgn_file is not None
            self._pgn_file.seek(game_handle.offset)
            game_node = chess.pgn.read_game(self._pgn_file)
            assert game_node is not None
            self._games[idx] = _GameHandle(game_node.headers, game_node)
        assert self._games[idx].game_node is not None
        self._game_idx = idx

    @property
    def game_node(self) -> chess.pgn.GameNode:
        "Get the currently selected position / game node."
        x = self._games[self._game_idx].game_node
        assert x is not None
        return x

    @game_node.setter
    def game_node(self, val: chess.pgn.GameNode) -> None:
        "Change the current position / game node."
        self._games[self._game_idx] = _GameHandle(val.game().headers, val)

    def set_prompt(
        self, postcommand_data: cmd2.plugin.PostcommandData
    ) -> cmd2.plugin.PostcommandData:
        # Overrides method from Cmd2.
        self.prompt = f"{move_str(self.game_node)}: "
        return postcommand_data
