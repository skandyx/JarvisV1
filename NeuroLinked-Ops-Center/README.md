# NeuroLinked Ops Center — Jarvis V1

Assistant IA local complet — **Jarvis** (voix/texte), le **Brain NeuroLinked** (mémoire neuromorphique persistante + tableau de bord 3D), et un **Ops Center** avec exécuteur d'agents intégré. Les trois services tournent sur votre machine. Rien ne quitte votre ordinateur, à l'exception des appels API que vous configurez.

---

## Ce que vous obtenez

| Service | Port | Description |
|---|---|---|
| **Ops Center** | `8010` | Le tableau de bord quotidien. Calendrier, documents, inbox, et exécuteur d'agents. Ouvrez `http://localhost:8010`. |
| **Brain NeuroLinked** | `8020` | Système de mémoire persistante avec visualisation 3D cérébrale. Jarvis pousse chaque conversation ici. Ouvrez `http://localhost:8020`. |
| **Jarvis** | `8340` | L'assistant voix/texte. Mot d'activation « Hey Jarvis ». Plus de 60 outils intégrés — ouverture d'applications, recherche web, gestion de fichiers, Spotify, vision, etc. Ouvrez `http://localhost:8340`. |

Chaque service est une application Python indépendante. Ils communiquent entre eux via localhost. Pas de Docker, pas de Node, pas de compte cloud nécessaire.

---

## Démarrage rapide (Linux)

### 1. Installation

```bash
cd NeuroLinked-Ops-Center
chmod +x install.sh start.sh stop.sh
./install.sh
```

L'installation prend environ 60 secondes la première fois. Elle crée un environnement virtuel Python (`.venv/`) et installe toutes les dépendances.

### 2. Démarrage

```bash
./start.sh
```

Les trois services se lancent en arrière-plan. Les logs sont dans le dossier `logs/`.

### 3. Configuration (une seule fois)

Ouvrez `http://localhost:8340` → cliquez sur l'icône ⚙ (engrenage) → collez votre clé API.

**Fournisseurs LLM supportés :**

| Fournisseur | Clé API | Où l'obtenir |
|---|---|---|
| **Mistral AI** | `mistral_api_key` | https://console.mistral.ai |
| **Anthropic (Claude)** | `anthropic_api_key` | https://console.anthropic.com |
| **OpenAI (GPT)** | `openai_api_key` | https://platform.openai.com/api-keys |
| **Groq** (gratuit) | `groq_api_key` | https://console.groq.com/keys |
| **xAI (Grok)** | `xai_api_key` | https://console.x.ai |
| **OpenRouter** (multi-modèles) | `openrouter_api_key` | https://openrouter.ai/keys |
| **Z.ai / GLM (ZhipuAI)** | `zai_api_key` | https://open.bigmodel.cn |
| **Ollama** (local, gratuit) | Aucune clé nécessaire | https://ollama.ai |

Laissez le fournisseur sur « Auto-détection » : Jarvis détectera automatiquement la première clé API renseignée (priorité : Mistral → Z.ai → Anthropic → OpenAI → Groq → OpenRouter → xAI → Ollama).

### 4. Arrêt

```bash
./stop.sh
```

---

## Démarrage rapide (Windows)

1. Installez Python 3.11+ depuis https://www.python.org/downloads/ (cochez « Add Python to PATH »).
2. Double-cliquez sur `START.bat`.
3. Ouvrez `http://localhost:8010`.
4. Ajoutez votre clé API via l'icône ⚙.

---

## Démarrage rapide (macOS / Linux — manuel)

```bash
cd NeuroLinked-Ops-Center

# Installer les dépendances
python3 -m pip install --user fastapi uvicorn websockets httpx pyyaml anthropic openai groq psutil cryptography pillow numpy python-multipart

# Lancer les trois services dans trois terminaux :
(cd neurolinked-brain && python3 run.py --port 8020 --host 127.0.0.1)
(cd ops-center/_jarvis && python3 server.py)
(cd ops-center && python3 server.py)

# Ouvrir http://localhost:8010
```

---

## Ce que fait chaque service

### Ops Center (`localhost:8010`)
Le tableau de bord quotidien. Une seule page, pas de connexion. Fonctionnalités :

- **Calendrier** — ajoutez des événements, voyez ce qui arrive
- **Documents** — liste d'accès rapide à vos documents
- **Inbox** — surface unifiée pour les notifications
- **Exécuteur d'agents** — exécute les agents définis dans `ops-center/custom_agents.json`
- **Paramètres (⚙)** — clés API, configuration de Jarvis, thème

### Brain NeuroLinked (`localhost:8020`)
Un stockage de mémoire neuromorphique visualisé comme un cerveau 3D. 10 régions cérébrales, chacune avec des centaines de « neurones » — quand vous ajoutez une note ou avez une conversation, les neurones s'activent et les connexions se renforcent. Avec le temps, le cerveau « apprend » quels sujets sont importants pour vous.

