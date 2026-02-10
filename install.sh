#!/bin/bash
# ============================================================
#  FM_CP Installer
#  Creates isolated venv + installs fm-cp with all dependencies
# ============================================================
set -e

# --- Config ---
INSTALL_DIR="$HOME/.fm-cp"
VENV_DIR="$INSTALL_DIR/venv"
BIN_LINK="/usr/local/bin/fm-cp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "============================================================"
echo "  FM_CP Installer"
echo "============================================================"
echo ""

# --- Check Python ---
if command -v python3 &>/dev/null; then
    PY=$(command -v python3)
    PY_VER=$($PY --version 2>&1)
    echo "  Python: $PY_VER ($PY)"
else
    echo "  ✗ Python 3 not found. Install Python 3.8+ first."
    exit 1
fi

# --- Check macOS ---
if [[ "$(uname)" != "Darwin" ]]; then
    echo "  ⚠ Not macOS — clipboard features will be unavailable."
    echo "    File I/O mode will still work."
    echo ""
fi

# --- Create install directory ---
echo "  Install dir: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# --- Create venv ---
if [ -d "$VENV_DIR" ]; then
    echo "  Removing existing venv..."
    rm -rf "$VENV_DIR"
fi

echo "  Creating virtual environment..."
$PY -m venv "$VENV_DIR"

# --- Install package ---
echo "  Installing fm-cp..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip

if [[ "$(uname)" == "Darwin" ]]; then
    "$VENV_DIR/bin/pip" install --quiet "$SCRIPT_DIR[clipboard]"
    echo "  ✓ Installed with clipboard support (PyObjC)"
else
    "$VENV_DIR/bin/pip" install --quiet "$SCRIPT_DIR"
    echo "  ✓ Installed (file I/O only, no clipboard)"
fi

# --- Create wrapper script ---
echo "  Creating fm-cp command..."
cat > "$INSTALL_DIR/fm-cp" << 'WRAPPER'
#!/bin/bash
# FM_CP wrapper — runs fm-cp from its isolated venv
exec "$HOME/.fm-cp/venv/bin/fm-cp" "$@"
WRAPPER
chmod +x "$INSTALL_DIR/fm-cp"

# --- Symlink to PATH ---
if [ -d "/usr/local/bin" ]; then
    if [ -L "$BIN_LINK" ] || [ -f "$BIN_LINK" ]; then
        rm -f "$BIN_LINK"
    fi
    ln -s "$INSTALL_DIR/fm-cp" "$BIN_LINK" 2>/dev/null && {
        echo "  ✓ Linked: $BIN_LINK"
    } || {
        echo "  ⚠ Could not link to $BIN_LINK (try: sudo ln -s $INSTALL_DIR/fm-cp $BIN_LINK)"
        echo "    Or add $INSTALL_DIR to your PATH"
    }
else
    echo "  ⚠ /usr/local/bin not found"
    echo "    Add $INSTALL_DIR to your PATH:"
    echo "    export PATH=\"$INSTALL_DIR:\$PATH\""
fi

# --- Verify ---
echo ""
echo "  Verifying installation..."
"$VENV_DIR/bin/fm-cp" --help 2>&1 | head -3
echo ""
echo "============================================================"
echo "  ✓ FM_CP installed successfully!"
echo ""
echo "  Usage:"
echo "    fm-cp -c              # Clipboard auto-detect"
echo "    fm-cp script.txt      # Compose file → FM clipboard"
echo "    fm-cp dump            # Raw clipboard XML dump"
echo ""
echo "  Uninstall:"
echo "    rm -rf ~/.fm-cp /usr/local/bin/fm-cp"
echo "============================================================"
