__version__ = '0.1.0'

import sys

import cmd2


class ChessCli(cmd2.Cmd):
    """A repl to edit and analyse chess games. """


if __name__ == '__main__':
    sys.exit(ChessCli().cmdloop())
