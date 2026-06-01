// Settings UI — loads, edits, and saves config via /api/settings.
// Exposes a global window.__jarvisFreeVoice(text) that main.js calls when
// the server returns an empty audio payload (free browser TTS fallback).

const $ = (id) => document.getElementById(id);

const FIELDS = [
    "llm_provider", "llm_model",
    "anthropic_api_key", "openai_api_key", "groq_api_key", "ollama_api_key", "xai_api_key", "mistral_api_key", "openrouter_api_key", "zai_api_key",
    "tts_provider", "edge_voice", "piper_model",
    "elevenlabs_api_key", "elevenlabs_voice_id",
    "user_name", "user_address", "city",
    "neurolink_url",
    // GoHighLevel
    "ghl_location_id", "ghl_api_key",
];
const CHECKBOXES = ["auto_connect_neurolink"];

function setStatus(msg, ok = true) {
    const el = $("settings-status");
    if (!el) return;
    el.textContent = msg;
    el.style.color = ok ? "var(--accent)" : "var(--accent-hot)";
    if (msg) setTimeout(() => { el.textContent = ""; }, 4000);
}

function showRelevantProviderFields() {
    const p = $("s-llm_provider").value;
    document.querySelectorAll('.settings-field[data-provider]').forEach((el) => {
        el.style.display = el.dataset.provider === p ? "" : "none";
    });
}

function showRelevantTtsFields() {
    const p = $("s-tts_provider").value;
    // For "auto", show edge_tts fields (since that's the default auto choice)
    const effectiveProvider = p === "auto" ? "edge_tts" : p;
    document.querySelectorAll('.settings-field[data-tts-provider]').forEach((el) => {
        el.style.display = el.dataset.ttsProvider === effectiveProvider ? "" : "none";
    });
}

// Launch token (per-startup, set in the served HTML by the server). Without
// it, /api/settings returns 401 — so all fetches must include it.
const __TOKEN_S = (typeof window !== 'undefined' && window.__NEUROLINKED_TOKEN__) || '';
function _hdr(extra) {
    const h = { ...(extra || {}) };
    if (__TOKEN_S) h['X-Neurolinked-Token'] = __TOKEN_S;
    return h;
}

async function loadSettings() {
    try {
        const r = await fetch("/api/settings", { headers: _hdr() });
        const data = await r.json();
        for (const f of FIELDS) {
            const el = $("s-" + f);
            if (!el) continue;
            const v = data[f];
            // For API keys, show the masked form so user sees "already set" —
            // if they leave it unchanged, server skips it on save.
            if (f.includes("api_key") && data[f + "_set"]) {
                el.value = v || "••••••••";
            } else {
                el.value = v || "";
            }
        }
        for (const c of CHECKBOXES) {
            const el = $("s-" + c);
            if (el) el.checked = !!data[c];
        }
        showRelevantProviderFields();
        showRelevantTtsFields();
        // Show which LLM is actually live
        if (data.llm_active) {
            setStatus(`Actif : ${data.llm_active} (${data.llm_active_model || "modèle par défaut"})`);
        } else {
            setStatus("Aucun LLM configuré — ajoutez une clé API ci-dessous.", false);
        }
    } catch (e) {
        setStatus(`Échec du chargement : ${e.message}`, false);
    }
}

async function saveSettings() {
    const payload = {};
    for (const f of FIELDS) {
        const el = $("s-" + f);
        if (!el) continue;
        payload[f] = el.value;
    }
    for (const c of CHECKBOXES) {
        const el = $("s-" + c);
        if (el) payload[c] = !!el.checked;
    }
    try {
        const r = await fetch("/api/settings", {
            method: "POST",
            headers: _hdr({ "Content-Type": "application/json" }),
            body: JSON.stringify(payload),
        });
        const data = await r.json();
        if (data.ok) {
            setStatus(`Sauvegardé — LLM actif : ${data.llm_active || "aucun"}`);
            setTimeout(() => closeSettings(), 1200);
        } else {
            setStatus(data.error || "Échec de la sauvegarde", false);
        }
    } catch (e) {
        setStatus(`Échec de la sauvegarde : ${e.message}`, false);
    }
}

function openSettings() {
    $("settings-modal").classList.remove("hidden");
    loadSettings();
}

function closeSettings() {
    $("settings-modal").classList.add("hidden");
}

