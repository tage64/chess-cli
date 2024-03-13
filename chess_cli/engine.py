import asyncio
import enum
import logging
import logging.handlers
import queue
import shutil
from collections import deque
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import assert_never, override

import chess.engine
from pydantic import BaseModel

from .base import Base, CommandFailure, InitArgs

ENGINE_TIMEOUT: int = 120  # Timeout for engine related operations.


class EngineProtocol(enum.StrEnum):
    """Type of protocol for a chess engine."""

    UCI = enum.auto()
    XBOARD = enum.auto()


class EngineConf(BaseModel):
    """Configuration for an engine."""

    path: str  # Path of engine executable.
    protocol: EngineProtocol
    options: dict[str, str | int | bool | None] = dict()
    fullname: str | None = None  # Full name of the engine from id.name.
    # The directory where the engine is installed.
    # This will be removed if the engine is removed.
    install_dir: str | None = None
    # Loaded instance of this engine. This is not really part of the configuration but stored here
    # anyway and discarded when saving the configuration.
    loaded_as: set[str] = set()


@dataclass
class LoadedEngine:
    config_name: str  # The name of the engine in the configuration.
    engine: chess.engine.Protocol  # The actual engine instance.


class Engine(Base):
    """An extention to chess-cli to support chess engines."""

    _engine_confs: dict[str, EngineConf]  # Configuration for all the engines.
    # All the currently loaded engines.  Note that this is indexed by the name given to the
    # loaded instance which may not be the same as in `_engine_confs`.
    _loaded_engines: dict[str, LoadedEngine]
    # The currently selected engine. Should be a member of loaded_engines.
    _selected_engine: str | None
    _engines_saved_log: deque[str]  # Log messages from all engines.
    # A queue for incoming log messages from engines.
    _engines_log_queue: queue.SimpleQueue[logging.LogRecord]

    def __init__(self, args: InitArgs) -> None:
        self._engine_confs = {}
        # No engines are loaded or selected at startup.
        self._loaded_engines = {}
        self._selected_engine = None

        super().__init__(args)

        ## Setup logging:
        self._engines_saved_log = deque()
        self._engines_log_queue = queue.SimpleQueue()
        log_handler = logging.handlers.QueueHandler(self._engines_log_queue)
        log_handler.setLevel(logging.WARNING)
        log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        chess.engine.LOGGER.addHandler(log_handler)

    # Close engines when REPL is quit.
    @override
    async def cmd_loop(self, *args, **kwargs) -> None:
        await super().cmd_loop(*args, **kwargs)
        while self.selected_engine is not None:
            await self.close_engine(self.selected_engine)

    @property
    def engine_confs(self) -> Mapping[str, EngineConf]:
        """Get all configured engines."""
        return self._engine_confs.items().mapping

    @property
    def loaded_engines(self) -> Mapping[str, LoadedEngine]:
        """Get all the currently loaded engines in a {name: engine} dictionary."""
        return self._loaded_engines.items().mapping

    @property
    def selected_engine(self) -> str | None:
        """The currently selected engine.

        `None` iff `self.loaded_engines()` is empty.
        """
        return self._selected_engine

    def get_selected_engine(self) -> str:
        """Get the selected engine or raise CommandFailure."""
        if self.selected_engine is None:
            self.poutput("Error: No engine is selected.")
            raise CommandFailure()
        return self.selected_engine

    def select_engine(self, engine: str) -> None:
        """Select an engine."""
        assert engine in self.loaded_engines
        self._selected_engine = engine

    async def close_engine(self, name: str) -> None:
        """Stop and quit an engine."""
        engine: LoadedEngine = self._loaded_engines.pop(name)
        self.engine_confs[engine.config_name].loaded_as.remove(name)
        async with asyncio.timeout(ENGINE_TIMEOUT):
            await engine.engine.quit()
        if self.selected_engine == engine:
            try:
                self.select_engine(next(iter(self.loaded_engines)))
            except StopIteration:
                self._selected_engine = None
        self._selected_engine = None

    def get_engines_log(self) -> Sequence[str]:
        """Get log messages from all engines."""
        # Read all log messages from the log_queue:
        # Note that this is not wait free.
        with suppress(queue.Empty):
            while True:
                self._engines_saved_log.append(self._engines_log_queue.get_nowait().message)
        return self._engines_saved_log

    def clear_engines_log(self) -> None:
        """Clear the log."""
        self._engines_saved_log.clear()
        # Note that this is not wait free.
        try:
            while True:
                self._engines_log_queue.get_nowait()
        except queue.Empty:
            pass

    @override
    def load_config(self) -> None:
        super().load_config()
        ## Retrieve the engine configurations from `self.config`:
        engine_confs = self.config["engine-configurations"]
        assert isinstance(engine_confs, dict), "Section 'engine-configurations' must be a dict"
        self._engine_confs = {
            name: EngineConf.validate(values) for (name, values) in engine_confs.items()
        }

    @override
    def save_config(self) -> None:
        self.config["engine-configurations"] = {
            name: conf.model_dump(mode="json", exclude={"loaded_as"})
            for (name, conf) in self.engine_confs.items()
        }
        super().save_config()

    def add_engine(self, path: str, protocol: EngineProtocol, name: str) -> None:
        """Add an engine to `self.engine_confs` but do not load it.

        `name` should not be in `self.engine_confs`.
        """
        if name in self.engine_confs:
            raise CommandFailure(
                f"The name {name} is already given to an engine. "
                f"You have to remove it with `engine rm {name}` first."
            )
        engine_conf: EngineConf = EngineConf(path=path, protocol=protocol)
        self._engine_confs[name] = engine_conf
        self.save_config()

    def rm_engine(self, name: str) -> None:
        """Remove an engine from `self.engine_confs`.

        The engine must not be loaded.
        """
        removed: EngineConf = self._engine_confs.pop(name)
        if removed.install_dir is not None:
            shutil.rmtree(removed.install_dir)
        self.save_config()

    def show_engine(self, name: str, verbose: bool = False) -> None:
        """Show an engine, loaded or not."""
        # TODO: Fix separate methods for showing loaded and unloaded engines.
        if name in self.loaded_engines:
            conf: EngineConf = self.engine_confs[self.loaded_engines[name].config_name]
        else:
            conf = self.engine_confs[name]
        show_str: str
        show_str = ">" if name == self.selected_engine else " "
        show_str += name
        if conf.fullname is not None:
            show_str += ": " + conf.fullname
        if name in self.loaded_engines:
            show_str += ", (loaded)"
        else:
            show_str += ", (not loaded)"
        if name == self.selected_engine:
            show_str += ", (selected)"
        self.poutput(show_str)
        if verbose:
            self.poutput(f"    Executable: {conf.path}")
            self.poutput(f"    Protocol: {conf.protocol}")
            if name in self.loaded_engines:
                engine: chess.engine.Protocol = self.loaded_engines[name].engine
                for key, val in engine.id.items():
                    if key != "name":
                        self.poutput(f"   {key}: {val}")

    async def set_engine_option(
        self, engine: str, name: str, value: str | int | bool | None
    ) -> None:
        """Set an option on a loaded engine."""
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].engine.options
        option: chess.engine.Option = options[name]
        if option.type in ["string", "file", "path"]:
            if not isinstance(value, str):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is"
                    f" {type(value)} which doesn't match very well."
                )
        elif option.type == "combo":
            if not isinstance(value, str):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is"
                    f" {type(value)} which doesn't match very well."
                )

            if not option.var:
                raise ValueError(
                    f"There are no valid alternatives for {option.name}, so you cannot set it to"
                    " any value. It's strange I know, but I'm probably not the engine's author so"
                    " I can't do much about it."
                )
            if value not in option.var:
                raise ValueError(
                    f"{value} is not a valid alternative for the combobox {option.name}. The list"
                    f" of valid options is: {option.var!r}."
                )
        elif option.type == "spin":
            if not isinstance(value, int):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is"
                    f" {type(value)} which doesn't match very well."
                )
            if option.min is not None and value < option.min:
                raise ValueError(
                    f"The minimum value for {option.name} is {option.min}, you specified {value}."
                )
            if option.max is not None and value > option.max:
                raise ValueError(
                    f"The maximum value for {option.name} is {option.max}, you specified {value}."
                )
        elif option.type == "check":
            if not isinstance(value, bool):
                raise ValueError(
                    f"{name} is a {option.type} according to the engine but the given type is"
                    f" {type(value)} which doesn't match very well."
                )
        elif option.type in ["button", "reset", "save"]:
            if value is not None:
                raise ValueError(
                    f"{name} is a button according to the engine but the given value is a"
                    f" {type(value)} which doesn't really make any sence."
                )
        else:
            raise AssertionError(f"Unsupported option type: {option.type}")
        async with asyncio.timeout(ENGINE_TIMEOUT):
            await self.loaded_engines[engine].engine.configure({option.name: value})

    async def load_engine(self, config_name: str, name: str) -> None:
        """Load an engine.

        `config_name` must be in `self.engine_confs`.
        """
        engine_conf: EngineConf = self.engine_confs[config_name]
        engine: chess.engine.Protocol
        try:
            async with asyncio.timeout(ENGINE_TIMEOUT):
                match engine_conf.protocol:
                    case EngineProtocol.UCI:
                        _, engine = await chess.engine.popen_uci(engine_conf.path)
                    case EngineProtocol.XBOARD:
                        _, engine = await chess.engine.popen_xboard(engine_conf.path)
                    case x:
                        assert_never(x)
        except chess.engine.EngineError as e:
            self.poutput(
                f"Engine Terminated Error: The engine {engine_conf.path} didn't behaved as it"
                " should. Either it is broken, or this program containes a bug. It might also be"
                " that you've specified wrong path to the engine executable."
            )
            raise e
        except FileNotFoundError as e:
            self.poutput(f"Error: Couldn't find the engine executable {engine_conf.path}: {e}")
            raise e
        except OSError as e:
            self.poutput(f"Error: While loading engine executable {engine_conf.path}: {e}")
            raise e
        self._loaded_engines[name] = LoadedEngine(config_name, engine)
        engine_conf.fullname = engine.id.get("name")
        engine_conf.loaded_as.add(name)

        ## Set all the configured options:
        invalid_options: list[str] = []
        for opt_name, value in engine_conf.options.items():
            try:
                await self.set_engine_option(name, opt_name, value)
            except ValueError as e:
                self.poutput(
                    f"Warning: Couldn't set {opt_name} to {value} as specified in the"
                    " configuration."
                )
                self.poutput(f"    {e}")
                invalid_options.append(opt_name)
                self.poutput(f"  {opt_name} will be removed from the configuration.")
        for x in invalid_options:
            del engine_conf.options[x]
        self.select_engine(name)
