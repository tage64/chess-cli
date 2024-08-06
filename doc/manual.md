# Chess-CLI - A Chess Command-line Utility

Chess-CLI is a [command-line utility][1] for editing, analyzing and viewing chess games.
It is a purely text based interfaced designed to work well with [screen readers][2] such as [NVDA][3].
Although it may be used by any chess enthusiast as a light weight chess tool.
This manual though, includes some helpful notes for NVDA-users, if those are not applicable to you, please just ignore them.

## Installation

### Windows

Windows users can just download and run [this installation program][4]. You will perhaps need to
accept the risc of running an executable from an unknown publisher, click on "More Info" and then
"Run Anyway" and it should work. The installer will create desktop and start menu shortcuts for
chess-cli and add `chess-cli` to the `PATH` environment variable.

### Linux, Mac and other platforms

Currently, the easiest is just to run it from the development environment.
See the [Readme][5] for instructions.

## Navigating the Terminal with NVDA

If you don't use a screen reader you may skip this section.

As aposed to a [graphical user interface][7], Chess-CLI uses a [text terminal][6] to interact with the user.
So here follows some tips of how to read and navigate the terminal using the [screen reader][2] [NVDA][3]:

### A note about keyboard layout

The following shortcuts will be different depending on your [keyboard layout][8], if you have a desktop or laptop keyboard.
Basicly, you should use the desktop layout if and only if you have a [numpad][9] on your keyboard.
You can change the setting in `NVDA Preferences -> Settings -> Keyboard`.

You will also need to know your [NVDA key][10], which is usually insert or capslock.

### Basic shortcuts

A terminal is basicly a 2-dimensional view of the text on the screen. The terminal also has a
cursor, which marks the current focus. To read the next/previous line, press numpad7/numpad9 on
desktop or NBDA+upArrow/NVDA+downArrow on laptop. To read the current line press numpad8 on desktop
or NVDA+shift+. on laptop. And to move focus to the cursor, press NVDA+numpadMinus on desktop or
NVDA+backspace on laptop. Those few shortcuts will be a good start. For a complete list of
shortcuts, see [the NVDA command reference here][11]. Especially [this section][12].

### Correct Review Mode

If the above commands doesn't work, make sure that object review is selected by pressing
NVDA+numpad1 a few times. More information about different review modes can be found [here][13].

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

#### Keyboard shortcuts

Chess-CLI has some builtin keyboard shortcuts. To get a list of them, type `key-bindings` or `kb`.

### Edit the Game

#### Make Moves

The easiest way to insert moves in the game is to just type them in [standard algebraic
notation][14]:

```
start: e4
1. e4: e5
1... e5: f4
2. f4:
```

If you want to make multiple moves in one command, or want to insert sidelines, you may use the
"play" (or "p") command:

```
2. f4: play exf4 Nf3 g5 h4
```
To add a sideline, add the flag "-s":
```
4. h4: p -s Bc4 g4 O-O
```

You can go back in the game with Shift+UpArrow and add variations in the game. For instance, if you
press `Shift+UpArrow` until you come to the move "2. f4" (or enter the command "goto 2."), and then
enter the move "d5", you'll add `d5` as a variation to `exf4`. You can move between different
variations with `Shift+LeftArrow` and `Shift+RightArrow`, and move forward in the game with
`Shift+DownArrow`.

To promote a variation, that is move a sideline closer to the mainline, use the "promote" (or "pr")
command. "demote" (or "de") works in the opposite way. "promote -m" will promote a sideline to the
mainline immediately.

If a move has multiple continuations already, and you want to add a new move as the mainline, you
may use the `-m` flag to the `play` command:

```
2. f4: p -m Nc6
```

#### Show the Moves of the Game

The "moves" command prints all moves in the game:

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

### Navigate in the Game

