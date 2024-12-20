import io
import os
import shutil
import tempfile
from collections import defaultdict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TextIO, override

import appdirs
import chess
import chess.pgn
import inflect
import toml

from .repl import CommandFailure, Repl
from .utils import move_str

FEN_WIDTH_UPPER_BOUND: int = 512


@dataclass
class InitArgs:
    """Arguments to the __init__() method of most ChessCli-classes."""

    file: str | None = None
    config_file: str = field(
        default_factory=lambda: os.path.join(appdirs.user_config_dir("chess-cli"), "config.toml")
    )


@dataclass
class GameHandle:
    """The headers of a game together with either the offset of the game in a file or a
    node in the parsed game."""

    headers: chess.pgn.Headers
    offset_or_game: int | chess.pgn.GameNode

    @property
    def offset(self) -> int | None:
        if isinstance(self.offset_or_game, int):
            return self.offset_or_game
        return None

    @property
    def game_node(self) -> chess.pgn.GameNode | None:
        if isinstance(self.offset_or_game, chess.pgn.GameNode):
            return self.offset_or_game
        return None


class ConfigError(Exception):
    """An exception raised if there is something wrong with the config file."""

    def __init__(self, config_file: str, msg: str) -> None:
        super().__init__(f"Bad config file at {config_file}: {msg}")


