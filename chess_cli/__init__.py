import os
from pathlib import Path

# TODO: Wait for https://github.com/Nuitka/Nuitka/issues/2454
# import importlib.metadata
# __metadata__ = importlib.metadata.metadata("chess-cli")
# __version__ = __metadata__["version"]
# __author__ = __metadata__["author"]
__version__ = "0.6.1"
__author__ = "Tage Johansson"
__package__ = "chess_cli"

# Add the dlls directory to path:
dllspath = str(Path(__file__).parent / "dlls")
os.environ["PATH"] = dllspath + os.pathsep + os.environ["PATH"]
