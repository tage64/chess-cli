# nuitka-project: --onefile
# nuitka-project: --enable-console
# nuitka-project: --user-package-configuration-file={MAIN_DIRECTORY}/my.nuitka-package.config.yml
import chess_cli.main

if __name__ == "__main__":
    chess_cli.main.main()
