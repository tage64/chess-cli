from .utils import *

from dataclasses import dataclass, field
import os
import shutil
import tempfile
from typing import *

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
        default_factory=lambda: os.path.join(appdirs.user_config_dir("chess-cli"), "config.toml")
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


class ConfigError(Exception):
    "An exception raised if there is something wrong with the config file."

    def __init__(self, config_file: str, msg: str) -> None:
        super().__init__(f"Bad config file at {config_file}: {msg}")


class Base(cmd2.Cmd):
    _config_file: str  # The path to the currently open config file.
    config: dict  # The current configuration as a dictionary.
    _games: list[_GameHandle]  # A list of all currentlyopen games.
    _pgn_file: Optional[IO[str]]  # The currently open PGN file.
    _game_idx: int  # The index of the currently selected game.

    def __init__(self, args: InitArgs) -> None:
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

        self.register_postcmd_hook(self.set_prompt)

    def config_error(self, msg: str) -> ConfigError:
        "Make a `ConfigError` with the provided message."
        return ConfigError(self._config_file, msg)

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

    @property
    def games(self) -> list[_GameHandle]:
        "A non-empty list of all currentlyopen games.  (Please do not alter this.)"
        return self._games

    @property
    def game_idx(self) -> int:
        "The index of the currently selected game.  `0 <= self.game_idx < len(self.games)`"
        return self.game_idx

    @property
    def pgn_file_name(self) -> Optional[str]:
        "The name of the currently open PGN file if any."
        return self._pgn_file.name if self._pgn_file is not None else None

    def set_prompt(
        self, postcommand_data: cmd2.plugin.PostcommandData
    ) -> cmd2.plugin.PostcommandData:
        # Overrides method from Cmd2.
        self.prompt = f"{move_str(self.game_node)}: "
        return postcommand_data

    def save_config(self) -> None:
        "Save the current configuration."
        os.makedirs(os.path.split(self._config_file)[0], exist_ok=True)
        with open(self._config_file, "w") as f:
            toml.dump(self.config, f)

    def load_config(self, config_file: str) -> None:
        self._config_file = config_file
        try:
            with open(self._config_file) as f:
                self.config = toml.load(f)
                if not isinstance(self.config, dict):
                    raise self.config_error("The parsed TOML-file must be a dict")
        except Exception as ex:
            raise self.config_error(repr(ex))

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
            game_node = chess.pgn.read_game(self._pgn_file)  # type: ignore
            assert game_node is not None
            self._games[idx] = _GameHandle(game_node.headers, game_node)
        assert self._games[idx].game_node is not None
        self._game_idx = idx

    def rm_game(self, game_idx: int) -> None:
        """Remove a game from the game list.  If it is the current game, the current game will
        be shifted to the next game unless the current game is the last game in which case it'll
        be shifted to the previous.  If the game list becomes empty a new empty game will be added.
        """
        self._games.pop(game_idx)
        if game_idx < self.game_idx or self.game_idx == len(self.games):
            if self.games:
                self._game_idx -= 1
            else:
                self.add_new_game()

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

    def _reload_games(self, file_name: str) -> None:
        """Open and load all games from `file_name`, assuming it contains exactly the same
        games, in same order, as `self.games`.  The current file will then be set to this file.
        """
        current_game: int = self.game_idx
        self._pgn_file = open(file_name)
        for i, game in enumerate(self.games):
            offset: int = self._pgn_file.tell()
            headers = chess.pgn.read_headers(self._pgn_file)
            assert headers is not None
            assert headers == game.headers
            if game.game_node is None:
                self.games[i] = _GameHandle(headers, offset)
        self.select_game(current_game)

    def write_games(self, file: IO[str]) -> None:
        "Print all games to a file or other text stream."
        current_game: int = self.game_idx
        for i in range(len(self.games)):
            self.select_game(i)
            print(self.game_node.game(), file=file)
        self.select_game(current_game)

    def save_games_to_file(self, file_name: str) -> None:
        """Print all games to a file.
        `file_name` should not be `self.pgn_file_name` unless you know what you are doing.
        """
        with open(file_name, "w+") as f:
            self.write_games(f)

    def save_games(self, file_name: Optional[str]) -> None:
        """Save all games and update the current PGN file to `file_name`."
        If `file_name` is `None`, the current PGN file will be used,
        if that is also `None`, an assertian will be fired.
        """
        file_name = file_name or self.pgn_file_name
        assert file_name is not None
        if self.pgn_file_name is None or not os.path.samefile(file_name, self.pgn_file_name):
            self.save_games_to_file(file_name)
        else:
            file: IO[str] = tempfile.NamedTemporaryFile(mode="w+", delete=False)
            tempfile_name = file.name
            try:
                self.write_games(file)
                file.close()
            except Exception:
                file.close()
                os.remove(tempfile_name)
            shutil.move(tempfile_name, file_name)
        self._reload_games(file_name)
