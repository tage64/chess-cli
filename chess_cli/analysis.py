from .base import *
from .engine import Engine

from dataclasses import dataclass
from collections import defaultdict
from typing import *

import chess
import chess.engine


@dataclass
class _AnalysisInfo:
    "Information about analysis."

    result: chess.engine.SimpleAnalysisResult
    engine: str
    board: chess.Board
    san: Optional[str]


class Analysis(Engine):
    _analysis: list[_AnalysisInfo]  # List of all (running or finished) analysis.
    _analysis_by_node: defaultdict[chess.pgn.GameNode, dict[str, _AnalysisInfo]]  # TODO comment
    _running_analysis: dict[str, _AnalysisInfo]
    _auto_analysis_engines: Set[str]  # All currently auto-analysing engines.
    _auto_analysis_number_of_moves: int  # Number of moves to analyse for auto analysis.

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

        self._analysis = []
        self._analysis_by_node = defaultdict(dict)
        self._running_analysis = dict()
        self._auto_analysis_engines = set()
        self._auto_analysis_number_of_moves = 5

        # Update auto-analysis after every command.
        def __update_auto_analysis(
            x: cmd2.plugin.PostcommandData,
        ) -> cmd2.plugin.PostcommandData:
            self.update_auto_analysis()
            return x

        self.register_postcmd_hook(__update_auto_analysis)

    def start_analysis(
        self,
        engine: str,
        number_of_moves: int,
        limit: Optional[chess.engine.Limit] = None,
    ) -> None:
        if engine in self._running_analysis:
            return
        analysis: _AnalysisInfo = _AnalysisInfo(
            result=self.loaded_engines[engine].analysis(
                self.game_node.board(),
                limit=limit,
                multipv=number_of_moves,
                game="this",
            ),
            engine=engine,
            board=self.game_node.board(),
            san=(self.game_node.san() if isinstance(self.game_node, chess.pgn.ChildNode) else None),
        )
        self._analysis.append(analysis)
        self._running_analysis[engine] = analysis
        self._analysis_by_node[self.game_node][engine] = analysis

    def stop_analysis(self, engine: str) -> None:
        self._running_analysis[engine].result.stop()
        del self._running_analysis[engine]

    def update_auto_analysis(self) -> None:
        for engine in self._auto_analysis_engines:
            if (
                engine in self._running_analysis
                and self._running_analysis[engine].board != self.game_node.board()
            ):
                self.stop_analysis(engine)
            self.start_analysis(engine, self._auto_analysis_number_of_moves)
