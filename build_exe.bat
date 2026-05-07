@echo off
py -3.13 -m PyInstaller --onefile --windowed ^
  --name quansheng-toolkit ^
  --icon assets/quansheng-toolkit.ico ^
  --collect-submodules quansheng_toolkit ^
  --add-data firmwares;firmwares ^
  --add-data assets;assets ^
  launcher.py
