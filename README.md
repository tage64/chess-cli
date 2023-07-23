# Chess-CLI - A repl to read, edit and analyse chess games in the terminal

A REPL for editing and analysing chess games.
See [manual here][3].

## Building

## Prerequisites

You need [Python 3.11 or later][1] and [Poetry][2].
You can install the latter with:
```Bash
$ pip install poetry
```

### Running in Development Environment

nstall the virtual environment with:
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

### Building an Windows executable

Inside Powershell, run:
```
$ windows_build.bat
```

Answer yes on all queries. (It might take some time and consume alot of memory.)

The resulting standalone executable is named "chess-cli.exe".

## Usage

If you managed to run the program with `poetry run chess-cli` (as described above), you will land in a REPL with the initial prompt "start: ".
To get a list of all commands, enter "help" followed by enter.
To get help information about a specific command, run `<COMMAND> --help`.

See a more complete manual [here][3].




[1]: https://www.python.org/downloads/
[2]: https://python-poetry.org
[3]: doc/manual.md
