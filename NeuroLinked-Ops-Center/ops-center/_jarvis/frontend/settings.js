// Settings UI — loads, edits, and saves config via /api/settings.
// Exposes a global window.__jarvisFreeVoice(text) that main.js calls when
// the server returns an empty audio payload (free browser TTS fallback).

const $ = (id) => document.getElementById(id);

const FIELDS = [
    "llm_provider", "llm_model",
    "anthropic_api_key", "openai_api_key", "groq_api_key", "ollama_api_key", "xai_api_key", "mistral_api_key", "openrouter_api_key", "zai_api_key",
    "tts_provider",
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
                "Les clés Anthropic (Claude), OpenAI, Groq, xAI, Mistral AI, OpenRouter et Z.ai fonctionnent toutes.",
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
