uv sync
uv run --with pyinstaller pyinstaller ^
  --noconfirm ^
  --add-data dlls:dlls ^
  --add-data ffmpeg:ffmpeg ^
  --copy-metadata berserk ^
  --onefile ^
  --name chess-cli ^
  main.py
