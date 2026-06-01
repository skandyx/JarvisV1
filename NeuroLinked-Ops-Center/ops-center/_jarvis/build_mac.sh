#!/bin/bash
# Build a protected macOS NeuroLinked bundle via PyInstaller.
# Run this on a Mac (PyInstaller can't cross-compile). Output: dist/NeuroLinked/

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  NeuroLinked â€” macOS PyInstaller Build${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# Detect python
if command -v python3 &> /dev/null; then
    PY=python3
    PIP=pip3
else
    PY=python
    PIP=pip
fi

# Ensure PyInstaller is installed
if ! $PY -c "import PyInstaller" 2>/dev/null; then
    echo "[1/3] Installing PyInstaller..."
    $PIP install --quiet pyinstaller
else
    echo "[1/3] PyInstaller already installed."
fi

# Clean prior build
echo "[2/3] Cleaning prior build/dist..."
rm -rf build dist NeuroLinked.spec 2>/dev/null || true

# Build
echo "[3/3] Building (this takes 2â€“5 min)..."
echo ""
$PY -m PyInstaller \
    --name NeuroLinked \
    --onedir \
    --windowed \
    --noconfirm \
    --add-data "dashboard:dashboard" \
    --hidden-import uvicorn.logging \
    --hidden-import uvicorn.loops \
    --hidden-import uvicorn.loops.auto \
    --hidden-import uvicorn.protocols \
    --hidden-import uvicorn.protocols.http \
    --hidden-import uvicorn.protocols.http.auto \
    --hidden-import uvicorn.protocols.websockets \
    --hidden-import uvicorn.protocols.websockets.auto \
    --hidden-import uvicorn.lifespan \
    --hidden-import uvicorn.lifespan.on \
    --hidden-import fastapi \
    --hidden-import brain \
    --hidden-import brain.brain \
    --hidden-import brain.config \
    --hidden-import brain.regions \
    --hidden-import brain.neurons \
    --hidden-import brain.synapses \
    --hidden-import brain.safety \
    --hidden-import brain.persistence \
    --hidden-import brain.knowledge_store \
    --hidden-import brain.claude_bridge \
    --hidden-import brain.screen_observer \
    --hidden-import brain.video_recorder \
    --hidden-import sensory \
    --hidden-import mss \
    --hidden-import cv2 \
    --hidden-import PIL \
    --hidden-import scipy \
    --hidden-import scipy.sparse \
    --hidden-import scipy.sparse.csgraph \
    run.py

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  BUILD COMPLETE${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  Binary:       dist/NeuroLinked/NeuroLinked"
echo "  App bundle:   dist/NeuroLinked/"
echo ""
echo "  Run it:       cd dist/NeuroLinked && ./NeuroLinked"
echo ""
echo "  Before shipping to users, copy dashboard/ next to the binary"
echo "  and add a start.sh that opens the browser."
echo ""
