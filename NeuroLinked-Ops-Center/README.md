# NeuroLinked Ops Center

Un stack complet d'assistant IA local-first — **Jarvis** (IA voix/texte), le
**NeuroLinked Brain** (mémoire neuromorphique persistante + tableau de bord 3D),
et un tableau de bord **Ops Center** avec un exécuteur d'agents intégré. Les
trois services tournent sur votre machine. Rien ne quitte votre ordinateur, à
l'exception des appels API que vous choisissez d'effectuer.

Ceci est l'instantané **v1 hérité** — ce qui alimentait les opérations internes
du mainteneur avant la migration vers un système plus récent. Ça fonctionne.
C'est à vous.

> Construit et publié pour la communauté. Utilisez-le. Modifiez-le. Livrez-le.
> Aucun support fourni — lisez d'abord la section dépannage.

---

## Ce que vous obtenez

| Service | Port | Description |
|---|---|---|
| **Ops Center** | `8010` | Le tableau de bord que vous ouvrez chaque jour. Calendrier, documents, boîte de réception et exécuteur d'agents. Ouvrir sur `http://localhost:8010`. |
| **NeuroLinked Brain** | `8020` | Un système de mémoire persistante sous forme de visualisation 3D d'un cerveau. Jarvis fait passer chaque conversation ici. Vous pouvez lui parler directement. Ouvrir sur `http://localhost:8020`. |
| **Jarvis** | `8340` | L'assistant voix/texte. Mot d'activation « Hey Jarvis ». Dispose de plus de 60 outils intégrés — ouverture d'applications, recherche web, gestion de fichiers, CRM GHL, Spotify, vision, etc. Ouvrir sur `http://localhost:8340`. |

Chaque service est un service Python autonome. Ils communiquent entre eux via
localhost. Pas de Docker, pas de Node, pas de compte cloud requis.

---

## Démarrage rapide en 60 secondes (Windows)

1. **Installez Python 3.11 ou plus récent.**
   Téléchargez-le depuis https://www.python.org/downloads/.
   **CRITIQUE :** cochez la case « Add Python to PATH » lors de l'installation.

2. **Double-cliquez sur `START.bat`** dans ce dossier.
   Le premier lancement prend environ 60 secondes pendant l'installation des
   paquets Python. Par la suite, les démarrages prendront environ 15 secondes.

3. **Votre navigateur s'ouvre sur `http://localhost:8010`.** C'est l'Ops Center.

