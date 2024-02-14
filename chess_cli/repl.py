import argparse
import code
import functools
import re
import shlex
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

import more_itertools
from cmd2 import Cmd2ArgumentParser
from prompt_toolkit import PromptSession

type ParsedArgs = argparse.Namespace
type CmdFunc[T] = Callable[[T, str], None]
type ArgparseCmdFunc[T] = Callable[[T, ParsedArgs], None]

CMD_FUNC_PREFIX: str = "do_"
DOC_STRING_REGEX: re.Pattern = re.compile(
    r"(?P<summary>([^\S\n]*\S.*\n?)+)(\s*\Z|\n\s*\n)", re.MULTILINE
)


@dataclass
class Command[T]:
    """A cmd in the REPL."""

    name: str  # The name of the command.
    aliases: list[str]  # A list of aliases for the command.
    func: CmdFunc[T]  # A function to execute for the command.
    summary: str | None  # A short summary for the command.
    long_help: str | None  # A longer description of the command.

    def __call__(self, self_: T, prompt: str) -> None:
        self.func(self_, prompt)


class QuitRepl(Exception):
    """Exception to raise to quit the REPL."""

    pass


class CommandFailure(Exception):
    """An exception to raise when a command fails.

    The message of the exception will be printed on STDERR.
    """

    pass


class ReplBase:
    """Base class for a REPL with no helpful commands like help or quit."""

    # All commands stored in a dict with names/aliases as keys. Note that a command
    # may occur multiple times if it has multiple names/aliases.
    _cmds: dict[str, Command[Self]]
    prompt_session: PromptSession

    def __init__(self) -> None:
        self._cmds = {}
        for name in dir(self):
            if name.startswith(CMD_FUNC_PREFIX):
                cmd = getattr(self, name)
                assert isinstance(cmd, Command), (
                    f"Repl initialization: All attributes starting with {CMD_FUNC_PREFIX} must be"
                    f" an instance of repl.Command. {cmd.__qualname__} is not. Please decorate it"
                    " with repl.command() or repl.argparse_command()."
                )
                self.add_cmd(cmd)
        self.prompt_session = PromptSession(enable_system_prompt=True, enable_open_in_editor=True)

    def add_cmd(self, cmd: Command[Self]) -> None:
        for name in more_itertools.prepend(cmd.name, cmd.aliases):
            assert name not in self._cmds, (
                f"Error when adding cmd {cmd.name}: {name} is already bound to the"
                f" {self._cmds[name].name}-cmd."
            )
            self._cmds[name] = cmd

    def poutput(self, *args, **kwargs) -> None:
        """Print to stdout.

        Excepts the same arguments as the builtin `print` function.
        """
        print(*args, **kwargs)

    def perror(self, *args, **kwargs) -> None:
        """Print to stderr."""
        print(*args, file=sys.stderr, **kwargs)

    def exec_cmd(self, prompt: str) -> None:
        """Parse a prompt and execute the corresponding command.

        This method may be overridden to simulate post and pre command hooks. It may
        also be called to execute an arbitrary command by its prompt.
        """
        prompt = prompt.strip()
        first_space: int = prompt.find(" ")
        if first_space < 0:
            cmd_name: str = prompt
            if not cmd_name:
                return
            rest: str = ""
        else:
            cmd_name = prompt[:first_space]
            rest = prompt[first_space:].strip()
        if cmd_name not in self._cmds:
            self.perror(f"Error: Command not found: {cmd_name}")
            return
        command: Command = self._cmds[cmd_name]
        command(self, rest)

    def prompt_str(self) -> str:
        """Get the string for the prompt, you can override this."""
        return "> "

    def prompt(self) -> None:
        """Issue a prompt and execute the entered command."""
        prompt: str = self.prompt_session.prompt(self.prompt_str())
        self.exec_cmd(prompt)

    def cmd_loop(self) -> None:
        """Run the application in a loop."""
        while True:
            try:
                self.prompt()
            except (QuitRepl, EOFError):
                break
            except CommandFailure as e:
                self.perror(f"Error: {e}")
            except KeyboardInterrupt:
                continue
            except Exception:
                print(traceback.format_exc())


