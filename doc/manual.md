# Chess-CLI - A Chess Command-line Utility

Chess-CLI is a [command-line utility][1] for editing, analyzing and viewing chess games.
It is a purely text based interfaced designed to work well with [screen readers][2] such as [NVDA][3].
Although it may be used by any chess enthusiast as a light weight chess tool.
This manual though, includes some helpful notes for NVDA-users, if those are not applicable to you, please just ignore them.

## Installation

### Windows

Windows users can just download [this standalone executable][4] and put it on some easily accessible place in the file system.
Just run the exe-file to start Chess-CLI.
You will perhaps need to accept the risc of running an executable from an unknown publisher, click on "More Info" and then "Run Anyway" and it should work.

### Linux, Mac and other platforms

Currently, the easiest is just to run it from the development environment.
See the [Readme][5] for instructions.

## Navigating the Terminal with NVDA

As aposed to a [graphical user interface][7], Chess-CLI uses a [text terminal][6] to interact with the user.
So here follows some tips of how to read and navigate the terminal using the [screen reader][2] [NVDA][3]:

### A note about keyboard layout

The following shortcuts will be different depending on your [keyboard layout][8], if you have a desktop or laptop keyboard.
Basicly, you should use the desktop layout if and only if you have a [numpad][9] on your keyboard.
You can change the setting in `NVDA Preferences -> Settings -> Keyboard`.

You will also need to know your [NVDA key][10], which is usually insert or capslock.

### Basic shortcuts

A terminal is basicly a 2-dimensional view of the text on the screen.
The terminal also has a cursor, which marks the current focus.
To read the next/previous line, press numpad7/numpad9 on desktop or NBDA+upArrow/NVDA+downArrow on laptop.
To read the current line press numpad8 on desktop or NVDA+shift+. on laptop.
And to move focus to the cursor, press NVDA+numpadMinus on desktop or NVDA+backspace on laptop.
Those few shortcuts will be a good start. For reference, see [the NVDA command reference here][11]. Especially [this section][12].

### Correct Review Mode

If the above commands doesn't work, make sure that object review is selected by pressing NVDA+numpad1 a few times.
More information about different review modes can be found [here][13].

### Command-line Interface in a Nutshell

Chess-CLI is a so called [command-line interface (CLI)][1].
A command-line interface is very simple, it roughly works as follows:

1. The user types a command like "help" or "quit" or "play e4" followed by enter.
2. The program processes the command and prints out a response.
    - [NVDA][3]-users can read that response by moving up in the terminal output with numpad7 or NVDA+upArrow, (see previous section).
    - No output usually means that the command did not encounter any error.

A CLI also has a prompt: the text right before the cursor on the same line.
In Chess-CLI, that prompt should initially be "start: ".
If you enter the command "play e4 e5", you shall notice that the prompt changes to "1... e5: ".
That means that the current position is no longer the starting position but the position after the moves e4 and e5.

Commands may take arguments.
Basicly, the first word on the command line is the name of a command and the following words are arguments to that command.
So if you enter "play e4 e5", then "play" would be the command and "e4" and "e5" will be arguments to the play-command.

To get a short description about a command and what arguments it takes, enter the command with the argument "--help".
Like "play --help" or "quit --help".

## All commands

Here follows a structured discription of all commands in Chess-CLI.
You should probably not read this from top to bottom, but rather skim through the sections and read what you find useful.

Note that these explonations focus rather on examples and less on completeness.
So to get a list of all flags and options for a command, you should enter the command with the "--help" argument.

### Fundamental commands - Quitinging, Getting Help, ...

#### Quit

The command "quit" exits the program and all unsaved moves are lost. No recovery possible.

#### Help

The "help" command prints out a short list of all availlable commands.

### Edit the Game

#### Play

The command "play" followed by a list of moves, will try to play those moves from the current position.
For example:

```
start: play e4 e5 f4 exf4
```
And add a sideline, add the flag "-s":
```
2... exf4: play -s d5 exd5 e4
```

Note that the moves must be entered in [standard algebraic notation][14], and that pieces must be entered with capital letters.
So "Nf3" instead of "nf3".

#### Moves - Show the Moves of the Game

