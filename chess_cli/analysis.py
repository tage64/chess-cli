from collections import defaultdict
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, override

import chess
import chess.engine
import chess.pgn

from .base import InitArgs
from .engine import Engine, LoadedEngine


@dataclass
class AnalysisInfo:
    """Information about analysis."""

    result: chess.engine.AnalysisResult
    engine: str
    board: chess.Board
    san: str | None

    def __hash__(self) -> int:
        return id(self)


class Analysis(Engine):
    _analyses: set[AnalysisInfo]  # List of all (running or finished) analyses.
    # For a game node, store all analysing engines:
    _analysis_by_node: defaultdict[chess.pgn.GameNode, dict[str, AnalysisInfo]]
    _running_analyses: dict[str, AnalysisInfo]
    _auto_analysis_engines: set[str]  # All currently auto-analysing engines.
    _auto_analysis_number_of_moves: int  # Number of moves to analyse for auto analysis.
    _auto_analysis_limit: chess.engine.Limit | None = None
    # The perspective of the evaluation score.
    eval_score_perspective: Literal["relative"] | chess.Color = "relative"

    def __init__(self, args: InitArgs) -> None:
        super().__init__(args)

        self._analyses = set()
        self._analysis_by_node = defaultdict(dict)
        self._running_analyses = dict()
        self._auto_analysis_engines = set()
        self._auto_analysis_number_of_moves = 5

    # Update auto analysis for every new prompt.
    @override
    async def pre_prompt(self) -> None:
        await super().pre_prompt()
        await self.update_auto_analysis()

    @override
    def load_config(self) -> None:
        super().load_config()
        analysis_conf = self.config["analysis"]
        assert isinstance(analysis_conf, dict), "Section 'analysis' must be a dict"
        if (perspective := analysis_conf.get("eval-score-perspective")) is not None:
            match perspective:
                case "white":
                    self.eval_score_perspective = chess.WHITE
                case "black":
                    self.eval_score_perspective = chess.BLACK
                case "relative":
                    self.eval_score_perspective = perspective
                case _:
                    raise AssertionError(
                        "eval-score-perspective in section analysis must be "
                        '"white", "black" or "relative"'
                    )

    @override
    def save_config(self) -> None:
        perspective: str
        match self.eval_score_perspective:
            case chess.WHITE:
                perspective = "white"
            case chess.BLACK:
                perspective = "black"
            case "relative" as r:
                perspective = r
            case x:
                raise AssertionError(x)
        self.config["analysis"] = {"eval-score-perspective": perspective}
        super().save_config()

    @property
    def analyses(self) -> set[AnalysisInfo]:
        """Get a set of all running and not running analyses."""
        return self._analyses

    @property
    def running_analyses(self) -> dict[str, AnalysisInfo]:
        """Get all currently running analyses."""
        return self._running_analyses

    @property
    def analysis_by_node(self) -> Mapping[chess.pgn.GameNode, Mapping[str, AnalysisInfo]]:
        return self._analysis_by_node.items().mapping

    async def start_analysis(
        self, engine: str, number_of_moves: int, limit: chess.engine.Limit | None = None
    ) -> None:
        if engine in self._running_analyses:
            return
        async with self.engine_timeout(engine, long=True):
            analysis: AnalysisInfo = AnalysisInfo(
                result=await self.loaded_engines[engine].engine.analysis(
                    self.game_node.board(), limit=limit, multipv=number_of_moves, game="this"
                ),
                engine=engine,
                board=self.game_node.board(),
                san=(
                    self.game_node.san()
                    if isinstance(self.game_node, chess.pgn.ChildNode)
                    else None
                ),
            )
        self._analyses.add(analysis)
        self._running_analyses[engine] = analysis
        self._analysis_by_node[self.game_node][engine] = analysis

    def stop_analysis(self, engine: str, remove_auto: bool = True) -> None:
        with suppress(KeyError):
            self._running_analyses[engine].result.stop()
            del self._running_analyses[engine]
        if remove_auto:
            with suppress(KeyError):
                self._auto_analysis_engines.remove(engine)

    @override
    async def close_engine(self, engine: LoadedEngine) -> None:
        self.stop_analysis(engine.loaded_name)
        await super().close_engine(engine)

    async def update_auto_analysis(self) -> None:
        for engine in self._auto_analysis_engines:
            if (
                engine in self._running_analyses
                and self._running_analyses[engine].board != self.game_node.board()
            ):
                self.stop_analysis(engine, remove_auto=False)
            await self.start_analysis(
                engine, self._auto_analysis_number_of_moves, self._auto_analysis_limit
            )

    async def start_auto_analysis(
        self, engine: str, number_of_moves: int, limit: chess.engine.Limit | None = None
    ) -> None:
        """Start auto analysis on the current position."""
        self._auto_analysis_engines.add(engine)
        self._auto_analysis_number_of_moves = number_of_moves
        self._auto_analysis_limit = limit
        await self.update_auto_analysis()

    def rm_analysis(self, engine: str, node: chess.pgn.GameNode) -> None:
        """Remove an analysis made by a certain engine at a certain node."""
        removed: AnalysisInfo = self._analysis_by_node[self.game_node].pop(engine)
        self._analyses.remove(removed)
