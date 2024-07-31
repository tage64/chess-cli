# nuitka-project: --standalone
# nuitka-project: --windows-console-mode=force
# nuitka-project: --user-package-configuration-file={MAIN_DIRECTORY}/my.nuitka-package.config.yml

if "__compiled__" in globals():
    # Disable Typeguard as it tries to inspect the source code of functions
    # which is not availlable when compiling with Nuitka.
    import os

    os.environ["TYPEGUARD_DISABLE"] = "1"
import chess_cli.main

if __name__ == "__main__":
    chess_cli.main.main()