class Base(Repl):
    _config_file: str  # The path to the currently open config file.
    config: defaultdict[str, dict]  # The current configuration as a dictionary.
    _games: list[GameHandle]  # A list of all currentlyopen games.
    _pgn_file: TextIO | None  # The currently open PGN file.
    _game_idx: int  # The index of the currently selected game.
    # Inflect engine for plural forms.
    p: inflect.engine

    def __init__(self, args: InitArgs) -> None:
        super().__init__()

        self.p = inflect.engine()

        self.config = defaultdict(dict)
        self._config_file = args.config_file
        self.load_config()

        ## Read the PGN/FEN file or initialize a new game:
        self._games = []
        self._pgn_file = None
        self._game_idx = 0
        if args.file is not None:
            self.load_games_from_file(Path(args.file))
        else:
            self.add_new_game()

    def config_error(self, msg: str) -> ConfigError:
        """Make a `ConfigError` with the provided message."""
        return ConfigError(self._config_file, msg)

    @property
    def game_node(self) -> chess.pgn.GameNode:
        """Get the currently selected position / game node."""
        x = self._games[self._game_idx].game_node
        assert x is not None
        return x

    @game_node.setter
    def game_node(self, val: chess.pgn.GameNode) -> None:
        """Change the current position / game node."""
        self._games[self._game_idx] = GameHandle(val.game().headers, val)

    @property
    def games(self) -> list[GameHandle]:
        """A non-empty list of all currentlyopen games.

        (Please do not alter this.).
        """
        return self._games

    @property
    def game_idx(self) -> int:
        """The index of the currently selected game.

        `0 <= self.game_idx < len(self.games)`.
        """
        return self._game_idx

    @property
    def pgn_file_path(self) -> Path | None:
        """The name of the currently open PGN file if any."""
        return Path(self._pgn_file.name) if self._pgn_file is not None else None

    @override
    def prompt_str(self) -> str:
        return move_str(self.game_node)

    def save_config(self) -> None:
        """Save the current configuration."""
        os.makedirs(os.path.split(self._config_file)[0], exist_ok=True)
        with open(self._config_file, "w") as f:
            toml.dump(self.config, f)

    def load_config(self) -> None:
        try:
            with open(self._config_file) as f:
                config = toml.load(f)
                if not isinstance(config, dict):
                    raise self.config_error("The parsed TOML-file must be a dict")
                self.config = defaultdict(dict, config)
        except FileNotFoundError:
            self.poutput(
                "WARNING: No configuration file found at {self._config_file}. Will creat a new one."
            )
            self.save_config()
        except Exception as ex:
            raise self.config_error(repr(ex)) from ex

    def add_new_game(self, idx: int | None = None) -> None:
        """Add a new game in the game list.

        If idx is None, append to the end of the game list.
        """
        game: chess.pgn.Game = chess.pgn.Game()
        game_handle: GameHandle = GameHandle(game.headers, game)
        if idx is None:
            self._games.append(game_handle)
            self._game_idx = len(self._games) - 1
        else:
            self._games.insert(idx, game_handle)
            self._game_idx = idx

    def select_game(self, idx: int) -> None:
        """Select a game from the game list."""
        assert 0 <= idx < len(self._games)
        game_handle = self._games[idx]
        if game_handle.offset is not None:
            assert self._pgn_file is not None
            self._pgn_file.seek(game_handle.offset)
            game_node = chess.pgn.read_game(self._pgn_file)
            assert game_node is not None
            self._games[idx] = GameHandle(game_node.headers, game_node)
        assert self._games[idx].game_node is not None
        self._game_idx = idx

    def get_game(self, idx: int) -> chess.pgn.Game:
        """Get a game from the game list without keeping the game in memory.

        If you should search for a game and the headers are not sufficient,
        this might be useful. But remember that you should not make changes to
        this game.
        """
        assert 0 <= idx < len(self._games)
        game_handle = self._games[idx]
        if game_handle.offset is not None:
            assert self._pgn_file is not None
            self._pgn_file.seek(game_handle.offset)
            game_node = chess.pgn.read_game(self._pgn_file)
            assert game_node is not None
            return game_node.game()
        else:
            assert game_handle.game_node is not None
            return game_handle.game_node.game()

    def visit_game(self, idx: int, visitor: chess.pgn.BaseVisitor):
        """Visit a game in the game list without loading it into memory."""
        assert 0 <= idx < len(self._games)
        game_handle = self._games[idx]
        if game_handle.offset is not None:
            assert self._pgn_file is not None
            self._pgn_file.seek(game_handle.offset)
            return chess.pgn.read_game(self._pgn_file, Visitor=lambda: visitor)
        else:
            assert game_handle.game_node is not None
            return game_handle.game_node.game().accept(visitor)

    def rm_game(self, game_idx: int) -> None:
        """Remove a game from the game list.

        If it is the current game, the current game will be shifted to the next game
        unless the current game is the last game in which case it'll be shifted to the
        previous.  If the game list becomes empty a new empty game will be added.
        """
        self._games.pop(game_idx)
        if game_idx < self.game_idx or self.game_idx == len(self.games):
            if self.games:
                self._game_idx -= 1
            else:
                self.add_new_game()

    def load_games_from_file(self, file_path: Path) -> None:
        """Load games from a PGN file or a starting position from a FEN.

        If loading a PGN file, all games will be discarded.
        A FEN will discard the current game and set the position as starting position.
        """
        try:
            file = open(file_path)  # noqa: SIM115
        except OSError as ex:
            raise CommandFailure(f"Error: Loading of {file_path} failed: {ex}") from ex
        try:
            # Try to read as FEN:
            content = file.read(FEN_WIDTH_UPPER_BOUND)
            try:
                board = chess.Board(content)
                print(
                    f"Successfully read {file_path} as FEN,"
                    " which is set to the starting position."
                )
                self.add_new_game()
                self.game_node.game().setup(board)
            except ValueError:
                # Try to parse as PGN.
                file.seek(0)
                games: list[GameHandle] = []
                while True:
                    offset: int = file.tell()
                    headers = chess.pgn.read_headers(file)
                    if headers is None:
                        break
                    games.append(GameHandle(headers, offset))
                if not games:
                    raise CommandFailure(f"Failed to parse {file_path} as FEN or PGN.") from None
                # Reset analysis.
                # TODO:
                # self.stop_engines()
                # self.init_analysis()
                self._games = games
                self._pgn_file = file
                self.select_game(0)
                print(
                    "Successfully loaded",
                    self.p.no("game", len(self._games)),  # type: ignore
                    "as PGN.",
                )
        except Exception:
            with suppress(OSError):
                file.close()

    def load_games_from_pgn_str(self, pgn: str) -> None:
        """Load games from a PGN string.

        Upon success, all previous games will be discarded.
        """
        pgn_io = io.StringIO(pgn)
        games: list[GameHandle] = []
        while game := chess.pgn.read_game(pgn_io):
            game_handle = GameHandle(game.headers, game)
            games.append(game_handle)
        if not games:
            raise CommandFailure("Couldn't read any games from the PGN.")
        self._games = games
        self._pgn_file = None
        self.select_game(0)
        self.poutput(f"Successfully loaded {len(self._games)} game(s).")

    def _reload_games(self, file_path: Path) -> None:
        """Open and load all games from `file_path`, assuming it contains exactly the
        same games, in same order, as `self.games`.

        The current file will then be set to this file.
        """
        current_game: int = self.game_idx
        self._pgn_file = open(file_path)  # NOQA: SIM115
        for i, game in enumerate(self.games):
            offset: int = self._pgn_file.tell()
            headers = chess.pgn.read_headers(self._pgn_file)
            assert headers is not None
            assert headers == game.headers, f"{headers}\n  not equal to  \n{game.headers}"
            if game.game_node is None:
                self.games[i] = GameHandle(headers, offset)
        self.select_game(current_game)

    def write_games(self, file: IO[str], games: Iterable[int]) -> None:
        """Print games to a file or other text stream.

        param file: A stream to write the games to.
        param games: Indices of the games to print.
        """
        current_game: int = self.game_idx
        is_first: bool = True
        for i in games:
            if not is_first:
                print(file=file)  # A newline is needed between all games.")
            else:
                is_first = False
            self.select_game(i)
            print(self.game_node.game(), file=file)
        self.select_game(current_game)

    def save_games(self, file_path: Path | None, games: Iterable[int]) -> None:
        """Save all games and update the current PGN file to `file_name`.

        param file_path: The file to write to. If it is `None`, the current PGN file will be used,
                if that is also `None`, an assertian will be fired.
        param games: List of game indices to save.
        """
        file_path = file_path or self.pgn_file_path
        assert file_path is not None
        if self.pgn_file_path is None or not os.path.samefile(file_path, self.pgn_file_path):
            with open(file_path, "w+") as f:
                self.write_games(f, games)
        else:
            file: IO[str] = tempfile.NamedTemporaryFile(mode="w+", delete=False)
            tempfile_name = file.name
            try:
                self.write_games(file, games)
                file.close()
            except Exception as e:
                file.close()
                os.remove(tempfile_name)
                raise e
            shutil.move(tempfile_name, file_path)
        self._reload_games(file_path)
        print(f"Successfully saved to {file_path}!")
