# Jarvis — Assistant Vocal (Édition Premium)

Un assistant IA voix/texte avec contrôle complet de l'ordinateur, automatisation
de navigateur, support multi-LLM, et une connexion live au **NeuroLinked Brain**
pour une mémoire neurale persistante.

> Parlez-lui. Il écoute. Il peut lire vos fichiers, exécuter des commandes
> shell, contrôler votre souris, piloter votre navigateur, prendre des captures
> d'écran avec vision, et se souvenir de tout. **Aucune clé API requise** pour
> démarrer — il est livré avec une voix navigateur gratuite et prend en charge
> les LLM locaux via Ollama.

---

## Ce qu'il fait

| Fonctionnalité | Module |
|---|---|
| 🎤 Voix entrée/sortie — **voix navigateur gratuite par défaut**, ElevenLabs premium en option | `server.py` + `frontend/settings.js` |
| 🧩 **Support multi-LLM** — Claude, GPT, Groq, Ollama (local/gratuit), xAI Grok, Mistral AI, OpenRouter — permutation à chaud dans les Paramètres | `llm_providers.py` |
| ⚙️ **UI Paramètres intégrée** (icône engrenage) — collez vos propres clés, aucune édition de fichier de config nécessaire | `frontend/settings.js` |
| 🧠 Mémoriser / rappeler / tâches (fichiers `.md` locaux + double-écriture NeuroLink) | `brain_tools.py` + `neurolink_bridge.py` |
| 💻 Exécution shell (à l'échelle du système, pas sandboxé) | `dev_tools.py` → `run_shell` |
| 📂 Lecture / écriture / ajout / recherche de fichiers (n'importe où sur le disque) | `dev_tools.py` |
| 🖱️ Contrôle souris + clavier + fenêtres | `computer_tools.py` |
| 🌐 Automatisation de navigateur (Playwright Chromium) | `browser_tools.py` |
| 📸 Vision d'écran (capture d'écran + LLM vision) | `screen_capture.py` |
| 👁️ Vision webcam (auto-attachée à chaque message) | Intégré au serveur |
| 👏 Déclenchement par double claquement (optionnel) | `scripts/clap-trigger.py` |
| 🔌 Serveur MCP (Claude Desktop / Code / Cursor) | `brain_mcp.py` |

---

## Fournisseurs LLM supportés

Jarvis prend en charge **7 fournisseurs LLM**, tous interchangeables à chaud
depuis l'interface Paramètres :

| Fournisseur | Modèle par défaut | Clé requise | Obtenir une clé |
|---|---|---|---|
| **Anthropic** (Claude) | `claude-haiku-4-5-20251001` | Oui | https://console.anthropic.com |
| **OpenAI** (GPT) | `gpt-4o-mini` | Oui | https://platform.openai.com/api-keys |
| **Groq** | `llama-3.3-70b-versatile` | Oui (gratuit) | https://console.groq.com/keys |
| **xAI** (Grok) | `grok-2-latest` | Oui | https://x.ai/api |
| **Mistral AI** | `mistral-large-latest` | Oui | https://console.mistral.ai |
| **OpenRouter** (multi-modèles) | `openai/gpt-4o-mini` | Oui | https://openrouter.ai/keys |
| **Ollama** (local) | `llama3.1:8b` | Non | https://ollama.com |

Vous pouvez surcharger le modèle par défaut dans les paramètres en renseignant
le champ « Model ». Exemples : `mistral-small-latest`, `openai/gpt-4o`,
`deepseek/deepseek-chat` (via OpenRouter), `claude-sonnet-4-20250514`, etc.

---

## Prérequis

- **Python 3.10+** (Windows) — https://python.org/downloads
- **Google Chrome** (pour l'automatisation de navigateur Playwright)
- **Une clé LLM** (choisissez-en une) — ou lancez Ollama localement pour un fonctionnement 100% gratuit
    - Anthropic — https://console.anthropic.com
    - OpenAI — https://platform.openai.com
    - Groq (rapide, offre gratuite) — https://console.groq.com
    - xAI Grok — https://x.ai/api
    - Mistral AI — https://console.mistral.ai
    - OpenRouter (multi-modèles) — https://openrouter.ai
    - Ollama (local, gratuit) — https://ollama.com
- **Clé ElevenLabs** (optionnel, pour la voix premium) — https://elevenlabs.io
    - Sans elle, Jarvis utilise la **voix navigateur gratuite** automatiquement

---

## Installation (Windows)

```cmd
install.bat
```

C'est tout. L'installateur va :

1. Vérifier que Python est installé
2. `pip install -r requirements.txt`
3. `playwright install chromium`
4. Vous demander les clés API + votre nom + ville → écrit `config.json`
5. Détecter le NeuroLinked Brain à `http://localhost:8000` et câbler automatiquement la connexion

Après l'installation, double-cliquez sur **`start.bat`** pour lancer.

---

## Connexion automatique au NeuroLinked Brain

Jarvis se connecte automatiquement à un serveur **NeuroLinked Brain** en cours
d'exécution à `http://localhost:8000` au démarrage.

- ✅ **Brain en cours d'exécution** → Chaque `remember` / `recall` écrit en
  double dans les fichiers `.md` locaux *et* la mémoire neurale du Brain. Les
  requêtes de rappel fusionnent les résultats.
- ⚠️ **Brain non démarré** → Jarvis bascule sur la mémoire locale uniquement.
  Un observateur retente toutes les 30 secondes — dès que le Brain est en
  ligne, Jarvis se reconnecte automatiquement. Rien ne casse.

Pour changer l'URL, éditez `config.json` :

```json
{
  "neurolink_url": "http://localhost:8000",
  "auto_connect_neurolink": true
}
```

---

## Configuration vocale

1. Le navigateur demande la permission du microphone → cliquez **Autoriser**
2. **Maintenez ESPACE** pour parler, relâchez pour envoyer. Ou cliquez sur l'orbe.
3. La permission webcam est également demandée — Jarvis vous voit. Une image est
   attachée automatiquement à chaque message.
4. Sans clés ElevenLabs, Jarvis bascule en mode texte uniquement silencieux.

---

## Démarrage de l'assistant

```cmd
start.bat
```

Puis ouvrez :
```
http://localhost:8340
```

Le port peut être changé dans `server.py` si 8340 est pris.

---

## Avancé

### MCP (Claude Desktop / Code / Cursor)

`brain_mcp.py` expose les outils brain de Jarvis comme serveur MCP pour que
n'importe quel client MCP puisse accéder à la même mémoire.

Ajoutez à votre config Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json`) :

```json
{
  "mcpServers": {
    "jarvis-brain": {
      "command": "python",
      "args": ["C:\\chemin\\vers\\jarvis\\brain_mcp.py"]
    }
  }
}
```

### Déclenchement par claquement

Exécutez `python scripts/clap-trigger.py` en arrière-plan. Deux claquements
lancent votre espace de travail complet (Spotify + VS Code + navigateur +
Jarvis). Éditez le script pour personnaliser.

---

## Structure des fichiers

```
jarvis/
├── install.bat              # Installateur en un clic (exécuter en premier)
├── start.bat                # Lancer Jarvis
├── config.json              # Vos clés API + préférences
├── config.example.json      # Modèle
├── server.py                # Backend FastAPI (Claude + voix)
├── llm_providers.py         # Couche multi-LLM (7 fournisseurs)
├── brain_tools.py           # Tâches, mémoire, notes (fichiers .md)
├── neurolink_bridge.py      # Connexion auto au NeuroLinked Brain
├── brain_mcp.py             # Serveur MCP (pour les autres clients IA)
├── dev_tools.py             # Fichiers + shell + hooks Claude Code
├── computer_tools.py        # Souris, clavier, fenêtres
├── browser_tools.py         # Contrôle navigateur Playwright
├── screen_capture.py        # Capture d'écran + vision
├── requirements.txt
├── frontend/
│   ├── index.html
│   ├── main.js
│   ├── settings.js
│   └── style.css
└── scripts/
    ├── clap-trigger.py
    └── launch-jarvis.ps1
```

---

## Dépannage

**« Python introuvable »** → Installez Python 3.10+ avec « Add to PATH » coché.

**« pip install a échoué »** → Exécutez `python -m pip install --upgrade pip` puis réessayez.

**« playwright install a échoué »** → Problème réseau. Réessayez : `python -m playwright install chromium`.

**Le microphone ne fonctionne pas** → Vérifiez les permissions micro du navigateur pour `localhost:8340`.

**Pas de sortie vocale** → Clé ElevenLabs manquante ou invalide. Jarvis fonctionne toujours en mode texte uniquement.

**NeuroLink indique « non joignable »** → Démarrez le NeuroLinked Brain (port 8000). Jarvis se reconnecte automatiquement dans les 30 secondes.

**Port 8340 déjà utilisé** → Éditez la dernière ligne de `server.py` pour le changer.

---

## Licence

Publié sous MIT — forkez-le, modifiez-le, livrez-le.
