#!/bin/bash
# ==============================================================================
#   NeuroLinked Ops Center — Installation Linux
#   Configure l'environnement Python, installe les dépendances,
#   et prépare les trois services (Brain, Jarvis, Ops Center).
# ==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${CYAN} ============================================${NC}"
echo -e "${CYAN}  NEUROLINKED OPS CENTER${NC}"
echo -e "${CYAN}  Installation Linux${NC}"
echo -e "${CYAN} ============================================${NC}"
echo ""

# ---- Step 1: Check Python ----
echo -e "${YELLOW}[1/6]${NC} Vérification de Python..."
if command -v python3 &> /dev/null; then
    PYVER=$(python3 --version 2>&1)
    echo "       Python trouvé : $PYVER"
    PY=python3
    PIP=pip3
elif command -v python &> /dev/null; then
    PYVER=$(python --version 2>&1)
    echo "       Python trouvé : $PYVER"
    PY=python
    PIP=pip
else
    echo ""
    echo -e "${RED}  ERREUR : Python n'est pas installé.${NC}"
    echo ""
    echo "  Installez Python 3.10 ou plus récent :"
    echo "    sudo apt install python3 python3-pip python3-venv"
    echo ""
    exit 1
fi

# ---- Step 2: Create virtual environment ----
echo ""
echo -e "${YELLOW}[2/6]${NC} Création de l'environnement virtuel Python..."
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    $PY -m venv "$VENV_DIR"
    echo "       Environnement virtuel créé dans .venv/"
else
    echo "       Environnement virtuel déjà existant."
fi
source "$VENV_DIR/bin/activate"
PIP_CMD="$VENV_DIR/bin/pip"
PY_CMD="$VENV_DIR/bin/python"

# ---- Step 3: Install core dependencies ----
echo ""
echo -e "${YELLOW}[3/6]${NC} Installation des dépendances principales..."
echo "       fastapi, uvicorn, websockets, httpx, anthropic, openai, groq..."
$PIP_CMD install --quiet --upgrade pip 2>/dev/null || true
$PIP_CMD install --quiet fastapi "uvicorn[standard]" websockets httpx pyyaml anthropic openai groq psutil cryptography pillow numpy python-multipart 2>/dev/null
echo "       Dépendances principales installées."

# ---- Step 4: Install Brain dependencies ----
echo ""
echo -e "${YELLOW}[4/6]${NC} Installation des dépendances du Brain..."
$PIP_CMD install --quiet numpy scipy 2>/dev/null
$PIP_CMD install --quiet Pillow 2>/dev/null
$PIP_CMD install --quiet mss 2>/dev/null
echo "       Pillow + mss installés (capture écran)"

# Optionnel : OCR
$PIP_CMD install --quiet pytesseract 2>/dev/null
echo "       pytesseract installé (OCR — nécessite aussi : sudo apt install tesseract-ocr)"

# pygetwindow ne fonctionne PAS sur Linux
echo "       pygetwindow ignoré (non supporté sur Linux — pas d'impact sur le fonctionnement)"

# ---- Step 5: Create required directories ----
echo ""
echo -e "${YELLOW}[5/6]${NC} Création des répertoires..."
mkdir -p "$SCRIPT_DIR/neurolinked-brain/brain_state"
mkdir -p "$SCRIPT_DIR/ops-center/_jarvis/sessions"
mkdir -p "$SCRIPT_DIR/ops-center/_jarvis/brain_storage"
mkdir -p "$SCRIPT_DIR/logs"
echo "       Répertoires créés."

# ---- Step 6: Generate shared launch token ----
echo ""
echo -e "${YELLOW}[6/6]${NC} Génération du jeton de lancement partagé..."
TOKEN_FILE="$SCRIPT_DIR/neurolinked-brain/.launch-token"
if [ ! -f "$TOKEN_FILE" ]; then
    $PY_CMD -c "import secrets; open('$TOKEN_FILE','w').write(secrets.token_urlsafe(32))"
    echo "       Jeton généré."
else
    echo "       Jeton déjà existant."
fi

echo ""
echo -e "${GREEN} ============================================${NC}"
echo -e "${GREEN}  INSTALLATION TERMINÉE !${NC}"
echo -e "${GREEN} ============================================${NC}"
echo ""
echo "  Pour démarrer les services :"
echo "    ./start.sh"
echo ""
echo "  Pour les arrêter :"
echo "    ./stop.sh"
echo ""
echo "  Après le démarrage, ouvrez :"
echo "    http://localhost:8010  (Ops Center)"
echo "    http://localhost:8020  (Brain)"
echo "    http://localhost:8340  (Jarvis)"
echo ""
echo -e "${GREEN} ============================================${NC}"
