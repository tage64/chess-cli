from .base import *

from collections import deque, defaultdict
from dataclasses import dataclass
import logging
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
    _engine_confs: dict[str, _EngineConf]  # Configuration for all the engines.
    _engines_saved_log: deque[str]
    _engines_log_queue: queue.SimpleQueue[str]

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)
