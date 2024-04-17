import importlib.metadata
import os

__metadata__ = importlib.metadata.metadata("chess-cli")
__version__ = __metadata__["version"]
__author__ = __metadata__["author"]
__package__ = "chess_cli"

# Add the dlls directory to path:
basepath = os.path.dirname(os.path.abspath(__file__))
dllspath = os.path.join(basepath, "..", "dlls")
os.environ["PATH"] = dllspath + os.pathsep + os.environ["PATH"]
