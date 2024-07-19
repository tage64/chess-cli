# Chess-CLI - A repl to read, edit and analyse chess games in the terminal

A REPL for editing and analysing chess games.
See [manual here][3].

## Building

### Prerequisites

You need [Python 3.12 or later][1] and [Poetry][2].
You can install the latter with:
```Bash
$ pip install poetry
```

On GNU/Linux, you also need ffmpeg and portaudio installed, on Windows, these things are bundeled
with the source files.

### Running in Development Environment

Install the virtual environment with:
```Bash
$ poetry install
```

And run it with:
```Bash
$ poetry run chess-cli
```

### Building Python Package

Just run:
```Bash
$ poetry build
# The result is in the dist directory, install with:
$ pip install dist/*.whl
```

### Building a Windows Installer

Inside Powershell, run:
```
$ poetry run nuitka chess-cli.py
```

The resulting executable should be `chess-cli.dist/chess-cli.exe`.

To build the installer, you need to install [NSIS][4]. Then, right click `windows_setup.nsi` in
Windowrs Explorer and choose "Compile NSIS Script". The resulting installation file is called
`Chess-CLI-setup.exe`.

## Usage

If you managed to run the program with `poetry run chess-cli` (as described above), you will land in a REPL with the initial prompt "start: ".
To get a list of all commands, enter "help" followed by enter.
To get help information about a specific command, run `<COMMAND> --help`.

See a more complete manual [here][3].




[1]: https://www.python.org/downloads/
[2]: https://python-poetry.org
[3]: doc/manual.md
[4]: https://nsis.sourceforge.io/Download