- **Tableau de bord 3D** — voyez le cerveau penser en temps réel
- **Notes** — votre second cerveau, avec recherche
- **Tâches** — suivi de to-do intégré avec Jarvis
- **Conversations** — Jarvis pousse chaque chat ici pour accumuler du contexte

### Jarvis (`localhost:8340`)
Un assistant voix/texte inspiré du J.A.R.V.I.S. de Tony Stark. Écoute « Hey Jarvis » via le microphone (autorisez l'accès dans Chrome la première fois). Ou tapez dans la zone de saisie.

Outils intégrés (sans configuration supplémentaire) :
- **Brain** — recherche, mémorisation, rappel (communique avec le Brain NeuroLinked)
- **Tâches** — ajouter, lister, compléter des tâches
- **Web** — recherche via DuckDuckGo, récupération de pages, résumé
- **Fichiers** — lire, écrire, lister des fichiers dans un dossier de travail
- **Applications** — ouvrir des applications de bureau (Chrome, VS Code, Spotify, etc.) par nom
- **Vision** — capture d'écran, analyse visuelle via LLM
- **Shell** — exécuter des commandes shell (dans un workspace ou sur tout le système)
- **Spotify** — contrôle de lecture si vous fournissez les identifiants Spotify
- **Dev Tools** — lecture/écriture de fichiers, exécution shell, délégation à Claude Code
- **MCP / Plugins / Projets** — installez des serveurs MCP, des plugins/skills, enregistrez des projets locaux avec des agents assignés

---

## Fonctionnalités avancées

### Serveurs MCP (Model Context Protocol)
Installez des serveurs MCP depuis GitHub, une URL ou créez-en depuis des modèles :
- Ouvrez les paramètres (⚙) → section « Serveurs MCP »
- Collez l'URL GitHub du serveur MCP ou choisissez un modèle
- Les serveurs sont automatiquement configurés pour Claude Desktop et Claude Code

### Plugins / Skills
Étendez les capacités de Jarvis avec des plugins installables :
- Depuis GitHub : collez l'URL du dépôt
- Depuis une URL : lien vers une archive ZIP
- Depuis un dossier local : chemin absolu du plugin

### Projets locaux
Enregistrez des dossiers de projets et assignez des agents pour vous aider à coder :
- **Vérificateur de Code** — examine le code et suggère des améliorations
- **Architecte Logiciel** — analyse la structure et propose des designs
- **Assistant Tests** — aide à écrire et exécuter des tests
- **Rédacteur de Documentation** — génère de la documentation
- **Auditeur de Sécurité** — identifie les vulnérabilités
- **Expert Refactoring** — propose des refactorings

---

## Sécurité et confidentialité

- **Les trois services sont liés à `127.0.0.1` uniquement.** Personne sur votre réseau ou sur Internet ne peut y accéder.
- **Protection Host-header** intégrée — bloque les attaques par DNS-rebinding.
- **Zéro télémétrie** — aucun analytic, aucun phone-home, aucun suivi d'utilisation. Le seul trafic réseau sortant est les appels API que VOUS configurez.
- **Vos données restent sur votre machine.** La mémoire du Brain est dans `neurolinked-brain/brain_state/` — fichiers JSON locaux. L'historique de chat de Jarvis est dans `ops-center/_jarvis/sessions/`.

---

## Dépannage

**« Python n'est pas installé »**
Installez Python 3.10+ et assurez-vous que `python3` est dans votre PATH. Sur Ubuntu/Debian : `sudo apt install python3 python3-pip python3-venv`

**Un service ne démarre pas**
Consultez les logs :
```bash
tail -f logs/brain.log
tail -f logs/jarvis.log
tail -f logs/ops.log
```
Causes courantes :
- Port déjà utilisé → lancez `./stop.sh`, attendez 5 secondes, puis `./start.sh`
- Paquet Python manquant → relancez `./install.sh`

**Le Brain ne démarre pas sur Linux (erreur pygetwindow)**
C'est corrigé dans cette version. Si le problème persiste, vérifiez que `pygetwindow` n'est PAS installé dans votre venv : `pip uninstall pygetwindow`

**« J'ai ajouté une clé API mais rien ne se passe »**
- Laissez le fournisseur sur « Auto-détection » dans les paramètres
- Rafraîchissez la page
- Vérifiez les logs pour les erreurs
- Testez votre clé sur le playground du fournisseur

**Jarvis ne m'entend pas**
Ouvrez `localhost:8340` directement dans Chrome. Chrome vous demandera l'autorisation du micro — cliquez Autoriser.

**Le tableau de bord du Brain est vide**
C'est normal au premier démarrage. Le cerveau apprend au fur et à mesure. Lancez quelques agents, ayez quelques conversations, puis revenez vérifier.

**Réinitialisation complète**
Supprimez `neurolinked-brain/brain_state/` et `ops-center/_jarvis/sessions/`. Au prochain démarrage, tout sera réinitialisé.

---

## Licence

MIT. Faites ce que vous voulez avec.
