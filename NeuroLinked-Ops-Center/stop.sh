#!/bin/bash
# ==============================================================================
#   NeuroLinked Ops Center — Arrêt des trois services (Linux/macOS)
# ==============================================================================

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"

echo ""
echo -e "${CYAN} ============================================${NC}"
echo -e "${CYAN}  NEUROLINKED OPS CENTER — ARRÊT${NC}"
echo -e "${CYAN} ============================================${NC}"
echo ""

# Méthode 1 : utiliser les fichiers PID
for SVC_INFO in "brain:Brain" "jarvis:Jarvis" "ops:Ops Center"; do
    SVC="${SVC_INFO%%:*}"
    NAME="${SVC_INFO##*:}"
    PID_FILE="$LOG_DIR/$SVC.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            echo "  Arrêt de $NAME (PID $PID)..."
            kill "$PID" 2>/dev/null || true
            rm -f "$PID_FILE"
        else
            rm -f "$PID_FILE"
        fi
    fi
done

# Méthode 2 : tuer les processus sur les ports (fallback)
sleep 1
for PORT in 8010 8020 8340; do
    PIDS=$(lsof -ti :$PORT 2>/dev/null || true)
    if [ -n "$PIDS" ]; then
        for PID in $PIDS; do
            echo "  Arrêt du processus sur le port $PORT (PID $PID)..."
            kill "$PID" 2>/dev/null || true
        done
    fi
done

sleep 1

# Vérification
echo ""
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

ALL_STOPPED=true
for PORT_INFO in "8010:Ops Center" "8020:Brain" "8340:Jarvis"; do
    PORT="${PORT_INFO%%:*}"
    NAME="${PORT_INFO##*:}"
    if check_port "$PORT"; then
        echo -e "  ${RED}✘${NC} $NAME (:$PORT) encore actif"
        ALL_STOPPED=false
    else
        echo -e "  ${GREEN}✔${NC} $NAME (:$PORT) arrêté"
    fi
done

if [ "$ALL_STOPPED" = true ]; then
    echo ""
    echo -e "${GREEN} Tous les services sont arrêtés.${NC}"
else
    echo ""
    echo -e "${RED} Certains services n'ont pas pu être arrêtés.${NC}"
    echo "  Forcez l'arrêt avec :  pkill -f 'run.py\|server.py' || true"
fi

echo ""
