#!/bin/bash
# ==============================================================================
#   NeuroLinked Ops Center — Démarrage des trois services (Linux/macOS)
#   Brain (8020), Jarvis (8340), Ops Center (8010)
# ==============================================================================

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
LOG_DIR="$SCRIPT_DIR/logs"

# Vérifier que l'installation a été faite
if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo -e "${RED}  ERREUR : Environnement virtuel introuvable.${NC}"
    echo "  Exécutez d'abord :  ./install.sh"
    echo ""
    exit 1
fi

source "$VENV_DIR/bin/activate"
mkdir -p "$LOG_DIR"

# Jeton partagé
TOKEN_FILE="$SCRIPT_DIR/neurolinked-brain/.launch-token"
if [ -f "$TOKEN_FILE" ]; then
    export NEUROLINKED_TOKEN="$(cat "$TOKEN_FILE" | tr -d '[:space:]')"
else
    export NEUROLINKED_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    echo "$NEUROLINKED_TOKEN" > "$TOKEN_FILE"
fi

echo ""
echo -e "${CYAN} ============================================${NC}"
echo -e "${CYAN}  NEUROLINKED OPS CENTER — DÉMARRAGE${NC}"
echo -e "${CYAN} ============================================${NC}"
echo ""

# ---- Lancer le Brain (port 8020) ----
echo -e "${YELLOW}🧠${NC} Lancement du Brain (port 8020)..."
cd "$SCRIPT_DIR/neurolinked-brain"
python3 run.py --port 8020 --host 127.0.0.1 > "$LOG_DIR/brain.log" 2>&1 &
BRAIN_PID=$!
cd "$SCRIPT_DIR"

# Attendre que le Brain soit prêt
sleep 3

# ---- Lancer Jarvis (port 8340) ----
echo -e "${YELLOW}🤖${NC} Lancement de Jarvis (port 8340)..."
cd "$SCRIPT_DIR/ops-center/_jarvis"
python3 server.py > "$LOG_DIR/jarvis.log" 2>&1 &
JARVIS_PID=$!
cd "$SCRIPT_DIR"

# Attendre que Jarvis soit prêt
sleep 2

# ---- Lancer l'Ops Center (port 8010) ----
echo -e "${YELLOW}🖥${NC}  Lancement de l'Ops Center (port 8010)..."
cd "$SCRIPT_DIR/ops-center"
python3 server.py > "$LOG_DIR/ops.log" 2>&1 &
OPS_PID=$!
cd "$SCRIPT_DIR"

# Attendre un peu puis vérifier les services
sleep 3

check_port() {
    python3 -c "
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
    s.connect(('127.0.0.1', $1))
    s.close()
    exit(0)
except:
    exit(1)
" 2>/dev/null
}

echo ""
echo -e "${CYAN} Vérification des services...${NC}"

for PORT_INFO in "8010:Ops Center" "8020:Brain" "8340:Jarvis"; do
    PORT="${PORT_INFO%%:*}"
    NAME="${PORT_INFO##*:}"
    if check_port "$PORT"; then
        echo -e "  ${GREEN}✔${NC} Service sur :$PORT ($NAME) actif"
    else
        echo -e "  ${RED}✘${NC} Service sur :$PORT ($NAME) ne répond pas (vérifie logs/)"
    fi
done

echo ""
echo -e "${GREEN} ============================================${NC}"
echo -e "${GREEN}  SERVICES LANCÉS${NC}"
echo -e "${GREEN} ============================================${NC}"
echo ""
echo "  Ops Center :   http://localhost:8010"
echo "  Brain :        http://localhost:8020"
echo "  Jarvis :       http://localhost:8340"
echo ""
echo "  Logs :"
echo "    tail -f $LOG_DIR/brain.log"
echo "    tail -f $LOG_DIR/jarvis.log"
echo "    tail -f $LOG_DIR/ops.log"
echo ""
echo "  Pour arrêter :  ./stop.sh"
echo ""

# Sauvegarder les PIDs pour stop.sh
echo "$BRAIN_PID" > "$LOG_DIR/brain.pid"
echo "$JARVIS_PID" > "$LOG_DIR/jarvis.pid"
echo "$OPS_PID" > "$LOG_DIR/ops.pid"
