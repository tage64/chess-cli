import argparse
import asyncio
import code
import functools
import re
import shlex
import sys
import traceback
from argparse import ArgumentParser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Never, Self

import more_itertools
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.patch_stdout import patch_stdout

type ParsedArgs = argparse.Namespace
type CmdAsyncFunc[T] = Callable[[T, str], Awaitable[None]]
type CmdFunc[T] = Callable[[T, str], None | Awaitable[None]]
type ArgparseCmdAsyncFunc[T] = Callable[[T, ParsedArgs], Awaitable[None]]
type ArgparseCmdFunc[T] = Callable[[T, ParsedArgs], None | Awaitable[None]]
type KeyBindingFunc[T] = Callable[[T, KeyPressEvent], None]

CMD_FUNC_PREFIX: str = "do_"
KEY_BINDING_FUNC_PREFIX: str = "kb_"
DOC_STRING_REGEX: re.Pattern = re.compile(
    r"(?P<summary>([^\S\n]*\S.*\n?)+)(\s*\Z|\n\s*\n)", re.MULTILINE
)


@dataclass
class Command[T]:
    """A cmd in the REPL."""

    name: str  # The name of the command.
    aliases: list[str]  # A list of aliases for the command.
    func: CmdAsyncFunc[T]  # A function to execute for the command.
    summary: str | None  # A short summary for the command.
    long_help: str | None  # A longer description of the command.

    async def __call__(self, self_: T, prompt: str) -> None:
        return await self.func(self_, prompt)

    async def call_wrap_exception(self, self_: T, prompt: str) -> None:
        try:
            return await self.func(self_, prompt)
        except ReplException as ex:  # Reraise
            raise ex
        except Exception as ex:
            raise _CommandException(ex) from ex


@dataclass
class KeyBinding[T: "ReplBase"]:
    """A key binding and corresponding method in a REPL."""

    keys: list[Keys]
    ptk_kwargs: dict  # Keyword arguments to prompt_toolkit.key_binding.KeyBindings.add().
    func: KeyBindingFunc[T]  # A function to execute for the key binding.
    summary: str | None  # A short summary for the key binding.

    def __call__(self, repl: T, event: KeyPressEvent) -> None:
        self.func(repl, event)

    def call_catch_exception(self, repl: T, event: KeyPressEvent) -> None:
        try:
            try:
                self.func(repl, event)
            except ReplException as ex:  # Reraise
                raise ex from ex
            except Exception as ex:  # Wrap in CommandException
                raise _CommandException(ex) from ex
        except Exception as exc:
            repl._kb_exceptions.put_nowait(exc)
            repl._kb_exception_event.set()


class ReplException(Exception):
    """Base exception for exceptions related to the REPL and the command loop."""

    pass


class CommandFailure(ReplException):
    """An exception to raise when a command fails.

    The message of the exception will be printed on STDERR.
    """

    pass


class QuitRepl(ReplException):
    """Exception to raise to quit the REPL."""

    pass


class CmdLoopContinue(ReplException):
    """Raise this exception to clear the current prompt and continue to the next
    iteration in the cmd loop."""

    pass


@dataclass
class _CommandException(Exception):
    """Wrapper class for exceptions thrown from key binding handlers or command
    functions."""

    inner_exc: Exception