The shortcuts `Shift+UpArrow` and `Shift+DownArrow` can be used to move to the previous or next move
in the game following the mainline. `Shift+LeftArrow` and `Shift+RightArrow` can be used to move to
the previous or next sideline.

#### Goto a Specific Move

The "goto" (or "g") command is used to go to a specific move in the game.
It takes as argument either a move number or a move.
The "-r" flag makes it recurse into sidelines.
For example:

```
3... e4: goto f4
2. f4: g 2...
2... exf4>: g start
start: g -r 3...e4
```

### Saving and Loading Games

Chess-CLI can read and write [PGN files][15].
Note that the paths to files in the following commands are relative to the working directory of Chess-CLI.
So if you enter "load -f foo.pgn", Chess-CLI will try to open a file named "foo.pgn" in the same directory as where the Chess-CLI executable was started.

#### Load

The "load" command loads a PGN file.
WARNING: Your current game will be lost when loading the new one.

Example:

```
start: load -f foo.pgn
```

You can also load a FEN or game from the clipboard with the `-c` flag:
```
start: load -c
```

#### Save

The "save" command saves the current game. If the current game is not loaded from a file and the
game hasn't been saved before, a file name must be provided with the `-f` flag. The `-c` flag can
alternatively be used to copy the game to the clipboard:
```
start: save -f foo.pgn
start: save -c
```

If you want to copy the current FEN to the clipboard you may use the `fen` command:
```
start: fen -c
```

### Setup Position

It is possible to setup a custom starting position for a game.

#### View the Current Position

You may view the current position by typing the `board` (or `b`) command. It'll both print an ASCII
representation of the board, where white and black pieces are represented by upper and lower case
letters and empty black and white squares are represented by plusses (`+`) and dashes
(`-`) respectively. Then, the castling rights and a list of all pieces for white and black are
printed as well.

```
start: b
  a b c d e f g h
8 r n b q k b n r 8
7 p p p p p p p p 7
6 - + - + - + - + 6
5 + - + - + - + - 5
4 - + - + - + - + 4
3 + - + - + - + - 3
2 P P P P P P P P 2
1 R N B Q K B N R 1
  a b c d e f g h

White and Black can castle on both sides
White: Ke1 Qd1 Ra1,h1 Bc1,f1 Nb1,g1 Pa2,b2,c2,d2,e2,f2,g2,h2
Black: ke8 qd8 ra8,h8 bc8,f8 nb8,g8 pa7,b7,c7,d7,e7,f7,g7,h7
```
 
#### Setup a Custom Position

