#!/usr/bin/env bash
# run.sh — Launch cloud-init GUI via uv (macOS / Linux)
# Usage: bash run.sh   (or: chmod +x run.sh && ./run.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  cloud-init GUI — Ubuntu Server OVA Configurator"
echo "  -------------------------------------------------"
echo ""

# ---------------------------------------------------------------------------
# 1. Ensure uv is available, installing it if necessary
# ---------------------------------------------------------------------------
if ! command -v uv &>/dev/null; then
    echo "uv not found — downloading and installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # The installer places uv in ~/.local/bin; add it to PATH for this session
    export PATH="$HOME/.local/bin:$PATH"

    if ! command -v uv &>/dev/null; then
        echo ""
        echo "ERROR: uv was installed but 'uv' is still not in PATH."
        echo "Open a new terminal and re-run this script, or add ~/.local/bin to PATH."
        exit 1
    fi
    echo "uv installed: $(uv --version)"
else
    echo "uv found: $(uv --version)"
fi

# ---------------------------------------------------------------------------
# 2. Ensure a pyproject.toml exists (uv project bootstrap)
# ---------------------------------------------------------------------------
if [[ ! -f pyproject.toml ]]; then
    echo "Initialising uv project..."
    uv init --name cloud-init-gui --no-readme --python ">=3.10"
    rm -f main.py hello.py   # remove uv stub files — we have our own entry points
fi

# ---------------------------------------------------------------------------
# 3. Install Python dependencies
#    PyYAML and pycdlib are required; tk is best-effort (may come from the OS)
# ---------------------------------------------------------------------------
echo "Syncing dependencies..."
uv add "PyYAML>=6.0" "pycdlib>=1.14.0" 2>/dev/null || true
uv add tk 2>/dev/null || true   # optional wheel; fall back to system python3-tk

# Verify tkinter is importable (stdlib module backed by native Tcl/Tk libs)
if ! uv run python -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "WARNING: tkinter is not available in the uv-managed Python."
    echo "On Debian / Ubuntu, install the system package and retry:"
    echo "  sudo apt install python3-tk"
    echo ""
    echo "On macOS with Homebrew Python, try:"
    echo "  brew install python-tk"
    echo ""
    echo "Then re-run: ./run.sh"
    exit 1
fi

# ---------------------------------------------------------------------------
# 4. Launch the application
# ---------------------------------------------------------------------------
echo "Launching cloud-init GUI..."
echo ""
exec uv run app.py
