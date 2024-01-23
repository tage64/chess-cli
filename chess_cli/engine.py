from .base import *

from collections import deque, defaultdict
from dataclasses import dataclass
import logging
import logging.handlers
import queue
from typing import *

import chess.engine


@dataclass
class _EngineConf:
    "Configuration for an engine."
    path: str  # Path of engine executable.
    protocol: str  # "uci" or "xboard"
    options: dict[str, Union[str, int, bool, None]] = {}
    fullname: Optional[str] = None  # Full name of the engine from id.name.
    # The directory where the engine is installed.
    # This will be removed if the engine is removed.
    install_dir: Optional[str] = None


class Engine(Base):
    "An extention to chess-cli to support chess engines."
    _loaded_engines: dict[str, chess.engine.SimpleEngine]  # All the currently loaded engines.
    # The currently selected engine. Should be a member of loaded_engines.
    _selected_engine: Optional[str]
    _engine_confs: dict[str, _EngineConf]  # Configuration for all the engines.
    _engines_saved_log: deque[str]
    _engines_log_queue: queue.SimpleQueue[str]

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

        ## Retrieve the engine configurations from `self.config`:
        try:
            engine_confs = self.config["engine-configurations"]
        except KeyError:
            raise self.config_error("Section 'engine-configurations' is missing")
        try:
            assert isinstance(engine_confs, dict), "Section 'engine-configurations' must be a dict"
            self.engine_confs = {
                name: _EngineConf(**values) for (name, values) in engine_confs.items()
            }
        except Exception as ex:
            raise self.config_error(repr(ex))

        # No engines are loaded or selected at startup.
        self._loaded_engines = {}
        self._selected_engine = None

        ## Setup logging:
        self._engines_saved_log = deque()
        self._engines_log_queue = queue.SimpleQueue()
        log_handler = logging.handlers.QueueHandler(self._engines_log_queue)
        log_handler.setLevel(logging.WARNING)
        log_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        chess.engine.LOGGER.addHandler(log_handler)

        # Close engines when REPL is quit.
        def close_engines() -> None:
            while self.selected_engine is not None:
                self.close_engine(self.selected_engine)

        self.register_postloop_hook(close_engines)

    @property
    def loaded_engines(self) -> dict[str, chess.engine.SimpleEngine]:
        "Get all the currently loaded engines in a {name: engine} dictionary."
        return self.loaded_engines

    @property
    def selected_engine(self) -> Optional[str]:
        "The currently selected engine. `None` iff `self.loaded_engines()` is empty."
        return self._selected_engine

    def select_engine(self, engine: str) -> None:
        "Select an engine."
        assert engine in self.loaded_engines
        self._selected_engine = engine

    def close_engine(self, engine: str) -> None:
        "Stop and quit an engine."
        self._loaded_engines.pop(engine).quit()
        if self.selected_engine == engine:
            try:
                self.select_engine(next(iter(self.loaded_engines)))
            except StopIteration:
                self._selected_engine = None
        self._selected_engine = None
