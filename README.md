# Chess-CLI - A repl to read, edit and analyse chess games in the terminal

A REPL for editing and analysing chess games.
See [manual here][3].

## Building

## Prerequisites

You need [Python 3.12 or later][1] and [Poetry][2].
You can install the latter with:
```Bash
$ pip install poetry
```

You also need ffmpeg and portaudio installed.

### Running in Development Environment

Install the virtual environment with:
```Bash
$ poetry install
```

And run it with:
```Bash
$ poetry run chess-cli
```

### Building

Just run:
```Bash
$ poetry build
# The result is in the dist directory, install with:
$ pip install dist/*.whl
```

### Building a Windows executable

Inside Powershell, run:
```
$ .\pyinstaller_windows.bat
```

The resulting (standalone) executable should be dist/chess-cli.exe.

## Usage

If you managed to run the program with `poetry run chess-cli` (as described above), you will land in a REPL with the initial prompt "start: ".
To get a list of all commands, enter "help" followed by enter.
To get help information about a specific command, run `<COMMAND> --help`.

See a more complete manual [here][3].




[1]: https://www.python.org/downloads/
[2]: https://python-poetry.org
[3]: doc/manual.md
