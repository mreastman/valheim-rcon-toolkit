#!/bin/bash
# Double-click launcher for rcon_dashboard.py (macOS).
# Checks for Python 3 and the textual package, installing textual if it's
# missing, then runs the dashboard. Double-clicking runs this via Terminal.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required but wasn't found."
    echo "Install it from https://www.python.org/downloads/ and try again."
    read -r -p "Press Enter to close..."
    exit 1
fi

if ! python3 -c "import textual" >/dev/null 2>&1; then
    echo "Installing required package (textual)..."
    if ! python3 -m pip install --quiet textual; then
        echo "Automatic install failed. Try running manually:"
        echo "  python3 -m pip install textual"
        read -r -p "Press Enter to close..."
        exit 1
    fi
fi

python3 rcon_dashboard.py

read -r -p "Press Enter to close..."