def command[
    T: ReplBase,
](
    name: str | None = None,
    aliases: list[str] | None = None,
    summary: str | None = None,
    long_help: str | None = None,
) -> Callable[[CmdFunc[T]], Command[T]]:
    """A decorator for methods of `Repl` to add them as commands."""

    def decorator(func: CmdFunc[T]) -> Command[T]:
        nonlocal summary
        nonlocal long_help
        if summary is None and func.__doc__:
            summary_match = DOC_STRING_REGEX.match(func.__doc__)
            summary = summary_match.group("summary").strip() if summary_match is not None else None
        if long_help is None:
            long_help = func.__doc__.strip() if func.__doc__ else None
        assert func.__name__.startswith(CMD_FUNC_PREFIX), (
            f"The name of a command function must start with {CMD_FUNC_PREFIX}, which is not the"
            f" case with {func.__name__}."
        )
        name: str = func.__name__[len(CMD_FUNC_PREFIX) :]
        cmd: Command[T] = Command(
            name=name,
            aliases=aliases or [],
            func=func,
            summary=summary,
            long_help=long_help,
        )
        functools.update_wrapper(cmd, func)
        return cmd

    return decorator


def argparse_command[
    T: ReplBase,
](argparser: Cmd2ArgumentParser, aliases: list[str] | None = None) -> Callable[
    [ArgparseCmdFunc[T]], Command[T]
]:
    """Returns a decorator for methods of `Repl` to add them as commands with an
    argparser."""

    def decorator(func: ArgparseCmdFunc[T]) -> Command[T]:
        if not argparser.prog:
            argparser.prog = func.__name__
        if not argparser.description:
            argparser.description = func.__doc__

        @functools.wraps(func)
        def cmd_func(repl: T, prompt: str) -> None:
            args = shlex.split(prompt)
            try:
                parsed_args: ParsedArgs = argparser.parse_args(args)
            except SystemExit:
                return
            func(repl, parsed_args)

        return command(
            aliases=aliases,
            summary=argparser.description if not func.__doc__ else None,
            long_help=argparser.format_help(),
        )(cmd_func)

    return decorator


class Repl(ReplBase):
    """Base class for a REPL with helpful commands like help or quit."""

    @command(aliases=["q", "exit"])
    def do_quit(self, _) -> None:
        """Exit the REPL."""
        raise QuitRepl()

    help_argparser = Cmd2ArgumentParser()
    help_argparser.add_argument("command", nargs="?", help="Get help for this specific command.")

    @argparse_command(help_argparser, aliases=["h"])
    def do_help(self, args: ParsedArgs) -> None:
        """Get a list of all possible commands or get help for a specific command."""
        if args.command is not None:
            if args.command not in self._cmds:
                raise CommandFailure(f"The command {args.command} does not exist")
            print(self._cmds[args.command].long_help)
        else:
            # Deduplicate commands by their id, that is if they are the same object.
            unique_commands: dict[int, Command] = {id(c): c for c in self._cmds.values()}
            for command in unique_commands.values():
                line: str = f"{command.name}"
                if command.aliases:
                    for alias in command.aliases:
                        line += f", {alias}"
                line += f" -- {command.summary}"
                self.poutput(line)

    @command()
    def do_py(self, expr: str) -> None:
        """Evaluate a Python expression or start an interactive Python shell.

        If this command is called without arguments, an interactive Python shell will be started.
        In that shell, `self` will refer to the current `Repl` instance.

        Otherwise, the rest of the command line will be parsed and executed as a Python expression
        with the `eval()` function.
        """
        if expr:
            print(eval(expr))
        else:
            local_vars = {"self": self}
            code.interact(local=local_vars)
