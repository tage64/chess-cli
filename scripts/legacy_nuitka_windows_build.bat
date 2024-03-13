poetry install
poetry run python -m nuitka main.py --onefile --product-name=chess-cli --copyright=GPL-3.0-or-later --product-version=0.1.0 --output-filename=chess-cli.exe
