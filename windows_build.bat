poetry install
poetry run python -m nuitka main.py --onefile --product-name=chess-cli --copyright=GPL-3.0-or-later --output-filename=chess-cli.exe
