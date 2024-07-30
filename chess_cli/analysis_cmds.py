import itertools
from argparse import ArgumentParser
from collections.abc import Iterable

import chess
import chess.engine
import chess.pgn

from .analysis import Analysis, AnalysisInfo
from .repl import argparse_command
from .utils import MoveNumber, score_str


class AnalysisCmds(Analysis):
    """Basic commands related to analysis."""

    analysis_argparser = ArgumentParser()
    analysis_subcmds = analysis_argparser.add_subparsers(dest="subcmd")
    analysis_start_argparser = analysis_subcmds.add_parser(
        "start", aliases=["s"], help="Start to analyse with the selected engine."
    )
    analysis_start_argparser.add_argument(
        "-f",
        "--fixed",
        action="store_true",
        help=(
            "Fix the analysis to the current move. If not given, the analysis will be stopped and"
            " restarted as the current position changes."
        ),
    )
    analysis_start_argparser.add_argument(
        "-n", "--number-of-moves", type=int, default=5, help="Show the n best moves."
    )
    analysis_start_argparser.add_argument(
        "--time", type=float, help="Analyse only the given number of seconds."
    )
    analysis_start_argparser.add_argument(
        "--depth", type=int, help="Analyse until the given depth is reached."
    )
    analysis_start_argparser.add_argument(
        "--nodes", type=int, help="Search only the given number of nodes."
    )
    analysis_start_argparser.add_argument(
        "--mate", type=int, help="Search for a mate in the given number of moves and stop then."
    )
    analysis_stop_argparser = analysis_subcmds.add_parser("stop", help="Stop analysing.")
    analysis_stop_argparser.add_argument(
        "-a", "--all", action="store_true", help="Stop all engines."
    )
    analysis_ls_argparser = analysis_subcmds.add_parser("ls", help="List analysis.")
    analysis_ls_argparser.add_argument(
        "-v", "--verbose", action="store_true", help="Print out more info."
    )
    analysis_ls_argparser.add_argument(
        "-l",
        "--lines",
        type=int,
        nargs="?",
        help="Maximum number of lines to show for each analysis.",
    )
    analysis_ls_group = analysis_ls_argparser.add_mutually_exclusive_group()
    analysis_ls_group.add_argument(
        "-r", "--running", action="store_true", help="List only running analysis."
    )
    analysis_ls_group.add_argument(
        "-s", "--stopped", action="store_true", help="List only stopped analysis."
    )
    analysis_show_argparser = analysis_subcmds.add_parser(
        "show", aliases=["sh"], help="Show all analysis performed at the current move."
    )
    analysis_show_argparser.add_argument(
        "lines", type=int, nargs="?", help="Maximum number of lines to show."
    )
    analysis_rm_argparser = analysis_subcmds.add_parser(
        "rm",
        aliases=["remove"],
        help=(
            "Remove analysis made by the selected engine at this move. Useful if you want to rerun"
            " the analysis."
        ),
    )
    analysis_rm_argparser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Remove all analysis made by all engines at this move.",
    )
    analysis_rm_argparser.add_argument(
        "engine",
        nargs="?",
        help=(
            "Remove analysis made by this engine at this move. Defaults to the currently selected"
            " engine."
        ),
    )

    @argparse_command(analysis_argparser, alias="a")
    async def do_analysis(self, args) -> None:
        """Manage analysis."""
        match args.subcmd:
            case "ls":
                self.analysis_ls(args)
            case "show" | "sh":
                self.analysis_show(args.lines)
            case None:
                self.analysis_show()
            case "start" | "s":
                await self.analysis_start(args)
            case "stop":
                self.analysis_stop(args)
            case "rm" | "remove":
                self.analysis_rm(args)
            case _:
                raise AssertionError("Invalid subcommand.")

    async def analysis_start(self, args) -> None:
        engine: str = self.get_selected_engine().loaded_name
        if engine in self.analysis_by_node[self.game_node]:
            answer: bool = await self.yes_no_dialog(
                f"Error: There's allready an analysis made by {engine} at this move.\n"
                "Do you want to remove it and restart the analysis?"
            )
            if answer:
                await self.exec_cmd("analysis rm")
            else:
                return
        if engine in self.running_analyses:
            self.poutput(
                f"Error: {engine} is already running an analysis, stop it with `analysis stop`"
                " before you can restart it."
            )
            return
        limit = chess.engine.Limit(
            time=args.time, depth=args.depth, nodes=args.nodes, mate=args.mate
        )
        if args.fixed:
            await self.start_analysis(engine, args.number_of_moves, limit)
        else:
            await self.start_auto_analysis(engine, args.number_of_moves)
        self.poutput(f"{engine} is now analysing.")

    def analysis_stop(self, args) -> None:
        if args.all:
            engines: Iterable[str] = self.running_analyses.keys()
        else:
            engine: str = self.get_selected_engine().loaded_name
            if engine not in self.running_analyses:
                self.poutput("Error: {engine} is not running any analysis.")
                return
            engines = [engine]
        for engine in engines:
            if engine not in self.running_analyses:
                continue
            self.stop_analysis(engine)
            self.poutput(f"Successfully stopped {engine}")

    def show_analysis(
        self, analysis: AnalysisInfo, verbose: bool = False, max_lines: int | None = None
    ) -> None:
        show_str: str = analysis.engine + " @ "
        if analysis.san is not None:
            show_str += f"{MoveNumber.last(analysis.board)} {analysis.san}: "
        else:
            show_str += "starting position: "

        def score_and_wdl_str(info: chess.engine.InfoDict) -> str:
            res: str = ""
            if "pv" in info:
                res += f"{analysis.board.san(info["pv"][0])}: "
            if "score" in info:
                score: chess.engine.Score = info["score"].relative
                res += score_str(score) + ", "
            if "wdl" in info:
                wdl: chess.engine.Wdl = info["wdl"].relative
                res += f"{round(wdl.expectation() * 100)} %, "
                res += f"{round(wdl.wins * 100 / wdl.total())}% win, "
                res += f"{round(wdl.draws * 100 / wdl.total())}% draw, "
                res += f"{round(wdl.losses * 100 / wdl.total())}% loss, "
            return res

        if not verbose:
            show_str += score_and_wdl_str(analysis.result.info)
            if analysis.engine in self.running_analyses:
                show_str += "(running)"
            else:
                show_str += "(stopped)"
        else:
            if analysis.engine in self.running_analyses:
                show_str += "(running), "
            else:
                show_str += "(stopped), "
            if "string" in analysis.result.info:
                show_str += analysis.result.info["string"] + "\n    "
            for key, val in analysis.result.info.items():
                if key not in [
                    "score",
                    "pv",
                    "multipv",
                    "currmove",
                    "currmovenumber",
                    "wdl",
                    "string",
                ]:
                    show_str += f"{key}: {val}, "
            for i, info in enumerate(itertools.islice(analysis.result.multipv, max_lines), 1):
                show_str += f"\n  {i}: {score_and_wdl_str(info)}"
                if "pv" in info and len(info["pv"]) >= 2:
                    show_str += f"\n    {analysis.board.variation_san(info["pv"])}"
        self.poutput(show_str)

    def analysis_ls(self, args) -> None:
        for analysis in self.analyses:
            if args.running and not (
                analysis.engine in self.running_analyses
                and analysis == self.running_analyses[analysis.engine]
            ):
                continue
            if args.stopped and (
                analysis.engine in self.running_analyses
                and analysis == self.running_analyses[analysis.engine]
            ):
                continue
            self.show_analysis(analysis, verbose=args.verbose, max_lines=args.lines)

    def analysis_show(self, lines: int | None = None) -> None:
        if not self.analysis_by_node[self.game_node]:
            self.poutput("No analysis at this move.")
            return
        for engine, analysis in self.analysis_by_node[self.game_node].items():
            self.poutput(f"({engine}): ", end="")
            self.show_analysis(analysis, verbose=True, max_lines=lines)

    def analysis_rm(self, args) -> None:
        if args.all:
            engines: Iterable[str] = self.analysis_by_node[self.game_node].keys()
        else:
            if args.engine:
                engine: str = args.engine
            else:
                engine = self.get_selected_engine().loaded_name
            if engine not in self.analysis_by_node[self.game_node]:
                self.poutput(f"Error: There is no analysis made by {engine} at this move.")
                return
            engines = [engine]
        for engine in engines:
            if engine in self.running_analyses:
                self.stop_analysis(engine)
            self.rm_analysis(engine, self.game_node)
            self.poutput(f"Removed analysis made by {engine}.")