class ReplBase:
    """Base class for a REPL with no helpful commands like help or quit."""

    # All commands stored in a dict with names/aliases as keys. Note that a command
    # may occur multiple times if it has multiple names/aliases.
    _cmds: dict[str, Command[Self]]
    _key_bindings: list[KeyBinding]  # All registered key bindings.
    _kb_manager: KeyBindings  # The prompt_toolkit's key bindings manager.
    # If an exception is thrown in a key binding handler it will be put on this queue.
    _kb_exceptions: asyncio.Queue[Exception]
    # If an exception is thrown in a key binding handler, this event will be set.
    _kb_exception_event: asyncio.Event
    _custom_tasks: set[asyncio.Task]
    prompt_session: PromptSession

    def __init__(self) -> None:
        self._cmds = {}
        self._key_bindings = []
        self._custom_tasks = set()
        self._kb_manager = KeyBindings()
        self._kb_exceptions = asyncio.Queue()
        self._kb_exception_event = asyncio.Event()
        for name in dir(self):
            if name.startswith(CMD_FUNC_PREFIX):
                cmd = getattr(self, name)
                assert isinstance(cmd, Command), (
                    f"Repl initialization: All attributes starting with {CMD_FUNC_PREFIX} must be"
                    f" an instance of repl.Command. {cmd.__qualname__} is not. Please decorate it"
                    " with repl.command() or repl.argparse_command()."
                )
                self.add_cmd(cmd)
            if name.startswith(KEY_BINDING_FUNC_PREFIX):
                kb = getattr(self, name)
                assert isinstance(kb, KeyBinding), (
                    "Repl initialization: All attributes starting with"
                    f" {KEY_BINDING_FUNC_PREFIX} must be an instance of repl.KeyBinding."
                    f" {kb.__qualname__} is not. Please decorate it with repl.key_binding()."
                )
                self._key_bindings.append(kb)
                self._kb_manager.add(*kb.keys, **kb.ptk_kwargs)(
                    functools.partial(kb.call_catch_exception, self)
                )
        self.prompt_session = PromptSession(
            key_bindings=self._kb_manager, enable_system_prompt=True, enable_open_in_editor=True
        )

    def add_cmd(self, cmd: Command[Self]) -> None:
        for name in more_itertools.prepend(cmd.name, cmd.aliases):
            assert name not in self._cmds, (
                f"Error when adding cmd {cmd.name}: {name} is already bound to the"
                f" {self._cmds[name].name}-cmd."
            )
            self._cmds[name] = cmd

    def add_task(self, task: asyncio.Task) -> None:
        """Add a task which will be run concurrently with the prompt.

        It may through exceptions such as CmdLoopContinue.
        """
        self._custom_tasks.add(task)

    async def pre_prompt(self) -> None:
        """Called before issuing a new prompt; that is, at every iteration of the cmd loop.

        You can override this method to simulate post command hooks.
        """
        pass

    def poutput(self, *args, **kwargs) -> None:
        """Print to stdout.

        Excepts the same arguments as the builtin `print` function.
        """
        print(*args, **kwargs)

    def perror(self, *args, **kwargs) -> None:
        """Print to stderr."""
        print(*args, file=sys.stderr, **kwargs)

    async def exec_cmd(self, prompt: str) -> None:
        """Parse a prompt and execute the corresponding command."""
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
        await command.call_wrap_exception(self, rest)

    def prompt_str(self) -> str:
        """Get the string for the prompt, you can override this."""
        return "> "

    async def prompt(self) -> None:
        """Issue a prompt and execute the entered command."""
        input_str: str
        if sys.stdin.isatty():
            input_str = await self._interactive_prompt()
        else:
            input_str = input()
            if input_str == "":
                raise QuitRepl()
        return await self.exec_cmd(input_str)

    async def _interactive_prompt(self) -> str:
        """Issue a prompt and execute the entered command."""

        async def kb_exc() -> Never:
            """Wait for any key binding handler to throw an exception and reraise it."""
            await self._kb_exception_event.wait()
            self._kb_exception_event.clear()
            raise self._kb_exceptions.get_nowait()

        async def prompt_wrapper() -> str:
            """Issue the prompt and handle KeyboardInterrupt exception."""
            try:
                return await self.prompt_session.prompt_async(self.prompt_str())
            except KeyboardInterrupt as ex:
                raise CmdLoopContinue() from ex
            except EOFError as ex:
                raise QuitRepl() from ex

        kb_exc_task: asyncio.Task = asyncio.create_task(kb_exc())
        prompt_task: asyncio.Task = asyncio.create_task(prompt_wrapper())
        with patch_stdout():
            try:
                while True:
                    done, pending = await asyncio.wait(
                        (kb_exc_task, prompt_task, *self._custom_tasks),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    assert len(done) == 1
                    assert self._kb_exceptions.empty()
                    [done] = done
                    if done is prompt_task:
                        return prompt_task.result()
                    else:
                        if done is not kb_exc_task:
                            self._custom_tasks.remove(done)
                        done.result()
            finally:
                kb_exc_task.cancel()
                if self.prompt_session.app.is_running:
                    self.prompt_session.app.exit()
                await prompt_task

    async def cmd_loop(self) -> None:
        """Run the application in a loop."""
        while True:
            try:
                await self.pre_prompt()
                await self.prompt()
            except CmdLoopContinue:
                continue
            except QuitRepl:
                break
            except CommandFailure as e:
                self.perror(f"Error: {e}")
            except _CommandException as ex:
                traceback.print_exception(ex.inner_exc)
            except asyncio.exceptions.CancelledError:
                self.perror("CancelledException thrown!")


def _get_cmd_name(func: Callable) -> str:
    """Given a method like do_foo_bar(), return the kebab-cased command name like foo-
    bar."""
    assert func.__name__.startswith(CMD_FUNC_PREFIX), (
        f"The name of a command function must start with {CMD_FUNC_PREFIX}, which is not the"
        f" case with {func.__qualname__}."
    )
    name: str = func.__name__[len(CMD_FUNC_PREFIX) :]
    return name.replace("_", "-")


def command[T: ReplBase](
    name: str | None = None,
    alias: list[str] | str | None = None,
    summary: str | None = None,
    long_help: str | None = None,
) -> Callable[[CmdFunc[T]], Command[T]]:
    """A decorator for methods of `Repl` to add them as commands."""

    aliases: list[str] = [alias] if isinstance(alias, str) else (alias or [])

    def decorator(func: CmdFunc[T]) -> Command[T]:
        nonlocal summary
        nonlocal long_help
        if summary is None and func.__doc__:
            summary_match = DOC_STRING_REGEX.match(func.__doc__)
            summary = summary_match.group("summary").strip() if summary_match is not None else None
        if long_help is None:
            long_help = func.__doc__.strip() if func.__doc__ else None
        name: str = _get_cmd_name(func)
        if asyncio.iscoroutinefunction(func):
            cmd: Command[T] = Command(
                name=name, aliases=aliases, func=func, summary=summary, long_help=long_help
            )
        else:

            @functools.wraps(func)
            async def async_func(*args, **kwargs) -> None:
                func(*args, **kwargs)

            cmd = Command(
                name=name, aliases=aliases, func=async_func, summary=summary, long_help=long_help
            )
        functools.update_wrapper(cmd, func)
        return cmd

    return decorator


def argparse_command[T: ReplBase](
    argparser: ArgumentParser, alias: list[str] | str | None = None
) -> Callable[[ArgparseCmdFunc[T]], Command[T]]:
    """Returns a decorator for methods of `Repl` to add them as commands with an
    argparser."""

    def decorator(func: ArgparseCmdFunc[T]) -> Command[T]:
        argparser.prog = _get_cmd_name(func)
        if not argparser.description:
            argparser.description = func.__doc__

        @functools.wraps(func)
        async def cmd_func(repl: T, prompt: str) -> None:
            args = shlex.split(prompt)
            try:
                parsed_args: ParsedArgs = argparser.parse_args(args)
            except SystemExit:
                return
            if asyncio.iscoroutinefunction(func):
                return await func(repl, parsed_args)
            else:
                func(repl, parsed_args)

        return command(
            alias=alias,
            summary=argparser.description if not func.__doc__ else None,
            long_help=argparser.format_help(),
        )(cmd_func)

    return decorator


def key_binding[T: ReplBase](
    keys: Keys | str | list[Keys | str], summary: str | None = None, **ptk_kwargs
) -> Callable[[KeyBindingFunc[T]], KeyBinding[T]]:
    """A decorator for methods of `Repl` to add them as key bindings.

    :param keys: one or more key bindings to trigger the method
    :param summary: a short summary for the method, defaults to the summary of the
        method's docstring
    :param ptk_kwargs: additional keyword arguments to be sent to
        prompt_toolkit.key_binding.KeyBindings.add()
    """

    keys_: list[Keys | str] = keys if isinstance(keys, list) else [keys]
    keys__: list[Keys] = [Keys(k) for k in keys_]

    def decorator(func: KeyBindingFunc[T]) -> KeyBinding[T]:
        nonlocal summary
        if summary is None and func.__doc__:
            summary_match = DOC_STRING_REGEX.match(func.__doc__)
            summary = summary_match.group("summary").strip() if summary_match is not None else None
        assert func.__name__.startswith(KEY_BINDING_FUNC_PREFIX), (
            f"The name of a key binding function must start with {KEY_BINDING_FUNC_PREFIX}, "
            f"which is not the case with {func.__qualname__}."
        )
        kb: KeyBinding[T] = KeyBinding(
            keys=keys__, ptk_kwargs=ptk_kwargs, func=func, summary=summary
        )
        functools.update_wrapper(kb, func)
        return kb

    return decorator


class Repl(ReplBase):
    """Base class for a REPL with helpful commands like help or quit."""

    async def yes_no_dialog(self, question: str) -> bool:
        """Show a yes/no dialog to the user and return True iff the answer was yes."""
        while True:
            ans: str = await self.prompt_session.prompt_async(f"{question} [Yes/no]: ")
            match ans.lower():
                case "y" | "yes":
                    return True
                case "n" | "no":
                    return False
                case _:
                    print("Error: Please answer yes or no.")

    @command(alias=["q", "exit"])
    def do_quit(self, _) -> None:
        """Exit the REPL."""
        raise QuitRepl()

    help_argparser = ArgumentParser()
    help_argparser.add_argument("command", nargs="?", help="Get help for this specific command.")

    @argparse_command(help_argparser, alias="h")
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
                line += f"  --  {command.summary}"
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

    @command(alias="kb")
    def do_key_bindings(self, _) -> None:
        """Print a list of all active key bindings."""
        for kb in self._key_bindings:
            text: str = ", ".join(kb.keys)
            if kb.summary:
                text += f"  --  {kb.summary}"
            self.poutput(text)

    @key_binding("c-q")
    def kb_quit(self, _) -> None:
        """Quit the REPL."""
        # TODO: make this work
        raise QuitRepl()
