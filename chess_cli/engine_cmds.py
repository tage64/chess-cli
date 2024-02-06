import os
import platform
import re
import shutil
import urllib.request
from typing import Any, Iterable, Union, Optional, Mapping

import appdirs
import chess
import chess.engine
import chess.pgn
import cmd2
import psutil

from .base import CommandFailure
from .engine import Engine, EngineConf, EngineProtocol
from .utils import sizeof_fmt


class EngineCmds(Engine):
    "Basic commands related to chess engines."
    engine_argparser = cmd2.Cmd2ArgumentParser()
    engine_subcmds = engine_argparser.add_subparsers(dest="subcmd")
    engine_ls_argparser = engine_subcmds.add_parser("ls", help="List loaded chess engines.")
    engine_ls_argparser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Display more information about the engines.",
    )
    engine_ls_argparser.add_argument(
        "-l", "--loaded", action="store_true", help="List only loaded engines."
    )
    engine_load_argparser = engine_subcmds.add_parser("load", help="Load a chess engine.")
    engine_load_argparser.add_argument(
        "name",
        help="Name of the engine. List availlable engines with the command `engine ls`",
    )
    engine_load_argparser.add_argument(
        "--as",
        dest="load_as",
        help=(
            "Load the engine with a different name. Useful if you want to have multiple instances"
            " of an engine running at the same time."
        ),
    )
    engine_import_argparser = engine_subcmds.add_parser("import", help="Import a chess engine.")
    engine_import_argparser.add_argument("path", help="Path to engine executable.")
    engine_import_argparser.add_argument(
        "name", help="A short name for the engine, (PRO TIP: avoid spaces in the name)."
    )
    engine_import_argparser.add_argument(
        "-p",
        "--protocol",
        type=EngineProtocol,
        default=EngineProtocol.UCI,
        choices=[p.value for p in EngineProtocol],
        help="Type of engine protocol.",
    )
    engine_rm_argparser = engine_subcmds.add_parser(
        "rm", aliases=["remove"], help="Remove an engine."
    )
    engine_rm_argparser.add_argument("engine", help="Name of engine to remove.")
    engine_install_argparser = engine_subcmds.add_parser(
        "install", help="Automaticly download and import some common engines."
    )
    engine_install_argparser.add_argument(
        "engine", choices=["stockfish", "lc0"], help="Which engine to install."
    )
    engine_quit_argparser = engine_subcmds.add_parser("quit", help="Quit all selected engines.")
    engine_select_argparser = engine_subcmds.add_parser(
        "select",
        help=(
            "Select a loaded engine. The selected engine will be used for commands like `analysis"
            " start` or `engine config`."
        ),
    )
    engine_select_argparser.add_argument("engine", help="Engine to select.")
    engine_config_argparser = engine_subcmds.add_parser(
        "config",
        aliases=["conf", "configure"],
        help="Set values for or get current values of different engine specific parameters.",
    )
    engine_config_subcmds = engine_config_argparser.add_subparsers(dest="config_subcmd")
    engine_config_get_argparser = engine_config_subcmds.add_parser(
        "get", help="Get the value of an option for the selected engine."
    )
    engine_config_get_argparser.add_argument("name", help="Name of the option.")
    engine_config_ls_argparser = engine_config_subcmds.add_parser(
        "ls",
        aliases=["list"],
        help="List availlable options and their current values for the selected engine.",
    )
    engine_config_ls_argparser.add_argument(
        "-r",
        "--regex",
        help="Filter option names by a case insensitive regular expression.",
    )
    engine_config_ls_argparser.add_argument(
        "-t",
        "--type",
        choices=["checkbox", "comboboxinteger", "text", "button"],
        nargs="+",
        help="Filter options by the given type.",
    )
    engine_config_ls_configured_group = engine_config_ls_argparser.add_mutually_exclusive_group()
    engine_config_ls_configured_group.add_argument(
        "-c",
        "--configured",
        action="store_true",
        help="Only list options that are already configured in some way.",
    )
    engine_config_ls_configured_group.add_argument(
        "-n",
        "--not-configured",
        action="store_true",
        help="Only list options that are not configured.",
    )
    engine_config_ls_argparser.add_argument(
        "--include-auto",
        "--include-automatically-managed",
        action="store_true",
        help=(
            "By default, some options like MultiPV or Ponder are managed automatically. There is no"
            " reason to change them so they are hidden by default. This option makes them vissable."
        ),
    )
    engine_config_set_argparser = engine_config_subcmds.add_parser(
        "set", help="Set a value of an option for the selected engine."
    )
    engine_config_set_argparser.add_argument("name", help="Name of the option to set.")
    engine_config_set_argparser.add_argument(
        "value",
        help=(
            "The new value. Use true/check or false/uncheck to set a checkbox. Buttons can only be"
            " set to 'trigger-on-startup', but note that you must use the `engine config trigger`"
            " command to trigger it right now."
        ),
    )
    engine_config_set_argparser.add_argument(
        "-t",
        "--temporary",
        action="store_true",
        help=(
            "Set the value in the running engine but don't store it in the engine's configuration."
        ),
    )
    engine_config_unset_argparser = engine_config_subcmds.add_parser(
        "unset",
        help="Change an option back to its default value and remove it from the configuration.",
    )
    engine_config_unset_argparser.add_argument("name", help="Name of the option to unset.")
    engine_config_unset_argparser.add_argument(
        "-t",
        "--temporary",
        action="store_true",
        help="Unset the value in the running engine but keep it in the engine's configuration.",
    )
    engine_config_trigger_argparser = engine_config_subcmds.add_parser(
        "trigger", help="Trigger an option of type button."
    )
    engine_config_trigger_argparser.add_argument("name", help="Name of the option to trigger.")
    engine_log_argparser = engine_subcmds.add_parser(
        "log", help="Show the logged things (like stderr) from the selected engine."
    )
    engine_log_subcmds = engine_log_argparser.add_subparsers(dest="log_subcmd")
    engine_log_subcmds.add_parser("clear", help="Clear the log.")
    engine_log_subcmds.add_parser("show", help="Show the log.")

    @cmd2.with_argparser(engine_argparser)  # type: ignore
    def do_engine(self, args: Any) -> None:
        "Everything related to chess engines. See subcommands for detailes"
        match args.subcmd:
            case "ls":
                self.engine_ls(args)
            case "import":
                self.engine_import(args)
            case "load":
                self.engine_load(args)
            case "rm" | "remove":
                self.engine_rm(args)
            case "install":
                self.engine_install(args)
            case "select":
                self.engine_select(args)
            case "log":
                self.engine_log(args)
            case "conf" | "config" | "configure":
                self.engine_config(args)
            case "quit":
                self.engine_quit(args)
            case _:
                assert False, "Unsupported subcommand."

    def engine_select(self, args) -> None:
        if args.engine not in self.loaded_engines:
            if args.engine in self.engine_confs:
                self.poutput(
                    f"Error: {args.engine} is not loaded. You can try to load it by running `engine"
                    f" load {args.engine}`."
                )
            else:
                self.poutput(
                    f"Error: There is no engine named {args.engine}. You can list all availlable"
                    " engines with `engine ls -a`, import an engine with the `engine import`"
                    " command, or install an engine with `engine install ...`."
                )
            return
        self.select_engine(args.engine)

    def engine_ls(self, args) -> None:
        if args.loaded:
            engines: Iterable[str] = self.loaded_engines.keys()
        else:
            engines = self.engine_confs.keys()
        for engine in engines:
            self.show_engine(engine, verbose=args.verbose)

    def engine_load(self, args) -> None:
        try:
            if args.name not in self.engine_confs:
                self.poutput(
                    f"Error: There is no engine named {args.name}. Consider importing one with"
                    " `engine import`."
                )
                return
            name: str = args.load_as or args.name
            if name in self.engine_confs:
                self.poutput(f"Error: There is already an engine named {name}.")
                return
            if name in self.loaded_engines:
                self.poutput(
                    f"Error: An engine named {name} is already loaded. If you want to run multiple"
                    " instances of a given engine, consider to load it as another name like"
                    " `engine load <name> --as <name2>`"
                )
                return
            self.load_engine(args.name, name)
            self.select_engine(name)
            self.show_engine(name, verbose=True)
            self.poutput(f"Successfully loaded and selected {name}.")
        except OSError:
            self.poutput(
                "Perhaps the executable has been moved or deleted, or you might be in a different"
                " folder now than when you configured the engine."
            )
            self.poutput(
                "You should probably locate the engine's executable (something like stockfish.exe)"
                " and update the engine configuration with the `engine config` command if"
                " necessary."
            )
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            self.poutput(f"Loading of {args.name} failed.")

    def engine_import(self, args) -> None:
        if args.name in self.engine_confs:
            self.poutput(
                f"Error: The name {args.name} is already in use, please pick another name or"
                " consider removing or updating the existing configuration with the `engine"
                " config` command."
            )
            return
        self.add_engine(args.path, args.protocol, args.name)
        try:
            self.load_engine(args.name, args.name)
            self.poutput(f"Successfully imported, loaded and selected {args.name}.")
        except (OSError, chess.engine.EngineError, chess.engine.EngineTerminatedError):
            self.rm_engine(args.name)
            self.poutput(f"Importing of the engine {args.path} failed.")

    def engine_rm(self, args) -> None:
        if args.engine not in self.engine_confs:
            self.poutput(
                f"Error: There is no engine named {args.engine}, list all engines with `engine ls`."
            )
            return
        if self.engine_confs[args.engine].loaded_as:
            self.poutput(f"Error: {args.engine} is loaded, please quit it before removing it.")
            return
        self.rm_engine(args.engine)
        self.poutput(f"Successfully removed {args.engine}")

    def engine_install(self, args) -> None:
        match args.engine:
            case "stockfish":
                self.install_stockfish()
            case "lc0":
                self.poutput(
                    "The installation is not supported yet. Please talk to the authors of this"
                    " application to get it implemented :)"
                )
            case _:
                assert False, "Invalid argument"

    def install_stockfish(self) -> None:
        dir: str = os.path.join(appdirs.user_data_dir("chess-cli"), "stockfish")
        os.makedirs(dir, exist_ok=True)
        match platform.system():
            case "Linux":
                url: str = (
                    "https://github.com/official-stockfish/Stockfish/releases/download/sf_16/stockfish-ubuntu-x86-64-avx2.tar"
                )
                archive_format: str = "tar"
                executable: str = "stockfish/stockfish-ubuntu-x86-64-avx2"
            case "Windows":
                url = "https://github.com/official-stockfish/Stockfish/releases/download/sf_16/stockfish-windows-x86-64-avx2.zip"
                archive_format = "zip"
                executable = "stockfish/stockfish-windows-x86-64-avx2.exe"
            case x:
                self.poutput(f"Error: Unsupported platform: {x}")
                return
        self.poutput("Downloading Stockfish...")
        engine_archive, _ = urllib.request.urlretrieve(url)
        self.poutput("Download complete. Unpacking...")
        shutil.unpack_archive(engine_archive, dir, archive_format)
        urllib.request.urlcleanup()
        if "stockfish" in self.engine_confs:
            self.poutput("Removing old stockfish")
            self.onecmd("engine rm stockfish")
        executable_path: str = os.path.join(dir, executable)
        self.onecmd(f'engine import "{executable_path}" stockfish')
        ncores: int = psutil.cpu_count()
        ncores_use: int = ncores - 1 if ncores > 1 else 1
        self.poutput(
            f"You seem to have {ncores} logical cores on your system. So the engine will use"
            f" {ncores_use} of them."
        )
        self.onecmd(f"engine config set threads {ncores_use}")
        ram: int = psutil.virtual_memory().total
        ram_use_MiB: int = int(0.75 * ram / 2**20)
        ram_use: int = ram_use_MiB * 2**20
        self.poutput(
            f"You seem to have a RAM of {sizeof_fmt(ram)} bytes, so stockfish will be configured to"
            f" use {sizeof_fmt(ram_use)} bytes (75 %) thereof for the hash."
        )
        self.onecmd(f"engine config set hash {ram_use_MiB}")
        self.poutput("You can change these settings and more with the engine config command.")

    def engine_quit(self, _args) -> None:
        if self.selected_engine is None:
            self.poutput("Error: No engine to quit.")
        else:
            self.close_engine(self.selected_engine)
            self.poutput(f"Quitted {self.selected_engine} without any problems.")

    def show_engine_option(self, engine: str, name: str) -> None:
        opt: chess.engine.Option = self.loaded_engines[engine].engine.options[name]
        configured_val: Optional[Union[str, int, bool]] = self.engine_confs[engine].options.get(
            name
        )
        val: Optional[Union[str, int, bool]] = configured_val or opt.default

        show_str: str = name
        if val is not None:
            if opt.type == "checkbox":
                if val:
                    show_str += " [X]"
                else:
                    show_str += " [ ]"
            else:
                show_str += " = " + repr(val)
        if opt.type == "button":
            show_str += ": (button)"
        else:
            if configured_val is not None and opt.default is not None:
                show_str += f": Default: {repr(opt.default)}, "
            else:
                show_str += " (default): "
            if opt.var:
                show_str += f"Alternatives: {repr(opt.var)}, "
            if opt.min is not None:
                show_str += f"Min: {repr(opt.min)}, "
            if opt.max is not None:
                show_str += f"Max: {repr(opt.max)}, "
            show_str += "Type: "
            if opt.type == "check":
                show_str += "checkbox"
            elif opt.type == "combo":
                show_str += "combobox"
            elif opt.type == "spin":
                show_str += "integer"
            elif opt.type == "string":
                show_str += "text"
            elif opt.type == "file":
                show_str += "text (file path)"
            elif opt.type == "path":
                show_str += "text (directory path)"
            elif opt.type == "reset":
                show_str += "button (reset)"
            elif opt.type == "save":
                show_str += "button (save)"
            else:
                assert False, f"Unsupported option type: {opt.type}."

        if configured_val is not None:
            show_str += ", (Configured)"
        if opt.is_managed():
            show_str += ", (Managed automatically)"

        self.poutput(show_str)

    def engine_config(self, args) -> None:
        if not self.selected_engine:
            self.poutput("Error: No engine is loaded.")
            return
        match args.config_subcmd:
            case "ls":
                self.engine_config_ls(args)
            case "get":
                self.engine_config_get(args)
            case "set":
                self.engine_config_set(args)
            case "unset":
                self.engine_config_unset(args)
            case "trigger":
                self.engine_config_trigger(args)
            case _:
                assert False, "Invalid subcommand."

    def get_engine_opt_name(self, engine: str, name: str) -> str:
        "Case insensitively search for a name of an option on an engine. Raises CommandFailure if not found."
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].engine.options
        if name in options:
            return name
        try:
            return next((name for name in options.keys() if name.lower() == name.lower()))
        except StopIteration:
            self.poutput(
                f"Error: No option named {name} in the engine {engine}. List all availlable options"
                " with `engine config ls`."
            )
            raise CommandFailure()

    def get_selected_engine(self) -> str:
        "Get the selected engine or raise CommandFailure."
        if self.selected_engine is None:
            self.poutput("Error: No engine is selected.")
            raise CommandFailure()
        return self.selected_engine

    def engine_config_get(self, args) -> None:
        engine: str = self.get_selected_engine()
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        self.show_engine_option(engine, opt_name)

    def engine_config_ls(self, args) -> None:
        engine: str = self.get_selected_engine()
        conf: EngineConf = self.engine_confs[engine]
        for name, opt in self.loaded_engines[engine].engine.options.items():
            if (args.configured and name not in conf.options) or (
                args.not_configured and name in conf.options
            ):
                continue
            if opt.is_managed() and not args.include_auto and name not in conf.options:
                continue
            if args.regex:
                try:
                    pattern: re.Pattern = re.compile(args.regex, flags=re.IGNORECASE)
                except re.error as e:
                    self.poutput(f'Error: Invalid regular expression "{args.regex}": {e}')
                    return
                if not pattern.fullmatch(name):
                    continue
            if args.type and (
                (opt.type == "check" and "checkbox" not in args.type)
                or opt.type == "combo"
                and "combobox" not in args.type
                or opt.type == "spin"
                and "integer" not in args.type
                or opt.type in ["button", "reset", "save"]
                and "button" not in args.type
                or opt.type in ["string", "file", "path"]
                and "string" not in args.type
            ):
                continue
            self.show_engine_option(engine, name)

    def engine_config_set(self, args) -> None:
        engine: str = self.get_selected_engine()
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].engine.options
        conf: EngineConf = self.engine_confs[engine]
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        option: chess.engine.Option = options[opt_name]
        if option.type in ["string", "combo", "file", "path"]:
            value: Union[str, int, bool, None] = args.value
        elif option.type == "spin":
            try:
                value = int(args.value)
            except ValueError:
                self.poutput(
                    f"Invalid integer: {args.value}. Note: This option expects an integer and"
                    " nothing else."
                )
                return
        elif option.type == "check":
            if args.value.lower() in ["true", "check"]:
                value = True
            elif args.value in ["false", "uncheck"]:
                value = False
            else:
                self.poutput(
                    f"{option.name} is a checkbox and can only be set to true/check or"
                    f" false/uncheck, but you set it to {args.value}. Please go ahead and correct"
                    " your mistake."
                )
                return
        elif option.type in ["button", "reset", "save"]:
            if not args.value.lower() == "trigger-on-startup":
                self.poutput(
                    f"{option.name} is a button and buttons can only be configured to"
                    " 'trigger-on-startup', (which means what it sounds like). If you want to"
                    f" trigger {option.name}, please go ahead and run `engine config trigger"
                    f" {option.name}` instead. Or you might just have made a typo when you entered"
                    " this command, if so, go ahead and run `engine config set"
                    f" {option.name} trigger-on-startup`."
                )
                return
            if not args.temporary:
                conf.options[option.name] = None
            return
        else:
            assert False, f"Unsupported option type: {option.type}"
        if not args.temporary:
            conf.options[option.name] = value
            self.save_config()
        self.set_engine_option(engine, option.name, value)

    def engine_config_unset(self, args) -> None:
        engine: str = self.get_selected_engine()
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].engine.options
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        default = options[opt_name].default
        if default is None:
            if args.temporary:
                self.poutput(
                    f"Error: {opt_name} has no default value and wasn't changed. Try to set it to a"
                    f" custom value with `engine config set --temporary {args.name} <value>`."
                )
                return
            self.poutput(
                f"Warning: {opt_name} has no default value so it's unchanged in the running engine."
            )
        else:
            self.loaded_engines[engine].engine.configure({opt_name: default})
            self.poutput(f"Successfully changed {opt_name} back to its default value: {default}.")

        if not args.temporary:
            conf: EngineConf = self.engine_confs[engine]
            conf.options.pop(opt_name, None)
            self.save_config()

    def engine_config_trigger(self, args) -> None:
        engine: str = self.get_selected_engine()
        options: Mapping[str, chess.engine.Option] = self.loaded_engines[engine].engine.options
        opt_name: str = self.get_engine_opt_name(engine, args.name)
        if options[opt_name].type not in ["button", "reset", "save"]:
            self.poutput(f"Error: {opt_name} is not a button.")
            return
        self.loaded_engines[engine].engine.configure({opt_name: None})

    def engine_log(self, args) -> None:
        match args.log_subcmd:
            case "clear":
                self.clear_engines_log()
            case "show":
                for line in self.get_engines_log():
                    self.poutput(line)
            case _:
                assert False, "Unrecognized command."
