from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass
from typing import *

import chess
import chess.engine
import chess.pgn
import cmd2

from .base import Base, InitArgs
from .engine import Engine


@dataclass
class AnalysisInfo:
    "Information about analysis."

    result: chess.engine.SimpleAnalysisResult
    engine: str
    board: chess.Board
    san: Optional[str]


class Analysis(Engine):
    _analyses: set[AnalysisInfo]  # List of all (running or finished) analyses.
    # For a game node, store all analysing engines:
    _analysis_by_node: defaultdict[chess.pgn.GameNode, dict[str, AnalysisInfo]]
    _running_analyses: dict[str, AnalysisInfo]
    _auto_analysis_engines: Set[str]  # All currently auto-analysing engines.
    _auto_analysis_number_of_moves: int  # Number of moves to analyse for auto analysis.

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

        self._analyses = set()
        self._analysis_by_node = defaultdict(dict)
        self._running_analyses = dict()
        self._auto_analysis_engines = set()
        self._auto_analysis_number_of_moves = 5

        # Update auto-analysis after every command.
        def __update_auto_analysis(
            x: cmd2.plugin.PostcommandData,
        ) -> cmd2.plugin.PostcommandData:
            self.update_auto_analysis()
            return x

        self.register_postcmd_hook(__update_auto_analysis)

    @property
    def analyses(self) -> set[AnalysisInfo]:
        "Get a set of all running and not running analyses."
        return self._analyses

    @property
    def running_analyses(self) -> dict[str, AnalysisInfo]:
        "Get all currently running analyses."
        return self._running_analyses

    @property
    def analysis_by_node(self) -> Mapping[chess.pgn.GameNode, Mapping[str, AnalysisInfo]]:
        return self._analysis_by_node.items().mapping

    def start_analysis(
        self,
        engine: str,
        number_of_moves: int,
        limit: Optional[chess.engine.Limit] = None,
    ) -> None:
        if engine in self._running_analyses:
            return
        analysis: AnalysisInfo = AnalysisInfo(
            result=self.loaded_engines[engine].engine.analysis(
                self.game_node.board(),
                limit=limit,
                multipv=number_of_moves,
                game="this",
            ),
            engine=engine,
            board=self.game_node.board(),
            san=(self.game_node.san() if isinstance(self.game_node, chess.pgn.ChildNode) else None),
        )
        self._analyses.add(analysis)
        self._running_analyses[engine] = analysis
        self._analysis_by_node[self.game_node][engine] = analysis

    def stop_analysis(self, engine: str) -> None:
        self._running_analyses[engine].result.stop()
        del self._running_analyses[engine]
        with suppress(KeyError):
            self._auto_analysis_engines.remove(engine)

    @override
    def close_engine(self, name: str) -> None:
        if name in self.running_analyses:
            self.stop_analysis(name)
        super().close_engine(name)

    def update_auto_analysis(self) -> None:
        for engine in self._auto_analysis_engines:
            if (
                engine in self._running_analyses
                and self._running_analyses[engine].board != self.game_node.board()
            ):
                self.stop_analysis(engine)
            self.start_analysis(engine, self._auto_analysis_number_of_moves)

    def start_auto_analysis(self, engine: str, number_of_moves: int) -> None:
        "Start auto analysis on the current position."
        self._auto_analysis_engines.add(engine)
        self._auto_analysis_number_of_moves = number_of_moves
        self.update_auto_analysis()

    def rm_analysis(self, engine: str, node: chess.pgn.GameNode) -> None:
        "Remove an analysis made by a certain engine at a certain node."
        removed: AnalysisInfo = self._analysis_by_node[self.game_node].pop(engine)
        self._analyses.remove(removed)
