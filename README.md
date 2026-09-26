# Chess-CLI - A repl to read, edit and analyse chess games in the terminal

A REPL for editing and analysing chess games.
See [manual here][3] and changelog [here][5].

## Building

### Prerequisites

You need [Python 3.12 or 3.13][1] and [uv][2].
Install uv using the instructions linked below.

On GNU/Linux, you also need ffmpeg and portaudio installed, on Windows, these things are bundeled
with the source files.

### Running in Development Environment

Install the virtual environment with:
```Bash
$ uv sync
```

And run it with:
```Bash
$ uv run chess-cli
```

### Building Python Package

Just run:
```Bash
$ uv build
# The result is in the dist directory, install with:
$ pip install dist/*.whl
```

### Building a Windows Installer

Inside Powershell, run:
```
$ uv run nuitka chess-cli.py
```

The resulting executable should be `chess-cli.dist/chess-cli.exe`.

To build the installer, you need to install [NSIS][4]. Then, right click `windows_setup.nsi` in
Windowrs Explorer and choose "Compile NSIS Script". The resulting installation file is called
`Chess-CLI-setup.exe`.

## Usage

If you managed to run the program with `uv run chess-cli` (as described above), you will land in a REPL with the initial prompt "start: ".
To get a list of all commands, enter "help" followed by enter.
To get help information about a specific command, run `<COMMAND> --help`.

See a more complete manual [here][3].




[1]: https://www.python.org/downloads/
[2]: https://docs.astral.sh/uv/getting-started/installation/
[3]: doc/manual.md
[4]: https://nsis.sourceforge.io/Download
[5]: CHANGELOG.md
