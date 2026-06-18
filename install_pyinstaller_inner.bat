@echo off
git submodule update --init
cd pyinstaller
setlocal
set PYINSTALLER_COMPILE_BOOTLOADER=1
python -m pip uninstall -y PyInstaller
python -m pip cache remove PyInstaller
python -m pip install --verbose --no-binary=PyInstaller PyInstaller
cd ..
