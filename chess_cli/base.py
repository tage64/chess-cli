from dataclasses import dataclass, field
from typing import *
import os

import appdirs  # type: ignore
import cmd2
import toml  # type: ignore


@dataclass
class InitArgs:
    file_name: Optional[str] = None
    config_file: str = field(
        default_factory=lambda: os.path.join(
            appdirs.user_config_dir("chess-cli"), "config.toml"
        )
    )


class Base(cmd2.Cmd):
    def __init__(self, args: InitArgs):
        ## Initialize cmd2:
        shortcuts: dict[str, str] = dict(cmd2.DEFAULT_SHORTCUTS)
        super().__init__(shortcuts=shortcuts, include_py=True, allow_cli_args=False)
        self.self_in_py = True

        self.load_config(args.config_file)

    def load_config(self, config_file: str) -> None:
        self.config_file: str = config_file
        try:
            with open(self.config_file) as f:
                self.config: dict = toml.load(f)
                if not isinstance(self.config, dict):
                    raise Exception("Failed to parse configuration")
        except Exception as ex:
            self.config = {}
            self.poutput(
                f"Error while processing config file at '{self.config_file}': {repr(ex)}"
            )
            self.poutput(
                "This session will be started with an empty configuration."
            )
