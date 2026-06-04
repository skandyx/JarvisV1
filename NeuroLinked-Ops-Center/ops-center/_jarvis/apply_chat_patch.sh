#!/bin/bash
# apply_chat_patch.sh — Ajoute le panel chat texte à Z.E.R.O.
# Usage : bash apply_chat_patch.sh
# Depuis le dossier : NeuroLinked-Ops-Center/ops-center/_jarvis/

set -e
FRONTEND="frontend"
CSS="$FRONTEND/style.css"
JS="$FRONTEND/main.js"
HTML="$FRONTEND/index.html"

echo "── Vérification des fichiers ──────────────────────"
[ -f "$CSS" ]  || { echo "ERREUR : $CSS introuvable"; exit 1; }
[ -f "$JS" ]   || { echo "ERREUR : $JS introuvable"; exit 1; }
[ -f "$HTML" ] || { echo "ERREUR : $HTML introuvable"; exit 1; }
echo "  ✓ frontend/ trouvé"

echo "── Backup ─────────────────────────────────────────"
cp "$CSS"  "$CSS.bak"
cp "$JS"   "$JS.bak"
cp "$HTML" "$HTML.bak"
echo "  ✓ .bak créés"

echo "── Patch style.css ────────────────────────────────"
if grep -q "chat-toggle" "$CSS"; then
    echo "  ⚠ chat_patch.css déjà présent — skip"
else
    cat chat_patch.css >> "$CSS"
    echo "  ✓ chat_patch.css ajouté"
fi

echo "── Patch main.js ──────────────────────────────────"
if grep -q "chat-panel" "$JS"; then
    echo "  ⚠ chat_patch.js déjà présent — skip"
else
    cat chat_patch.js >> "$JS"
    echo "  ✓ chat_patch.js ajouté"
fi

echo "── Patch index.html (cache-bust) ──────────────────"
# Met à jour le ?v= de style.css pour forcer le rechargement
sed -i 's/style\.css?v=[a-z0-9]*/style.css?v=chat1/g' "$HTML"
echo "  ✓ cache-bust appliqué"

echo ""
echo "  ✅ Patch terminé !"
echo "  → Redémarre Jarvis puis ouvre http://localhost:8340"
echo "  → Le bouton ⌨ CHAT apparaît en bas à gauche"
echo ""
echo "  Pour rollback :"
echo "    cp frontend/style.css.bak frontend/style.css"
echo "    cp frontend/main.js.bak   frontend/main.js"
echo "    cp frontend/index.html.bak frontend/index.html"