A position can be setup by the `setup` command. It can either be the string "start" ("or "s"), a
FEN, or a list of piece-square identifiers like Kg1 or bb8.

- `setup start` sets up the starting position.
- `setup 4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w KQkq - 0`
   sets up a position by a FEN string.
- `setup Kg1 Pa2,b2,c2 ke8 qd8`
   sets a position by piece square identifiers, (see the put command for more details)

#### Put Individual Pieces

Use the `put` command to put individual pieces on the board. White pieces are denoted with capital
letters. Here are some examples:

```
put Rc6 pc2,b3
```
Puts a white rook on c6 and black pawns on c2 and b3.
```
put Ke3 Qd1 bb2,b3
```
Puts the white king on e3, a white queen on d1, and black bishops on b2 and b3.

#### Clear Squares

Clear squares with the clear command:
```
clear c3 d2
```
Clears the `c3` and `d2` squares.

#### Get and Set Turn to Play

To get the player to move in the current position, use the `turn command:
```
start: turn
white
```

Change the turn with:
```
start: turn black
It is now black to play.
start: turn
black
```

#### Get and Set Castling Rights

Get the castling rights with the `castling` (or `csl`) command:
```
start: csl
White and black can castle on both sides
```

Set castling rights by a short string which is either 'clear' or a combination of the letters 'K', 'k', 'Q' or 'q' where each letter denotes
king- or queenside castling for white or black respectively:
```
start: csl Kq
White can castle kingside and black can castle queenside.
start: csl clear
Neither white nor black is allowed to castle.
```

#### Get and Set En-Passant Rights

En-passant rights can be set with the `ep` command. In the following example, we move the white pawn
from e2 to e5 and the black pawn from d7 to d5 and then alter the en-passant rights:
```
start: clear e2 d7
start: put Pe5 pd5
Putting:
- White pawn at e5
- Black pawn at d5
start: ep
En passant is not possible in this position.
start: ep d6
En passant is possible at d6.
start: ep clear
En passant is not possible in this position.
```

### Chess Engines

Chess-CLI can communicate with chess engines supporting any of the [UCI][16] or [XBoard][17]
protocols. Before using a chess engine for the first time, it must be imported or installed. If you
just want to install [Stockfish][18], you may simply use the `engine install` command:

```
start: engine install stockfish
```
This will install Stockfish with some sane defaults based on your computers hardware.

To import a costum engine, download the executable and put it on some known place of your file
system. Then, run the `engine import` command with the name of the executable and a name for the
engine:
```
start: engine import <PATH/TO/ENGINE_EXECUTABLE.exe> <NAME>
```

Note that it is possible to import the same engine with different names. This is useful if you want
different configurations for different purposes.

#### Load, Quit and Select Engines

You will have to load an imported engine before using it. Do this with the `engine load` command and
the name you gave it when importing:
```
start: engine load stockfish
```
It is possible to load an engine under a different name, useful if you want to load multiple
instances of the same engine:
```
start: engine load stockfish --as sf1
start: engine load stockfish --as sf2
```
List the loaded engines with the `engine ls --loaded` (or `engine ls -l`) command:
```
start: engine ls -l
>sf1: Stockfish 16.1, (loaded), (selected)
 sf2: Stockfish 16.1, (loaded)
```

All engines will be quit when exiting Chess-CLI. If you for some reason want to quit an engine
before that, you may use the `engine quit` command:
```
start: engine quit stockfish
```

If you load multiple engines, the last of them will be "selected". The currently selected engine is
the engine used for analysis and other engine related commands. To switch between loaded engines,
use the `engine select` command:
```
start: engine select sf1
```

#### List Engines

List all imported engines with:
```
start: engine ls
```
Or all loaded engines:
```
start: engine ls -l
```

#### Remove Engines

You can remove an imported engine with the `engine rm` command:
```
start: engine rm stockfish
```

#### Chess Engine Configuration

It is important to be able to configure a chess engine to adapt it to a specific use case. You
should consult the manual for your chess engine for recommendations, for Stockfish you can take a
look at [this page][19].

The `engine config` command is used to alter the parameters of the currently selected engine. You
can list all options with the `engine config ls` command:
```
start: engine config ls
...
```
To only list configured options add the `-c` flag:
```
start: engine config ls -c
Threads = 7: Default: 1, Min: 1, Max: 1024, Type: integer, (Configured)
Hash = 4096: Default: 16, Min: 1, Max: 33554432, Type: integer, (Configured)
```

To set the value of an option use the `engine config set` command with the name and value:
```
start: engine config set hash 6144
start: engine config get hash
Hash = 6144: Default: 16, Min: 1, Max: 33554432, Type: integer, (Configured)
```

For more configuration options, type `engine config --help`.

### Analysis

To begin analysing with the currently selected engine, simply type `analysis start`. The analysis
will follow when you enter new moves. If this is not desired, add the `--fixed` option.

If you want to limit the analysis, you may add options to the `analysis start` command. (For a
complete reference, type `analysis start --help`.) For instance, if you want to analyse for one hour
on a specific move, type:
```
1. e4: analysis start --fixed --time 3600
```

To show the analysis, type `analysis show` (or simply `a` for `analysis`).

The analysis can be stopped with `analysis stop`.

### Creat a Chess Match

It is possible to create chess matches, both you against the machine and two machines against each
other.

#### Add Players

You will first have to add the machines that should act as players. You can do this with the `player
add` command.

First load an engine (as described above), for instance "stockfish" and type:
```
start: player add stockfish white
```
This means that stockfish will be set to play white.

If you want Stockfish to play against an other engine, for instance Leela Chess Zero, and suppose
that Leela is loaded as "lc0", you can type:
```
start: player add lc0 b
```
("w" and "b" can be shorthands for "white and "black" respectively.)

To list the players, type `player ls`.

#### Set a Time Control

To set the clock, use the `clock set` command:
```
start: clock set 3+2
```
This will set the clock to 3 minutes plus 2 seconds increment per move.

If you want to change the time for one player, you can again use the `clock set` command:
```
start: clock set --bt 20:00 -bi 30
```
This will set Black's time to 20 minutes and 30 seconds increment.
```
start: clock show
3+2 -- 20+30
```

#### Start the Match

To check that everything is configured correctly, type `match show`:
```
start: match show
Players:
  White: stockfish
  White clock: 3+2
  Black clock: 20+30
The match is not started.
```

Start the match with the `match start` command. If it is white to play, the engine will begin think
immediately and make the move when done. Then, if you have set another engine to play black, that
engine will make its move, otherwise you can type a move.

#### During the Match

Under the match, you can check the current time with the `time` or (`t`) command:
```
1. d4: t
2 minutes and 55.2 seconds -- 19 minutes and 48 seconds
```

The match can be paused with the `match pause` (or `ma p`) and `match resume` (`ma r`) commands.

#### After the Match

After the match, you may use the `match show` command to display the result and the `match reset`
command to reset the clock and the players.

### Create Lichess Challenge

It is possible to create an open challenge on [Lichess][21] from the current position with the
`challenge` command. You have to provide a time control and optionally a name:
```
challenge 3+2 --name "Interesting game"
```

The command will output one general URL and one specific URL for White and Black.

### Recording Chess Videos

Chess-CLI has basic capabilities to record chess videos. Specifically, it can record audio and
render the current chess board. So the resulting video will only contain your voice and the chess
board, there is no functionality to film yourself at the moment.

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

You can as well use the shortcuts CTRL+P and CTRL+R to pause and resume the recording.

#### `record mark`

Sometimes when recording a lengthy video it is hard to find certain positions or variations within
the video. Therefor we have developed a system to mark certain key positions while recording, and
then export the timestamps for those positions within the video to a text file.

To put a mark on the current position (while recording), press `CTRL+K`, or type:
```
1... e5: record mark [<COMMENT>]
```
where `[<COMMENT>]` is an **optional** comment at the mark. You can also use the shortcut CTRL+K to
put a mark at the current position.

#### `record delete`

If you want to discard the recording you can use the `record delete` command.

#### `record save`

Save the recording with the `record save <FILE>.mp4` command. If you have taken marks, you should
also specify a TXT file for the marks.
```
1... e5: record save danish_gambit.mp4 marks.txt
Recording successfully saved to danish_gambit.mp4
It was 3 minutes and 7.2 seconds long.
```

[1]: https://en.wikipedia.org/wiki/Command-line_interface
[2]: https://en.wikipedia.org/wiki/Screen_reader
[3]: https://www.nvaccess.org
[4]: https://github.com/tage64/chess-cli/releases/latest/download/Chess-CLI-setup.exe
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
[16]: https://en.wikipedia.org/wiki/Universal_Chess_Interface
[17]: https://www.chessprogramming.org/Chess_Engine_Communication_Protocol
[18]: https://stockfishchess.org
[19]: https://disservin.github.io/stockfish-docs/stockfish-wiki/Stockfish-FAQ.html#optimal-settings
[20]: https://en.wikipedia.org/wiki/Forsyth–Edwards_Notation
[21]: https://lichess.org