// Vérification premier lancement. Si aucune clé LLM n'est configurée (et que le
// fournisseur n'est pas Ollama local, qui ne nécessite pas de clé), ouvre
// automatiquement la fenêtre des paramètres pour que l'utilisateur sache où
// coller ses clés au lieu d'avoir le silence "aucun LLM configuré".
async function maybePromptFirstRun() {
    try {
        const r = await fetch("/api/settings", { headers: _hdr() });
        const d = await r.json();
        const provider = (d.llm_provider || "").toLowerCase();
        if (provider === "ollama") return;             // local model, no key needed
        if (d.llm_active) return;                       // a provider IS live, we're good
        // No active provider — open settings and put a friendly note.
        openSettings();
        setTimeout(() => {
            setStatus(
                "Bienvenue — collez votre clé API pour le fournisseur choisi, puis cliquez sur Enregistrer. " +
                "Les clés Anthropic (Claude), OpenAI, Groq, xAI, Mistral AI, OpenRouter et Z.ai fonctionnent toutes. " +
                "Vous pouvez aussi installer des serveurs MCP, des plugins/skills depuis GitHub et enregistrer des projets locaux.",
                true
            );
        }, 200);
    } catch (_) { /* server not ready yet — silent */ }
}

window.addEventListener("DOMContentLoaded", () => {
    $("settings-btn").addEventListener("click", openSettings);
    $("settings-close").addEventListener("click", closeSettings);
    $("settings-cancel").addEventListener("click", closeSettings);
    $("settings-save").addEventListener("click", saveSettings);
    $("s-llm_provider").addEventListener("change", showRelevantProviderFields);
    $("s-tts_provider").addEventListener("change", showRelevantTtsFields);

    // Dismiss on backdrop click
    $("settings-modal").addEventListener("click", (e) => {
        if (e.target.id === "settings-modal") closeSettings();
    });
    // Escape to close
    window.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && !$("settings-modal").classList.contains("hidden")) {
            closeSettings();
        }
    });

    // First-run prompt — wait a beat for the WebSocket to settle, then check.
    setTimeout(maybePromptFirstRun, 1500);
});

// ===========================================================================
//   FREE BROWSER VOICE FALLBACK — used by main.js when server sends empty audio
// ===========================================================================

let _voicesReady = false;
let _preferredVoice = null;

function pickVoice() {
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return null;
    // Prefer an English-male-ish voice that sounds Jarvis-y, else any English, else first.
    const rank = (v) => {
        let s = 0;
        if (/en[-_]/i.test(v.lang)) s += 10;
        if (/(daniel|david|george|mark|alex|ravi|aaron|fred)/i.test(v.name)) s += 5;
        if (/(male|man)/i.test(v.name)) s += 3;
        if (v.localService) s += 1;
        return s;
    };
    return [...voices].sort((a, b) => rank(b) - rank(a))[0];
}

function ensureVoices() {
    if (_voicesReady) return;
    const vs = window.speechSynthesis.getVoices();
    if (vs.length) {
        _preferredVoice = pickVoice();
        _voicesReady = true;
    } else {
        window.speechSynthesis.onvoiceschanged = () => {
            _preferredVoice = pickVoice();
            _voicesReady = true;
        };
    }
}
ensureVoices();

window.__jarvisFreeVoice = function (text, { source = "jarvis" } = {}) {
    if (!window.speechSynthesis || !text || !text.trim()) return;
    ensureVoices();
    // Cancel any in-flight speech so new responses interrupt old ones
    try { window.speechSynthesis.cancel(); } catch (_) {}
    const u = new SpeechSynthesisUtterance(text);
    if (_preferredVoice) u.voice = _preferredVoice;
    u.rate = 1.02;
    u.pitch = source === "brain" ? 0.95 : 1.0;
    u.volume = 1.0;
    u.lang = _preferredVoice?.lang || "en-US";
    window.speechSynthesis.speak(u);
};

window.__jarvisFreeVoiceCancel = function () {
    try { window.speechSynthesis?.cancel(); } catch (_) {}
};

// ===========================================================================
//   MCP / PLUGINS / PROJECTS — Action handlers for settings UI
// ===========================================================================

async function _apiCall(method, url, body) {
    const opts = { method, headers: _hdr({ 'Content-Type': 'application/json' }) };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(url, opts);
    return await r.json();
}