The "moves" command prints all moves in the game.
Continuing on the previous example:

```
3... e4: moves
  1. e4 e5 2. f4 <d5 3. exd5 e4
```

The "<" at the 2... d5 indicates that there is another variation at that move.
To show the game with all variations, add the "-r" flag:

```
3... e4: moves -r
  1. e4 e5 2. f4 <d5
    2... exf4>
  3. exd5 e4
```

#### Goto a Specific Move

The "goto" command is used to go to a specific move in the game.
It takes as argument either a move number or a move.
The "-r" flag makes it recurse into sidelines.
For example:

```
3... e4: goto f4
2. f4: goto 2...
2... exf4>: goto start
start: goto -r 3...e4
```

### Saving and Loading Games

Chess-CLI can read and write [PGN files][15].
Note that the paths to files in the following commands are relative to the working directory of Chess-CLI.
So if you enter "load foo.pgn", Chess-CLI will try to open a file named "foo.pgn" in the same directory as where the Chess-CLI executable was started.

#### Load

The "load" command loads a PGN file.
WARNING: Your current game will be lost when loading the new one.

Example:

```
start: load foo.pgn
```

#### Save

The "save" command saves the current game.
If the current game is not loaded from a file and the game hasn't been saved before, a file name is required as argument.

### Recording Chess Videos

Chess-CLI has basic capabilities to record chess videos. Specifically, it can record audio and render the current chess board. So the resulting video will only contain your voice and the chess board, there is no functionality to film yourself at the moment.

#### `record start`

The `record start` command starts the recording of audio with your default microphone. There is no
way to select microphone at the moment.

Once the recording is started, the current chess board will be rendered in the video. So if you now
make moves or move to another position in the game, the chess board in the video will be updated.
For example:

```
start: record start
Recording started successfully.
<<Talk about the starting position>>
<<Say that we should look at the Danish gambit>>
start: play e4 e5 d4 exd4
2... exd4:
<<The position in the video is now updated>>
<<We can go back to the move e5>>
2... exd4: goto e5
1... e5:
<<The chess board in the video will now show the position after 1. e4 e5>>
```

If you are about to show a chess game or a complex analysis it might be advisable to prepair the
game first and only move around in the game with the arrow keys or the `goto` command. Remember that
you can use the shift-key plus any arrow key to quickly navigate in the game.

#### `record pause` and `record resume`

Pause/resume the recording:

```
1... e5: record pause
Paused recording at 8.8 seconds
<<You can say what you want, the recording is paused. >>
1... e5: record resume
Resumed recording at 8.8 seconds
```

#### `record delete`

If you want to discard the recording you can use the `record delete` command.

#### `record save`

Save the recording with the `record save <FILE>.mp4` command. The file name **must** end with
`.mp4`.

```
1... e5: record save danish_gambit.mp4
Recording successfully saved to danish_gambit.mp4
It was 3 minutes and 7.2 seconds long.
```

[1]: https://en.wikipedia.org/wiki/Command-line_interface
[2]: https://en.wikipedia.org/wiki/Screen_reader
[3]: https://www.nvaccess.org
[4]: https://github.com/tage64/chess-cli/releases/latest/download/chess-cli.exe
[5]: https://github.com/tage64/chess-cli#readme
[6]: https://en.wikipedia.org/wiki/Computer_terminal#Text_terminals
[7]: https://en.wikipedia.org/wiki/Graphical_user_interface
[8]: https://www.nvaccess.org/files/nvda/documentation/userGuide.html#KeyboardLayouts
[9]: https://en.wikipedia.org/wiki/Numeric_keypad
[10]: https://www.nvaccess.org/files/nvda/documentation/userGuide.html#TheNVDAModifierKey
[11]: https://www.nvaccess.org/files/nvda/documentation/keyCommands.html
[12]: https://www.nvaccess.org/files/nvda/documentation/keyCommands.html#ReviewingText
[13]: https://www.nvaccess.org/files/nvda/documentation/userGuide.html#ReviewModes
[14]: https://en.wikipedia.org/wiki/Algebraic_notation_(chess)
[15]: https://en.wikipedia.org/wiki/Portable_Game_Notation
