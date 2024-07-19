#!/usr/bin/python3
"""A short script to parse the output of dependencies.exe from
<https://github.com/lucasg/Dependencies>.

The purpose is to get all dependent (non-system) DLLs for a DLL-file. I used it
to query the dependencies for libcairo-2.dll:
    - Download and install GTK+ from <https://www.gtk.org>.
    - Download and unpack dependencies from <https://github.com/lucasg/Dependencies>.
    - Open a terminal and navigate to the lib directory in the GTK installation folder.
    - Run dependencies.exe with an appropriate depth and pipe the JSON output to this script:
        Dependencies.exe libcairo-2.dll -chain -json -depth 4 | python query_dll_dependencies.py.
"""

import json
import sys
from pathlib import PureWindowsPath


def collect_dlls(data: dict, collection: set[PureWindowsPath]) -> None:
    """Collect all descendent DLLs relative to the current directory."""
    f = data["Filepath"]
    if not f:
        return
    this: PureWindowsPath = PureWindowsPath(f)
    if this.is_relative_to("."):
        collection.add(this)
        for d in data["Dependencies"]:
            collect_dlls(d, collection)


def main() -> None:
    data = json.load(sys.stdin)
    dlls: set[PureWindowsPath] = set()
    collect_dlls(data["Root"], dlls)
    for dll in dlls:
        print(dll)


if __name__ == "__main__":
    main()
