"""
Couche Fournisseur LLM — Interface unifiée pour Anthropic, OpenAI, Groq,
Ollama, xAI, Mistral AI, OpenRouter et Z.ai (GLM).

Tous les fournisseurs retournent une réponse au format Anthropic :

    response.content       -> liste de blocs, chacun avec .type dans {"text","tool_use"}
    response.stop_reason   -> "tool_use" | "end_turn" | "max_tokens"

Ainsi la boucle de conversation de server.py reste native Anthropic et
fonctionne avec tous les fournisseurs sans réécriture. Pour ajouter un
nouveau fournisseur, implémentez une nouvelle sous-classe de _Provider
et enregistrez-la dans _PROVIDERS.

Les schémas d'outils sont passés au format Anthropic :
    [{ "name": "add_task", "description": "...", "input_schema": {...} }, ...]

Les messages sont passés au format Anthropic :
    [{ "role": "user" | "assistant", "content": str | list[block] }, ...]

Les formats d'appel d'outils spécifiques à chaque fournisseur sont
traduits à l'intérieur de chaque adaptateur.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Normalized response shape (duck-typed to match anthropic.types.Message)
# ---------------------------------------------------------------------------

@dataclass
class _Block:
    type: str
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[dict] = None


@dataclass
class LLMResponse:
    content: list
    stop_reason: str  # "tool_use" | "end_turn" | "max_tokens"


# ---------------------------------------------------------------------------
# Base provider
# ---------------------------------------------------------------------------

class _Provider:
    name = "base"

    def __init__(self, api_key: str = "", model: str = "", **kwargs):
        self.api_key = api_key
        self.model = model
        self.extra = kwargs

    async def chat(
        self,
        *,
        system: str,
        messages: list,
        tools: list,
        max_tokens: int = 800,
    ) -> LLMResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Anthropic (Claude) — format natif, zéro traduction
# ---------------------------------------------------------------------------

class AnthropicProvider(_Provider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001", **kwargs):
        super().__init__(api_key, model, **kwargs)
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def chat(self, *, system, messages, tools, max_tokens=800):
        resp = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        # Already in the shape we want. Wrap blocks as _Block so downstream can
        # read .type/.text/.id/.name/.input without caring about SDK objects.
        blocks = []
        for b in resp.content:
            t = getattr(b, "type", None)
            if t == "text":
                blocks.append(_Block(type="text", text=b.text))
            elif t == "tool_use":
                blocks.append(_Block(type="tool_use", id=b.id, name=b.name, input=dict(b.input)))
            else:
                blocks.append(_Block(type=t or "text", text=str(b)))
        return LLMResponse(content=blocks, stop_reason=resp.stop_reason)


# ---------------------------------------------------------------------------
# OpenAI (GPT) — traduit tool_calls <-> blocs Anthropic tool_use
# ---------------------------------------------------------------------------

class OpenAIProvider(_Provider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1", **kwargs):
        super().__init__(api_key, model, **kwargs)
        self.base_url = base_url.rstrip("/")
        self.use_openrouter_auth = kwargs.get("use_openrouter_auth", False)

    def _to_openai_tools(self, tools):
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for t in tools
        ]

    def _to_openai_messages(self, system, messages):
        out = [{"role": "system", "content": system}]
        for m in messages:
            role = m["role"]
            content = m["content"]
            if isinstance(content, str):
                out.append({"role": role, "content": content})
                continue
            # List of blocks — Anthropic-shaped
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for b in content:
                    if isinstance(b, dict):
                        bt = b.get("type")
                        if bt == "text":
                            text_parts.append(b.get("text", ""))
                        elif bt == "tool_use":
                            tool_calls.append({
                                "id": b["id"],
                                "type": "function",
                                "function": {
                                    "name": b["name"],
                                    "arguments": json.dumps(b.get("input", {})),
                                },
                            })
                msg = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                out.append(msg)
            else:  # user
                text_parts = []
                image_parts = []
                tool_results = []
                for b in content:
                    if isinstance(b, dict):
                        bt = b.get("type")
                        if bt == "text":
                            text_parts.append(b.get("text", ""))
                        elif bt == "image":
                            src = b.get("source", {})
                            if src.get("type") == "base64":
                                image_parts.append({
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{src.get('media_type','image/jpeg')};base64,{src.get('data','')}",
                                    },
                                })
                        elif bt == "tool_result":
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": b["tool_use_id"],
                                "content": b.get("content", ""),
                            })
                if tool_results:
                    out.extend(tool_results)
                elif image_parts:
                    parts = [{"type": "text", "text": "\n".join(text_parts) or ""}] + image_parts
                    out.append({"role": "user", "content": parts})
                else:
                    out.append({"role": "user", "content": "\n".join(text_parts)})
        return out

    async def chat(self, *, system, messages, tools, max_tokens=800):
        import httpx
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": self._to_openai_messages(system, messages),
        }
        if tools:
            payload["tools"] = self._to_openai_tools(tools)
        # Provider-specific timeout. Ollama (local) needs much longer than
        # cloud APIs because the model has to be loaded into RAM on cold start
        # AND large system prompts take real CPU time to process. Cloud
        # providers respond in <10s normally, so 60s is plenty there.
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        
        # OpenRouter requires additional headers for proper attribution
        if self.use_openrouter_auth:
            headers["HTTP-Referer"] = "https://neurolinked.ai"
            headers["X-Title"] = "NeuroLinked Brain"
        
        async with httpx.AsyncClient(timeout=getattr(self, "timeout_s", 60)) as http:
            r = await http.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            data = r.json()

        msg = data["choices"][0]["message"]
        finish = data["choices"][0].get("finish_reason", "stop")

        blocks = []
        if msg.get("content"):
            blocks.append(_Block(type="text", text=msg["content"]))
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            blocks.append(_Block(
                type="tool_use",
                id=tc["id"],
                name=tc["function"]["name"],
                input=args,
            ))

        stop_reason = {
            "tool_calls": "tool_use",
            "function_call": "tool_use",
            "stop": "end_turn",
            "length": "max_tokens",
        }.get(finish, "end_turn")

        return LLMResponse(content=blocks, stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# Groq — API compatible OpenAI, URL de base différente
# ---------------------------------------------------------------------------

class GroqProvider(OpenAIProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", **kwargs):
        super().__init__(api_key=api_key, model=model, base_url="https://api.groq.com/openai/v1", **kwargs)


# ---------------------------------------------------------------------------
# Ollama (local) — gratuit, pas de clé API, utilise l'API compatible OpenAI
# ---------------------------------------------------------------------------

class OllamaProvider(OpenAIProvider):
    name = "ollama"
    # Local CPU/GPU inference + cold-start model load + large system prompts
    # easily blow past 60s. 300s gives the model time to warm up on first call;
    # subsequent calls will be far faster as the model stays loaded in RAM.
    timeout_s = 300

    def __init__(self, api_key: str = "ollama", model: str = "llama3.1:8b",
                 base_url: str = "http://localhost:11434/v1", **kwargs):
        super().__init__(api_key=api_key, model=model, base_url=base_url, **kwargs)
        # Allow override via config (e.g. user has a fast GPU and wants to fail fast)
        self.timeout_s = kwargs.get("timeout_s", 300)


# ---------------------------------------------------------------------------
# xAI Grok — également compatible OpenAI
# ---------------------------------------------------------------------------

class XAIProvider(OpenAIProvider):
    name = "xai"

    def __init__(self, api_key: str, model: str = "grok-2-latest", **kwargs):
        super().__init__(api_key=api_key, model=model, base_url="https://api.x.ai/v1", **kwargs)


# ---------------------------------------------------------------------------
# Mistral AI — API compatible OpenAI
# ---------------------------------------------------------------------------

class MistralProvider(OpenAIProvider):
    name = "mistral"

    def __init__(self, api_key: str, model: str = "mistral-large-latest", **kwargs):
        super().__init__(api_key=api_key, model=model, base_url="https://api.mistral.ai/v1", **kwargs)


# ---------------------------------------------------------------------------
# Z.ai (ZhipuAI / GLM) — API compatible OpenAI, modèles GLM-4
# ---------------------------------------------------------------------------

class ZAIProvider(OpenAIProvider):
    name = "zai"

    def __init__(self, api_key: str, model: str = "glm-4-plus", **kwargs):
        super().__init__(api_key=api_key, model=model, base_url="https://open.bigmodel.cn/api/paas/v4", **kwargs)


# ---------------------------------------------------------------------------
# OpenRouter — API compatible OpenAI avec de nombreux modèles
# ---------------------------------------------------------------------------

class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(self, api_key: str, model: str = "openai/gpt-4o-mini", **kwargs):
        super().__init__(api_key=api_key, model=model, base_url="https://openrouter.ai/api/v1", **kwargs)
        # OpenRouter uses a special Authorization format
        self.use_openrouter_auth = True


# ---------------------------------------------------------------------------
# Registre + fabrique
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "claude":    AnthropicProvider,   # alias
    "openai":    OpenAIProvider,
    "gpt":       OpenAIProvider,       # alias
    "groq":      GroqProvider,
    "ollama":    OllamaProvider,
    "local":     OllamaProvider,       # alias
    "xai":       XAIProvider,
    "grok":      XAIProvider,           # alias
    "mistral":   MistralProvider,
    "mistralai": MistralProvider,   # alias
    "openrouter": OpenRouterProvider,
    "zai":       ZAIProvider,
    "zhipu":     ZAIProvider,       # alias
    "glm":       ZAIProvider,        # alias
}


def available_providers() -> list[str]:
    return sorted({v.name for v in _PROVIDERS.values()})


def make_provider(name: str, api_key: str = "", model: str = "", **kwargs) -> _Provider:
    """Construit un fournisseur par nom. Les noms inconnus lèvent une ValueError."""
    cls = _PROVIDERS.get((name or "").lower())
    if cls is None:
        raise ValueError(
            f"Fournisseur LLM inconnu '{name}'. Disponibles : {', '.join(available_providers())}"
        )
    init_kwargs = {}
    if model:
        init_kwargs["model"] = model
    init_kwargs.update(kwargs)
    return cls(api_key=api_key, **init_kwargs)


def from_config(cfg: dict) -> _Provider:
    """Construit le fournisseur actif à partir d'un dict config.json.

    Champs attendus :
        llm_provider        : "anthropic" | "openai" | "groq" | "ollama" | "xai" | "mistral" | "openrouter"
        llm_model           : remplacement de modèle optionnel
        <provider>_api_key  : clé pour le fournisseur actif (ex. "openai_api_key")
    Remonte à `anthropic_api_key` quand llm_provider est anthropic (rétrocompatibilité).

    Si llm_provider est vide ou non défini, détecte automatiquement le premier
    fournisseur disposant d'une clé API non vide dans la config.
    """
    name = (cfg.get("llm_provider") or "").lower().strip()
    # "auto" est traité comme vide → déclenche l'auto-détection
    if name == "auto":
        name = ""
    model = cfg.get("llm_model") or ""

    # --- Auto-détection : si llm_provider est vide ou "auto", chercher la première clé API renseignée ---
    if not name:
        # Ordre de préférence pour l'auto-détection
        _AUTO_DETECT_ORDER = [
            ("mistral",    "mistral_api_key"),
            ("zai",        "zai_api_key"),
            ("anthropic",  "anthropic_api_key"),
            ("openai",     "openai_api_key"),
            ("groq",       "groq_api_key"),
            ("openrouter", "openrouter_api_key"),
            ("xai",        "xai_api_key"),
            ("ollama",     "ollama_api_key"),
        ]
        for provider_name, key_name in _AUTO_DETECT_ORDER:
            val = cfg.get(key_name, "")
            if isinstance(val, str):
                val = val.strip()
            if val and val != "ollama" or (provider_name == "ollama" and val == "ollama"):
                # Vérifier aussi les variables d'environnement
                if val or os.environ.get(f"{provider_name.upper()}_API_KEY"):
                    name = provider_name
                    print(f"[jarvis] Auto-détection : fournisseur '{name}' détecté (clé {key_name} renseignée)", flush=True)
                    break

        # Si toujours rien, essayer les variables d'environnement
        if not name:
            _ENV_DETECT = [
                ("mistral",   "MISTRAL_API_KEY"),
                ("zai",       "ZAI_API_KEY"),
                ("anthropic", "ANTHROPIC_API_KEY"),
                ("openai",    "OPENAI_API_KEY"),
                ("groq",      "GROQ_API_KEY"),
                ("openrouter","OPENROUTER_API_KEY"),
                ("xai",       "XAI_API_KEY"),
            ]
            for provider_name, env_var in _ENV_DETECT:
                if os.environ.get(env_var):
                    name = provider_name
                    print(f"[jarvis] Auto-détection : fournisseur '{name}' détecté (variable env {env_var})", flush=True)
                    break

        if not name:
            raise ValueError(
                "Aucun fournisseur LLM configuré. Veuillez définir 'llm_provider' et une "
                "clé API correspondante dans config.json ou via l'interface des paramètres. "
                "Fournisseurs disponibles : anthropic, openai, groq, ollama, xai, mistral, openrouter, zai."
            )

    if name in ("anthropic", "claude"):
        key = cfg.get("anthropic_api_key") or cfg.get("claude_api_key") or os.environ.get("ANTHROPIC_API_KEY") or ""
        model = model or "claude-haiku-4-5-20251001"
    elif name in ("openai", "gpt"):
        key = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY") or ""
        model = model or "gpt-4o-mini"
    elif name == "groq":
        key = cfg.get("groq_api_key") or os.environ.get("GROQ_API_KEY") or ""
        model = model or "llama-3.3-70b-versatile"
    elif name in ("ollama", "local"):
        key = cfg.get("ollama_api_key") or "ollama"
        model = model or "llama3.1:8b"
    elif name in ("xai", "grok"):
        key = cfg.get("xai_api_key") or cfg.get("grok_api_key") or os.environ.get("XAI_API_KEY") or ""
        model = model or "grok-2-latest"
    elif name in ("mistral", "mistralai"):
        key = cfg.get("mistral_api_key") or os.environ.get("MISTRAL_API_KEY") or ""
        model = model or "mistral-large-latest"
    elif name == "openrouter":
        key = cfg.get("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY") or ""
        model = model or "openai/gpt-4o-mini"
    elif name in ("zai", "zhipu", "glm"):
        key = cfg.get("zai_api_key") or os.environ.get("ZAI_API_KEY") or ""
        model = model or "glm-4-plus"
    else:
        raise ValueError(f"Fournisseur llm_provider inconnu dans la config : {name}")

    if not key and name not in ("ollama", "local"):
        raise ValueError(
            f"Clé API manquante pour le fournisseur '{name}'. "
            f"Définissez '{name}_api_key' dans config.json ou via l'interface des paramètres."
        )

    return make_provider(name, api_key=key, model=model)
