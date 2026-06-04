// ============================================================
//   CHAT PANEL — patch à coller à la fin de main.js
//   - Envoyer des messages texte (sans voix)
//   - Copier chaque réponse de Z.E.R.O.
//   - Synchronisé avec le transcript vocal existant
// ============================================================

(function () {

    // ── Inject HTML ──────────────────────────────────────────
    const panelHTML = `
    <button id="chat-toggle" title="Ouvrir le chat texte">⌨ CHAT</button>

    <div id="chat-panel" role="dialog" aria-label="Chat texte Z.E.R.O.">
        <div id="chat-header">
            <span>Z.E.R.O.</span> · TEXT INTERFACE
            <button id="chat-clear" title="Effacer la conversation">CLEAR</button>
        </div>
        <div id="chat-messages" aria-live="polite"></div>
        <div id="chat-inputbar">
            <textarea
                id="chat-input"
                placeholder="Tapez votre message... (Entrée pour envoyer, Shift+Entrée pour saut de ligne)"
                rows="1"
                aria-label="Message à envoyer"
            ></textarea>
            <button id="chat-send" title="Envoyer (Entrée)">SEND ↵</button>
        </div>
    </div>`;

    const wrapper = document.createElement('div');
    wrapper.innerHTML = panelHTML;
    document.body.appendChild(wrapper.firstElementChild); // toggle button
    document.body.appendChild(wrapper.lastElementChild);  // panel

    // ── References ───────────────────────────────────────────
    const toggle    = document.getElementById('chat-toggle');
    const panel     = document.getElementById('chat-panel');
    const messages  = document.getElementById('chat-messages');
    const input     = document.getElementById('chat-input');
    const sendBtn   = document.getElementById('chat-send');
    const clearBtn  = document.getElementById('chat-clear');

    // ── Open/close ───────────────────────────────────────────
    toggle.addEventListener('click', () => {
        const open = panel.classList.toggle('open');
        toggle.classList.toggle('active', open);
        if (open) {
            input.focus();
            scrollToBottom();
        }
    });

    // Close on Escape
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && panel.classList.contains('open')) {
            panel.classList.remove('open');
            toggle.classList.remove('active');
        }
    });

    // ── Helpers ──────────────────────────────────────────────
    function now() {
        return new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function scrollToBottom() {
        requestAnimationFrame(() => {
            messages.scrollTop = messages.scrollHeight;
        });
    }

    function escHtml(str) {
        return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    // ── Add message bubble ────────────────────────────────────
    // role : 'user' | 'jarvis' | 'brain'
    function addChatMessage(role, text) {
        const roleLabels = { user: 'VOUS', jarvis: 'Z.E.R.O.', brain: 'BRAIN' };

        const wrap = document.createElement('div');
        wrap.className = `cmsg ${role}`;

        // Row: [copy button] + [bubble]  (order flipped for user via CSS)
        const row = document.createElement('div');
        row.className = 'cmsg-row';

        const bubble = document.createElement('div');
        bubble.className = 'cmsg-bubble';
        bubble.textContent = text;   // textContent → sélectionnable, XSS-safe

        // Copy button (only on jarvis + brain)
        if (role !== 'user') {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'cmsg-copy';
            copyBtn.title = 'Copier ce message';
            copyBtn.innerHTML = '⎘';
            copyBtn.addEventListener('click', () => {
                navigator.clipboard.writeText(text).then(() => {
                    copyBtn.classList.add('copied');
                    copyBtn.innerHTML = '✓';
                    setTimeout(() => {
                        copyBtn.classList.remove('copied');
                        copyBtn.innerHTML = '⎘';
                    }, 1800);
                });
            });
            row.appendChild(copyBtn);
        }

        row.appendChild(bubble);
        wrap.appendChild(row);

        // Role label + timestamp
        const meta = document.createElement('div');
        meta.style.display = 'flex';
        meta.style.justifyContent = role === 'user' ? 'flex-end' : 'flex-start';
        meta.style.gap = '8px';

        const roleEl = document.createElement('span');
        roleEl.className = 'cmsg-role';
        roleEl.textContent = roleLabels[role] || role.toUpperCase();

        const tsEl = document.createElement('span');
        tsEl.className = 'cmsg-ts';
        tsEl.textContent = now();

        if (role === 'user') {
            meta.appendChild(tsEl);
            meta.appendChild(roleEl);
        } else {
            meta.appendChild(roleEl);
            meta.appendChild(tsEl);
        }

        wrap.appendChild(meta);
        messages.appendChild(wrap);
        scrollToBottom();

        return bubble; // returned so we can stream-append later if needed
    }

    // ── Clear ────────────────────────────────────────────────
    clearBtn.addEventListener('click', () => {
        messages.innerHTML = '';
        addSystemNote('Conversation effacée.');
    });

    function addSystemNote(text) {
        const note = document.createElement('div');
        note.style.cssText = 'text-align:center;font-size:10px;opacity:0.3;font-family:monospace;padding:4px 0;letter-spacing:.1em;';
        note.textContent = `— ${text} —`;
        messages.appendChild(note);
        scrollToBottom();
    }

    // ── Textarea auto-resize ──────────────────────────────────
    input.addEventListener('input', () => {
        input.style.height = 'auto';
        input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });

    // ── Send ─────────────────────────────────────────────────
    function sendMessage() {
        const text = input.value.trim();
        if (!text) return;

        // Add to chat panel
        addChatMessage('user', text);

        // Also add to the existing 3D transcript overlay so the neural
        // visualizer shows the activity (it reads from addTranscript)
        if (typeof addTranscript === 'function') {
            addTranscript('user', text);
        }

        // Send via the existing WebSocket (same path as voice)
        if (typeof ws !== 'undefined' && ws && ws.readyState === WebSocket.OPEN) {
            try {
                ws.send(JSON.stringify({ text }));
                if (typeof setState === 'function') setState('THINKING');
            } catch (e) {
                addSystemNote('Erreur WebSocket — reconnexion...');
            }
        } else {
            addSystemNote('WebSocket déconnecté — réessayez dans un instant.');
        }

        // Reset input
        input.value = '';
        input.style.height = '36px';
        input.focus();
    }

    sendBtn.addEventListener('click', sendMessage);

    input.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // ── Mirror incoming WS messages into chat panel ───────────
    // On patche addTranscript pour dupliquer dans le chat panel
    // sans toucher au reste du code existant.
    const _origAddTranscript = window.addTranscript;

    window.addTranscript = function (role, text) {
        // Call original (3D overlay)
        if (typeof _origAddTranscript === 'function') {
            _origAddTranscript(role, text);
        }
        // Mirror in chat panel (jarvis + brain only — user déjà ajouté dans sendMessage)
        if (role === 'jarvis' || role === 'brain') {
            addChatMessage(role, text);
        }
    };

    // ── Welcome note ─────────────────────────────────────────
    addSystemNote('Interface texte prête · les réponses de Z.E.R.O. apparaissent ici');

    console.log('[chat-panel] loaded');

})();
