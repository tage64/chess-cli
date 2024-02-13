import argparse
import functools
import re
import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Self

import more_itertools
from cmd2 import Cmd2ArgumentParser
from prompt_toolkit import PromptSession

type ParsedArgs = argparse.Namespace
type CmdFunc[T] = Callable[[T, list[str]], None]
type ArgparseCmdFunc[T] = Callable[[T, ParsedArgs], None]

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

    def __call__(self, self_: T, args: list[str]) -> None:
        self.func(self_, args)


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
    _cmds: ClassVar[dict[str, Command[Self]]] = {}
    prompt_session: PromptSession

    @classmethod
    def add_cmd(cls, cmd: Command[Self]) -> None:
        for name in more_itertools.prepend(cmd.name, cmd.aliases):
            assert name not in cls._cmds, (
                f"Error when adding cmd {cmd.name}: {name} is already bound to the"
                f" {cls._cmds[name].name}-cmd."
            )
            cls._cmds[name] = cmd

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        for x in cls.__dict__.values():
            if isinstance(x, Command):
                cls.add_cmd(x)

    def __init__(self):
        self.prompt_session = PromptSession(enable_system_prompt=True, enable_open_in_editor=True)

    def poutput(self, *args, **kwargs) -> None:
        """Print to stdout.

        Excepts the same arguments as the builtin `print` function.
        """
        print(*args, **kwargs)

    def perror(self, *args, **kwargs) -> None:
        """Print to stderr."""
        print(*args, file=sys.stderr, **kwargs)

    def exec_cmd(self, prompt: str) -> None:
        args: list[str] = shlex.split(prompt)
        if len(args) == 0:
            return
        cmd_name: str = args[0]
        if cmd_name not in self._cmds:
            self.perror(f"Error: Command not found: {cmd_name}")
            return
        command: Command = self._cmds[cmd_name]
        command(self, args[1:])

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
        if summary is None:
            summary_match = DOC_STRING_REGEX.match(func.__doc__)
            summary = summary_match.group("summary").strip() if summary_match is not None else None
        if long_help is None:
            long_help = func.__doc__.strip() if func.__doc__ else None
        cmd: Command[T] = Command(
            name=func.__name__,
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
        def cmd_func(repl: T, args: list[str]) -> None:
            try:
                parsed_args: ParsedArgs = argparser.parse_args(args)
            except SystemExit:
                return
            func(repl, parsed_args)

        return command(
            aliases=aliases, summary=argparser.description, long_help=argparser.format_help()
        )(cmd_func)

    return decorator


class Repl(ReplBase):
    """Base class for a REPL with helpful commands like help or quit."""

    @command(aliases=["exit"])
    def quit(self, _) -> None:
        """Exit the REPL."""
        raise QuitRepl()

    help_argparser = Cmd2ArgumentParser()
    help_argparser.add_argument("command", nargs="?", help="Get help for this specific command.")

    @argparse_command(help_argparser, aliases=["h"])
    def help(self, args: ParsedArgs) -> None:
        """Get a list of all possible commands or get help for a specific command."""
        if args.command is not None:
            if args.command not in self._cmds:
                raise CommandFailure(f"The command {args.command} does not exist")
            print(self._cmds[args.command].long_help)
        else:
            # Deduplicate commands by their id, that is if they are the same object.
            unique_commands: dict[int, Command] = {id(c):c for c in self._cmds.values()}
            for command in unique_commands.values():
                line: str = f"{command.name}"
                if command.aliases:
                    for alias in command.aliases:
                        line += f", {alias}"
                line += f" -- {command.summary}"
                self.poutput(line)
