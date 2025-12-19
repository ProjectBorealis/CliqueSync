#!/bin/sh

# This is where Python executables install if not installed system-wide
PATH="$HOME/.local/bin:$PATH"

export PATH

export PYTHONOPTIMIZE=1
export PYTHONHASHSEED=0
export PYI_STATIC_ZLIB=1
export OBJECT_MODE=64
pyinstaller --onefile \
            --additional-hooks-dir=hooks \
            -n CliqueSync \
            --clean \
            --runtime-tmpdir Saved \
            --key cd17c3ab10dba6bd \
            -i resources/icon.ico \
            --version-file version.rc \
            -p . pbsync/__main__.py "$@"