// Install MCP from GitHub
async function installMcpGithub() {
    const url = $('s-mcp_github_url').value.trim();
    if (!url) { setStatus('Entrez une URL GitHub pour le serveur MCP.', false); return; }
    setStatus('Installation du serveur MCP depuis GitHub...', true);
    const result = await _apiCall('POST', '/api/mcp/install_github', { github_url: url });
    if (result.ok) {
        setStatus(`Serveur MCP installé : ${result.name || result.id}`);
        $('s-mcp_github_url').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Install MCP from URL
async function installMcpUrl() {
    const url = $('s-mcp_zip_url').value.trim();
    if (!url) { setStatus('Entrez une URL pour l\'archive du serveur MCP.', false); return; }
    setStatus('Installation du serveur MCP depuis l\'URL...', true);
    const result = await _apiCall('POST', '/api/mcp/install_url', { zip_url: url });
    if (result.ok) {
        setStatus(`Serveur MCP installé : ${result.name || result.id}`);
        $('s-mcp_zip_url').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Create MCP from template
async function createMcpTemplate() {
    const templateId = $('s-mcp_template').value;
    if (!templateId) { setStatus('Choisissez un modèle de serveur MCP.', false); return; }
    setStatus('Création du serveur MCP depuis le modèle...', true);
    const result = await _apiCall('POST', '/api/mcp/create_template', { template_id: templateId });
    if (result.ok) {
        setStatus(`Serveur MCP créé : ${result.name || result.id}`);
        $('s-mcp_template').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Install Plugin from GitHub
async function installPluginGithub() {
    const url = $('s-plugin_github_url').value.trim();
    if (!url) { setStatus('Entrez une URL GitHub pour le plugin.', false); return; }
    setStatus('Installation du plugin depuis GitHub...', true);
    const result = await _apiCall('POST', '/api/plugins/install_github', { github_url: url });
    if (result.ok) {
        setStatus(`Plugin installé : ${result.name || result.id} (${result.tool_count || 0} outil(s))`);
        $('s-plugin_github_url').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Install Plugin from URL
async function installPluginUrl() {
    const url = $('s-plugin_zip_url').value.trim();
    if (!url) { setStatus('Entrez une URL pour l\'archive du plugin.', false); return; }
    setStatus('Installation du plugin depuis l\'URL...', true);
    const result = await _apiCall('POST', '/api/plugins/install_url', { zip_url: url });
    if (result.ok) {
        setStatus(`Plugin installé : ${result.name || result.id}`);
        $('s-plugin_zip_url').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Install Plugin from local path
async function installPluginLocal() {
    const path = $('s-plugin_local_path').value.trim();
    if (!path) { setStatus('Entrez le chemin du dossier local du plugin.', false); return; }
    setStatus('Installation du plugin depuis le dossier local...', true);
    const result = await _apiCall('POST', '/api/plugins/install_local', { local_path: path });
    if (result.ok) {
        setStatus(`Plugin installé : ${result.name || result.id}`);
        $('s-plugin_local_path').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Register a project
async function registerProject() {
    const path = $('s-project_path').value.trim();
    if (!path) { setStatus('Entrez le chemin du dossier du projet.', false); return; }
    const name = $('s-project_name').value.trim();
    const agentType = $('s-project_agent_type').value;
    setStatus('Enregistrement du projet...', true);
    const result = await _apiCall('POST', '/api/projects/register', { path, name: name || undefined });
    if (result.ok) {
        let msg = `Projet enregistré : ${result.name || result.id} (${result.project_type || 'inconnu'}, ${result.file_count || 0} fichier(s))`;
        // If an agent type was selected, assign it immediately
        if (agentType && result.id) {
            const agentResult = await _apiCall('POST', `/api/projects/${result.id}/assign_agent`, { agent_type: agentType });
            if (agentResult.ok) {
                msg += ` — Agent ${agentResult.name || agentType} assigné`;
            }
        }
        setStatus(msg);
        $('s-project_path').value = '';
        $('s-project_name').value = '';
        $('s-project_agent_type').value = '';
    } else {
        setStatus(`Erreur : ${result.error}`, false);
    }
}

// Wire up action buttons when settings modal opens
window.addEventListener("DOMContentLoaded", () => {
    // MCP actions — install on Enter key or double-click
    const mcpGithub = $('s-mcp_github_url');
    const mcpZip = $('s-mcp_zip_url');
    const mcpTemplate = $('s-mcp_template');
    if (mcpGithub) mcpGithub.addEventListener('keydown', e => { if (e.key === 'Enter') installMcpGithub(); });
    if (mcpZip) mcpZip.addEventListener('keydown', e => { if (e.key === 'Enter') installMcpUrl(); });
    if (mcpTemplate) mcpTemplate.addEventListener('change', e => { if (e.target.value) createMcpTemplate(); });

    // Plugin actions
    const pluginGithub = $('s-plugin_github_url');
    const pluginZip = $('s-plugin_zip_url');
    const pluginLocal = $('s-plugin_local_path');
    if (pluginGithub) pluginGithub.addEventListener('keydown', e => { if (e.key === 'Enter') installPluginGithub(); });
    if (pluginZip) pluginZip.addEventListener('keydown', e => { if (e.key === 'Enter') installPluginUrl(); });
    if (pluginLocal) pluginLocal.addEventListener('keydown', e => { if (e.key === 'Enter') installPluginLocal(); });

    // Project actions
    const projectPath = $('s-project_path');
    if (projectPath) projectPath.addEventListener('keydown', e => { if (e.key === 'Enter') registerProject(); });
});