4. **Ajoutez une clé API (une seule fois).**
   Cliquez sur l'icône engrenage (en bas à droite) → collez une clé API
   Anthropic, OpenAI, Groq, Mistral, xAI ou OpenRouter → Enregistrer.
   - Anthropic : https://console.anthropic.com — obtenez une clé `sk-ant-...`
   - OpenAI : https://platform.openai.com/api-keys — obtenez une clé `sk-...`
   - Groq (offre gratuite disponible) : https://console.groq.com/keys — obtenez une clé `gsk_...`
   - xAI (Grok) : https://x.ai/api — obtenez une clé `xai-...`
   - Mistral AI : https://console.mistral.ai — obtenez votre clé API
   - OpenRouter (multi-modèles) : https://openrouter.ai/keys — obtenez une clé `sk-or-...`
   - **Ou :** installez Ollama (https://ollama.ai) pour un LLM local gratuit, aucune clé nécessaire.

5. **Testez.** Allez dans la section Agent Runner → cliquez `Run` sur l'agent
   pré-installé `Finance Watchdog`. Il exercera le brain + LLM et vous verrez
   le résultat en quelques secondes.

C'est tout. Vous avez maintenant un stack IA ops local entièrement fonctionnel.

---

## Démarrage rapide (macOS / Linux)

```bash
# Ouvrez le Terminal, cd dans ce dossier
cd ~/Desktop/NeuroLinked-Ops-Center   # ou là où vous l'avez placé

# Installez les dépendances Python (une seule fois)
python3 -m pip install --user fastapi uvicorn websockets httpx pyyaml anthropic openai groq psutil cryptography pillow numpy python-multipart

# Démarrez les trois services dans trois onglets de terminal :
(cd neurolinked-brain && python3 run.py --port 8020 --host 127.0.0.1)
(cd ops-center/_jarvis && python3 server.py)
(cd ops-center && python3 server.py)

# Ouvrez http://localhost:8010
```

(Ou transformez `START.bat` en script `.sh` — même principe.)

---

## Ce que fait chaque service

### Ops Center (`localhost:8010`)
Le tableau de bord quotidien. Page unique, pas de connexion. Fonctionnalités :

- **Calendrier** — ajoutez des événements, consultez ce qui arrive
- **Documents** — liste de documents en accès rapide, collez vos liens
- **Boîte de réception** — une surface unifiée pour les notifications type Slack/e-mail
- **Agent Runner** — exécute les agents définis dans `ops-center/custom_agents.json`
- **Paramètres (icône engrenage)** — clés API, config Jarvis, thème

### NeuroLinked Brain (`localhost:8020`)
Un stockage mémoire neuromorphique visualisé comme un cerveau 3D. 10 régions
cérébrales, chacune avec des centaines de « neurones » — lorsque vous ajoutez
une note ou avez une conversation, les neurones s'activent et les connexions se
renforcent. Au fil du temps, le cerveau « apprend » quels sujets comptent pour
vous.

- **Tableau de bord 3D** — voyez le cerveau penser en temps réel
- **Notes** — vos notes second cerveau, recherchables
- **Tâches** — suivi de to-do intégré à Jarvis
- **Conversations** — Jarvis fait passer chaque discussion ici pour que le
  cerveau accumule du contexte. Demandez-lui des semaines plus tard « qu'avons-
  nous décidé à propos de X ? »

### Jarvis (`localhost:8340`)
Un assistant voix/texte inspiré du J.A.R.V.I.S. de Tony Stark. Écoute
« Hey Jarvis » via votre microphone (vous devrez accorder la permission micro
dans Chrome la première fois). Ou tapez dans la zone de saisie.

Outils intégrés (sans configuration supplémentaire) :
- **Brain** — recherche, mémorisation, rappel (communique avec le NeuroLinked Brain)
- **Tâches** — ajouter, lister, compléter des tâches
- **Web** — recherche via DuckDuckGo, récupération de pages, résumé
- **Fichiers** — lire, écrire, lister les fichiers dans un dossier workspace
- **Applications** — ouvrir des applications de bureau (Chrome, VS Code, Spotify, etc.) par nom
- **Vision** — `regarde cet écran` (prend une capture d'écran, envoie au LLM avec vision)
- **Shell** — exécuter des commandes shell (dans un workspace, ou à l'échelle du système si activé)
- **Spotify** — contrôler la lecture si vous fournissez les identifiants Spotify
- **GHL (GoHighLevel CRM)** — récupérer des contacts, envoyer des messages, déclencher des workflows si vous avez un compte GHL

Ouvrez `localhost:8340` directement, ou utilisez le panneau de chat qui
apparaît dans l'Ops Center.

---

## Fournisseurs LLM supportés

Jarvis prend en charge **7 fournisseurs LLM**, tous interchangeables à chaud
depuis les paramètres (icône engrenage) :

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
le champ « Model ». Par exemple : `mistral-small-latest`, `openai/gpt-4o`,
`deepseek/deepseek-chat` (via OpenRouter), etc.

**Aucune clé n'est obligatoire.** Avec Ollama installé localement, tout le
stack fonctionne gratuitement.

---

## Ajouter vos propres agents

Les agents sont définis dans `ops-center/custom_agents.json`. Le fichier est
livré avec deux exemples (`Finance Watchdog`, `Meeting Prep Pro`). Ajoutez-en
un nouveau :

```json
{
  "mon_qualificateur_leads": {
    "id": "mon_qualificateur_leads",
    "name": "Qualificateur de Leads",
    "description": "Lit les nouveaux leads, les note, rédige une réponse.",
    "steps": [
      { "type": "brain_search", "inputs": { "query": "nouveaux leads cette semaine" } },
      { "type": "reason",       "inputs": { "prompt": "Notez chaque lead de 1 à 10 sur la pertinence. Brève justification." } },
      { "type": "draft_email",  "inputs": {
          "to": "leads@votreentreprise.com",
          "subject": "Notes des leads — cette semaine",
          "notes": "Utilisez le résultat de notation de l'étape précédente."
      }},
      { "type": "notify", "inputs": { "channel": "slack", "message": "Notes des leads prêtes." } }
    ],
    "created_at": "2026-01-01T00:00:00",
    "enabled": true
  }
}
```

Enregistrez → rafraîchissez l'Ops Center → votre nouvel agent apparaît. Cliquez
`Run`.

**Types d'étapes disponibles :** `brain_search`, `reason`, `draft_email`,
`notify`, `create_task`, `summarize`, `call_api`.

---

## Intégrations optionnelles

Vous pouvez brancher n'importe laquelle. Ouvrez l'icône engrenage dans
l'Ops Center, collez votre clé, enregistrez.

| Intégration | Ce que ça débloque | Où obtenir une clé |
|---|---|---|
| Anthropic | Meilleur LLM généraliste (Claude) | https://console.anthropic.com |
| OpenAI | GPT-4o, images DALL·E, Whisper | https://platform.openai.com |
| Groq | Offre gratuite, LLM ultra-rapide | https://console.groq.com |
| xAI (Grok) | Modèles Grok | https://x.ai/api |
| Mistral AI | Modèles Mistral (open source + propriétaires) | https://console.mistral.ai |
| OpenRouter | Accès à des centaines de modèles via une seule API | https://openrouter.ai |
| ElevenLabs | Voix premium (Jarvis parle mieux) | https://elevenlabs.io |
| GoHighLevel | CRM, contacts, SMS, workflows | Votre sous-compte GHL → Paramètres → Jeton d'intégration privée |
| Spotify | Contrôle musical via Jarvis | https://developer.spotify.com |
| Slack | Canal de notifications | Paramètres du workspace → Apps → Incoming Webhooks |

**Aucune n'est requise.** Avec une seule clé LLM (ou Ollama en local), tout le
stack fonctionne.

---

## Sécurité et confidentialité

- **Les trois services se lient uniquement à `127.0.0.1`.** Personne sur votre
  réseau ou sur Internet ne peut les atteindre. Ne changez pas cela sauf si
  vous savez exactement ce que vous faites.
- **Protection d'en-tête Host** intégrée — bloque les attaques de DNS-rebinding
  même sur la machine locale.
- **Zéro télémétrie** — pas d'analytics, pas de phone-home, pas de suivi
  d'utilisation. Le seul trafic réseau sortant est constitué des appels API que
  VOUS configurez (par ex. quand Jarvis appelle Anthropic ou récupère un
  contact GHL).
- **Vos données restent sur votre machine.** La mémoire du brain vit dans
  `neurolinked-brain/brain_state/` — de purs fichiers JSON locaux. L'historique
  de chat Jarvis vit dans `ops-center/_jarvis/sessions/` — même principe.

---

## Dépannage

**« Python n'est pas installé ou pas dans le PATH »**
Réinstallez Python depuis python.org et **assurez-vous de cocher la case
« Add Python to PATH »** lors de l'installation. Puis relancez `START.bat`.

**Un des services refuse de démarrer**
Ouvrez la fenêtre de terminal minimisée pour ce service (Brain / Jarvis /
OpsCenter dans votre barre des tâches). Lisez l'erreur. Causes les plus
courantes :
- Port déjà utilisé → exécutez `STOP.bat`, attendez 5 secondes, puis `START.bat` à nouveau
- Paquet Python manquant → exécutez manuellement :
  `python -m pip install --user fastapi uvicorn websockets httpx pyyaml anthropic openai groq cryptography`

**Jarvis ne m'entend pas**
Ouvrez `localhost:8340` directement dans Chrome. Chrome demandera la
permission du micro — cliquez Autoriser. Si vous l'avez précédemment refusée,
cliquez sur l'icône caméra/micro dans la barre d'adresse → réinitialisez les
permissions.

**« J'ai ajouté une clé API mais rien ne se passe »**
- Rafraîchissez le tableau de bord
- Vérifiez la fenêtre de terminal du service concerné — les erreurs s'y affichent
- Assurez-vous que votre clé est valide en la testant sur le playground du fournisseur

**Le tableau de bord du brain à `localhost:8020` est vide**
C'est normal au premier démarrage. Le cerveau apprend au fur et à mesure de
votre utilisation. Lancez quelques agents, ayez quelques conversations avec
Jarvis, puis revenez vérifier dans une journée.

**Je veux tout effacer et recommencer**
Supprimez `neurolinked-brain/brain_state/` et `ops-center/_jarvis/sessions/`.
Au prochain démarrage, tout s'initialisera à zéro.

---

## Ce que ce n'est PAS

- **Pas un produit successeur.** Le mainteneur a construit un produit
  successeur avec un constructeur visuel d'agents, des jobs planifiés, un flux
  en temps réel, des managers par rôle et une UI soignée. Ceci est le
  prédécesseur — une v1 plus simple qui préexiste à tout cela. Ça fonctionne
  pour un usage personnel et de petites ops ; ce n'est pas testé en condition
  réelle pour des équipes.
- **Pas multi-utilisateur.** Une personne, une machine. Pas de connexion, pas
  de permissions.
- **Pas supporté.** Les forks sont bienvenus. Les pull requests sont les
  bienvenues (s'il y a un repo public). Les rapports de bugs — débrouillez-
  vous, corrigez, partagez le correctif.

---

## Licence

MIT. Faites ce que vous voulez avec.

---

## Héritage

C'est ce que nous utilisions chez le mainteneur en 2025-2026 avant de construire
le successeur. Ça a alimenté nos opérations quotidiennes — préparation de
réunions, revue financière, routage de leads, rédaction de contenu. Les agents
pré-installés sont de vrais agents que nous exécutions. Le brain est le vrai
modèle de mémoire neuromorphique. Le Jarvis est le vrai assistant vocal.

Nous l'avons dépassé car nous avions besoin de multi-utilisateur, de jobs
planifiés et d'un constructeur visuel. **Vous n'avez probablement besoin d'aucun
de cela.** Cette v1 est largement suffisante pour un solopreneur ou une petite
équipe gérant ses propres opérations.

Prenez-le. Lancez-le. Faites-le vôtre. Si ça vous aide à livrer — c'était
tout le but de sa publication.

— le mainteneur
