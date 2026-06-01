"""Discord REST API wrapper (bot token).

Plain stdlib so we don't add a dependency. Covers:
    post_message(token, channel_id, content, *, embed=None) -> {ok, message_id}
    dm_user(token, user_id, content)                        -> {ok, channel_id, message_id}
    read_recent(token, channel_id, limit=20)                -> {ok, messages: [...]}
    list_guilds(token)                                       -> {ok, guilds: [...]}
    list_channels(token, guild_id)                           -> {ok, channels: [...]}
    test_token(token)                                        -> {ok, user: {...}}

Discord's REST base is https://discord.com/api/v10. We pin v10 so behavior
is stable. All endpoints use the bot token via `Authorization: Bot <token>`.
"""
from __future__ import annotations
import json
import urllib.request
import urllib.error
from typing import Any


_BASE = "https://discord.com/api/v10"
_UA = "NeuroLinkedOS (https://neurolinked.local, v1.0)"


def _req(method: str, path: str, token: str, body: dict | None = None) -> dict:
    if not token:
        return {"ok": False, "error": "bot_token required"}
    url = f"{_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": _UA,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            if not raw:
                return {"ok": True}
            return {"ok": True, **(json.loads(raw) if raw.startswith(b"{") else {"_list": json.loads(raw)})}
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8", "replace")
            err = json.loads(body_txt)
            msg = err.get("message") or err.get("error") or body_txt[:200]
        except Exception:
            msg = f"HTTP {e.code}"
        return {"ok": False, "error": f"HTTP {e.code}: {msg}", "status": e.code}
    except Exception as e:
        return {"ok": False, "error": str(e)[:240]}


# ---- public API -----------------------------------------------------------

def test_token(token: str) -> dict:
    """Return {ok, user: {id, username, ...}} or {ok: False, error}."""
    r = _req("GET", "/users/@me", token)
    if not r.get("ok"):
        return r
    # strip the meta key, return the user object
    user = {k: v for k, v in r.items() if k != "ok"}
    return {"ok": True, "user": user}


def post_message(token: str, channel_id: str, content: str, *, embed: dict | None = None) -> dict:
    if not channel_id:
        return {"ok": False, "error": "channel_id required"}
    body: dict[str, Any] = {"content": content[:2000]}
    if embed:
        body["embeds"] = [embed]
    r = _req("POST", f"/channels/{channel_id}/messages", token, body)
    if not r.get("ok"):
        return r
    return {"ok": True, "message_id": r.get("id"), "channel_id": channel_id}


def open_dm(token: str, user_id: str) -> dict:
    """Open (or get) a DM channel with a user. Returns {ok, channel_id}."""
    r = _req("POST", "/users/@me/channels", token, {"recipient_id": user_id})
    if not r.get("ok"):
        return r
    return {"ok": True, "channel_id": r.get("id")}


def dm_user(token: str, user_id: str, content: str) -> dict:
    o = open_dm(token, user_id)
    if not o.get("ok"):
        return o
    r = post_message(token, o["channel_id"], content)
    if not r.get("ok"):
        return r
    return {"ok": True, "channel_id": o["channel_id"], "message_id": r.get("message_id")}


def read_recent(token: str, channel_id: str, limit: int = 20) -> dict:
    """Fetch the last N messages from a channel. Returns {ok, messages}."""
    limit = max(1, min(100, int(limit)))
    r = _req("GET", f"/channels/{channel_id}/messages?limit={limit}", token)
    if not r.get("ok"):
        return r
    msgs = r.get("_list") or []
    # compact each message to a readable shape
    out = []
    for m in msgs:
        out.append({
            "id": m.get("id"),
            "author": (m.get("author") or {}).get("username", ""),
            "author_id": (m.get("author") or {}).get("id", ""),
            "content": m.get("content", ""),
            "timestamp": m.get("timestamp", ""),
        })
    return {"ok": True, "messages": out}


def list_guilds(token: str) -> dict:
    r = _req("GET", "/users/@me/guilds", token)
    if not r.get("ok"):
        return r
    guilds = r.get("_list") or []
    return {"ok": True, "guilds": [{"id": g.get("id"), "name": g.get("name")} for g in guilds]}


def list_channels(token: str, guild_id: str) -> dict:
    """Lightweight channel list — only text-like channels, no overwrites."""
    r = _req("GET", f"/guilds/{guild_id}/channels", token)
    if not r.get("ok"):
        return r
    chans = r.get("_list") or []
    # Discord channel types: 0 = text, 5 = announcement, 15 = forum, 13 = stage
    KEEP = {0, 5, 15}
    return {"ok": True, "channels": [
        {"id": c.get("id"), "name": c.get("name"), "type": c.get("type")}
        for c in chans if c.get("type") in KEEP
    ]}


# ----- Full state audit + write APIs (used by the reconciler agent) --------

# Channel type IDs (Discord docs):
CHANNEL_TYPES = {
    0:  "text", 2: "voice", 4: "category", 5: "announcement",
    13: "stage", 15: "forum", 16: "media",
}

# Permission bitflag map — only the ones a moderator usually cares about.
# Full reference: https://discord.com/developers/docs/topics/permissions
PERMS = {
    "create_instant_invite":      1 << 0,
    "kick_members":               1 << 1,
    "ban_members":                1 << 2,
    "administrator":              1 << 3,
    "manage_channels":            1 << 4,
    "manage_guild":               1 << 5,
    "add_reactions":              1 << 6,
    "view_audit_log":             1 << 7,
    "priority_speaker":           1 << 8,
    "stream":                     1 << 9,
    "view_channel":               1 << 10,
    "send_messages":              1 << 11,
    "send_tts_messages":          1 << 12,
    "manage_messages":            1 << 13,
    "embed_links":                1 << 14,
    "attach_files":               1 << 15,
    "read_message_history":       1 << 16,
    "mention_everyone":           1 << 17,
    "use_external_emojis":        1 << 18,
    "view_guild_insights":        1 << 19,
    "connect":                    1 << 20,
    "speak":                      1 << 21,
    "mute_members":               1 << 22,
    "deafen_members":             1 << 23,
    "move_members":               1 << 24,
    "use_vad":                    1 << 25,
    "change_nickname":            1 << 26,
    "manage_nicknames":           1 << 27,
    "manage_roles":               1 << 28,
    "manage_webhooks":            1 << 29,
    "manage_emojis_and_stickers": 1 << 30,
    "use_application_commands":   1 << 31,
    "manage_threads":             1 << 34,
    "create_public_threads":      1 << 35,
    "create_private_threads":     1 << 36,
    "send_messages_in_threads":   1 << 38,
    "moderate_members":           1 << 40,
}


def perms_to_dict(bitfield: int) -> dict:
    """Convert a permission integer to {name: bool} for the flags we track."""
    return {name: bool(int(bitfield) & flag) for name, flag in PERMS.items() if int(bitfield) & flag}


def perms_to_int(flags: dict) -> int:
    """Convert {name: bool} → integer bitfield. Unknown flag names are ignored."""
    n = 0
    for name, on in (flags or {}).items():
        if on and name in PERMS:
            n |= PERMS[name]
    return n


def get_guild(token: str, guild_id: str) -> dict:
    """Server metadata + all the settings the reconciler cares about."""
    r = _req("GET", f"/guilds/{guild_id}?with_counts=true", token)
    if not r.get("ok"):
        return r
    return {"ok": True, "guild": {
        "id": r.get("id"),
        "name": r.get("name"),
        "description": r.get("description"),
        "owner_id": r.get("owner_id"),
        "approximate_member_count": r.get("approximate_member_count"),
        "approximate_presence_count": r.get("approximate_presence_count"),
        "features": r.get("features") or [],
        "verification_level": r.get("verification_level"),           # 0-4
        "default_message_notifications": r.get("default_message_notifications"),  # 0=all, 1=mentions
        "explicit_content_filter": r.get("explicit_content_filter"), # 0=off, 1=members w/o roles, 2=all
        "afk_channel_id": r.get("afk_channel_id"),
        "afk_timeout": r.get("afk_timeout"),                          # seconds (60..3600)
        "system_channel_id": r.get("system_channel_id"),
        "system_channel_flags": r.get("system_channel_flags"),
        "rules_channel_id": r.get("rules_channel_id"),
        "public_updates_channel_id": r.get("public_updates_channel_id"),
        "preferred_locale": r.get("preferred_locale"),
        "premium_tier": r.get("premium_tier"),
        "premium_subscription_count": r.get("premium_subscription_count"),
        "vanity_url_code": r.get("vanity_url_code"),
    }}


def list_roles(token: str, guild_id: str) -> dict:
    """All roles in the server, sorted high → low by position."""
    r = _req("GET", f"/guilds/{guild_id}/roles", token)
    if not r.get("ok"):
        return r
    roles = r.get("_list") or []
    roles.sort(key=lambda x: x.get("position", 0), reverse=True)
    out = []
    for role in roles:
        out.append({
            "id": role.get("id"),
            "name": role.get("name"),
            "color": role.get("color", 0),
            "position": role.get("position", 0),
            "hoist": role.get("hoist", False),
            "mentionable": role.get("mentionable", False),
            "managed": role.get("managed", False),  # True = bot/integration-managed
            "permissions": int(role.get("permissions", 0)),
            "permission_flags": perms_to_dict(int(role.get("permissions", 0))),
        })
    return {"ok": True, "roles": out}


def list_channels_full(token: str, guild_id: str) -> dict:
    """Every channel in the server with full overwrites — the workhorse for audits."""
    r = _req("GET", f"/guilds/{guild_id}/channels", token)
    if not r.get("ok"):
        return r
    chans = r.get("_list") or []
    chans.sort(key=lambda x: (x.get("position", 0), x.get("name", "")))
    out = []
    for c in chans:
        overwrites = []
        for o in c.get("permission_overwrites") or []:
            allow_int = int(o.get("allow", 0))
            deny_int = int(o.get("deny", 0))
            overwrites.append({
                "id": o.get("id"),
                "type": "role" if o.get("type") == 0 else "member",
                "allow": allow_int,
                "deny": deny_int,
                "allow_flags": perms_to_dict(allow_int),
                "deny_flags": perms_to_dict(deny_int),
            })
        out.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "type": c.get("type"),
            "type_name": CHANNEL_TYPES.get(c.get("type"), f"unknown({c.get('type')})"),
            "position": c.get("position", 0),
            "parent_id": c.get("parent_id"),
            "topic": c.get("topic"),
            "nsfw": c.get("nsfw", False),
            "rate_limit_per_user": c.get("rate_limit_per_user", 0),  # slowmode (seconds)
            "bitrate": c.get("bitrate"),               # voice channels
            "user_limit": c.get("user_limit"),          # voice channels (0 = unlimited)
            "default_auto_archive_duration": c.get("default_auto_archive_duration"),
            "overwrites": overwrites,
        })
    return {"ok": True, "channels": out}


def set_channel_overwrite(token: str, channel_id: str, target_id: str,
                          target_type: str = "role",
                          allow_flags: dict | None = None,
                          deny_flags: dict | None = None) -> dict:
    """PUT a permission overwrite on a channel.

    target_type: 'role' or 'member'.
    allow_flags / deny_flags: dicts of {permission_name: True}. Anything not in
    either dict is treated as neutral (inherit from category / @everyone).
    """
    body = {
        "type": 0 if target_type == "role" else 1,
        "allow": str(perms_to_int(allow_flags or {})),
        "deny":  str(perms_to_int(deny_flags or {})),
    }
    return _req("PUT", f"/channels/{channel_id}/permissions/{target_id}", token, body)


def delete_channel_overwrite(token: str, channel_id: str, target_id: str) -> dict:
    return _req("DELETE", f"/channels/{channel_id}/permissions/{target_id}", token)


def create_role(token: str, guild_id: str, name: str, *,
                color: int = 0, permissions: int = 0,
                hoist: bool = False, mentionable: bool = False) -> dict:
    body = {
        "name": name[:100],
        "color": int(color),
        "permissions": str(int(permissions)),
        "hoist": bool(hoist),
        "mentionable": bool(mentionable),
    }
    r = _req("POST", f"/guilds/{guild_id}/roles", token, body)
    if not r.get("ok"):
        return r
    return {"ok": True, "role_id": r.get("id"), "name": r.get("name")}


def modify_role(token: str, guild_id: str, role_id: str, **fields) -> dict:
    """Patch a role. Pass any of: name, color (int), permissions (int), hoist, mentionable."""
    body = {}
    if "name" in fields:        body["name"] = str(fields["name"])[:100]
    if "color" in fields:       body["color"] = int(fields["color"])
    if "permissions" in fields: body["permissions"] = str(int(fields["permissions"]))
    if "hoist" in fields:       body["hoist"] = bool(fields["hoist"])
    if "mentionable" in fields: body["mentionable"] = bool(fields["mentionable"])
    if not body:
        return {"ok": False, "error": "no fields to modify"}
    return _req("PATCH", f"/guilds/{guild_id}/roles/{role_id}", token, body)


def modify_role_positions(token: str, guild_id: str, positions: list[dict]) -> dict:
    """Reorder roles. positions = [{'id': '<role_id>', 'position': <int>}, ...]."""
    return _req("PATCH", f"/guilds/{guild_id}/roles", token, positions)


def create_channel(token: str, guild_id: str, name: str, *,
                   channel_type: int = 0, parent_id: str | None = None,
                   topic: str | None = None) -> dict:
    body: dict = {"name": name[:100], "type": int(channel_type)}
    if parent_id: body["parent_id"] = parent_id
    if topic is not None: body["topic"] = topic[:1024]
    r = _req("POST", f"/guilds/{guild_id}/channels", token, body)
    if not r.get("ok"):
        return r
    return {"ok": True, "channel_id": r.get("id"), "name": r.get("name"), "type": r.get("type")}


def modify_channel(token: str, channel_id: str, **fields) -> dict:
    """PATCH a channel. Accepts: name, topic, nsfw, rate_limit_per_user, position,
    parent_id, bitrate (voice), user_limit (voice).
    """
    body = {}
    if "name"                in fields: body["name"]                 = str(fields["name"])[:100]
    if "topic"               in fields: body["topic"]                = ("" if fields["topic"] is None else str(fields["topic"])[:1024])
    if "nsfw"                in fields: body["nsfw"]                 = bool(fields["nsfw"])
    if "rate_limit_per_user" in fields: body["rate_limit_per_user"]  = max(0, min(21600, int(fields["rate_limit_per_user"])))
    if "position"            in fields: body["position"]             = int(fields["position"])
    if "parent_id"           in fields: body["parent_id"]            = fields["parent_id"]
    if "bitrate"             in fields: body["bitrate"]              = int(fields["bitrate"])
    if "user_limit"          in fields: body["user_limit"]           = max(0, min(99, int(fields["user_limit"])))
    if not body:
        return {"ok": False, "error": "no fields to modify"}
    return _req("PATCH", f"/channels/{channel_id}", token, body)


def get_bot_member(token: str, guild_id: str, bot_id: str) -> dict:
    """Fetch the bot's own member record — used to find which roles it actually has."""
    r = _req("GET", f"/guilds/{guild_id}/members/{bot_id}", token)
    if not r.get("ok"):
        return r
    return {"ok": True, "member": {
        "user_id": (r.get("user") or {}).get("id"),
        "roles": r.get("roles") or [],
        "joined_at": r.get("joined_at"),
    }}


def modify_guild(token: str, guild_id: str, **fields) -> dict:
    """PATCH the server itself. Accepts (selectively): name, description,
    verification_level (0-4), default_message_notifications (0-1),
    explicit_content_filter (0-2), afk_channel_id, afk_timeout,
    system_channel_id, rules_channel_id, public_updates_channel_id,
    preferred_locale.
    """
    SAFE = {"name","description","verification_level","default_message_notifications",
            "explicit_content_filter","afk_channel_id","afk_timeout",
            "system_channel_id","rules_channel_id","public_updates_channel_id",
            "preferred_locale","system_channel_flags"}
    body = {k: v for k, v in fields.items() if k in SAFE}
    if not body:
        return {"ok": False, "error": "no recognized fields"}
    return _req("PATCH", f"/guilds/{guild_id}", token, body)


# ----- AutoMod rules ------------------------------------------------------

# AutoMod trigger types (Discord docs):
#   1 = keyword       (custom or harmful link block)
#   3 = spam          (generic spam content)
#   4 = keyword_preset (Discord's curated lists: profanity / sexual / slurs)
#   5 = mention_spam
AUTOMOD_TRIGGER = {"keyword": 1, "spam": 3, "keyword_preset": 4, "mention_spam": 5}
# Preset list IDs: 1 = profanity, 2 = sexual content, 3 = slurs
AUTOMOD_PRESET = {"profanity": 1, "sexual": 2, "slurs": 3}
# Action types: 1 = block message, 2 = send alert message, 3 = timeout user
AUTOMOD_ACTION = {"block_message": 1, "send_alert": 2, "timeout": 3}


def list_automod_rules(token: str, guild_id: str) -> dict:
    r = _req("GET", f"/guilds/{guild_id}/auto-moderation/rules", token)
    if not r.get("ok"): return r
    return {"ok": True, "rules": r.get("_list") or []}


def create_automod_rule(token: str, guild_id: str, *,
                         name: str, event_type: int = 1,
                         trigger_type: int, trigger_metadata: dict | None = None,
                         actions: list[dict] = None,
                         enabled: bool = True,
                         exempt_roles: list[str] = None,
                         exempt_channels: list[str] = None) -> dict:
    body = {
        "name": name[:100],
        "event_type": int(event_type),  # 1 = MESSAGE_SEND
        "trigger_type": int(trigger_type),
        "enabled": bool(enabled),
        "actions": actions or [],
    }
    if trigger_metadata is not None: body["trigger_metadata"] = trigger_metadata
    if exempt_roles: body["exempt_roles"] = exempt_roles
    if exempt_channels: body["exempt_channels"] = exempt_channels
    return _req("POST", f"/guilds/{guild_id}/auto-moderation/rules", token, body)


def modify_automod_rule(token: str, guild_id: str, rule_id: str, **fields) -> dict:
    return _req("PATCH", f"/guilds/{guild_id}/auto-moderation/rules/{rule_id}", token, fields)


def delete_automod_rule(token: str, guild_id: str, rule_id: str) -> dict:
    return _req("DELETE", f"/guilds/{guild_id}/auto-moderation/rules/{rule_id}", token)


# ----- Welcome screen -----------------------------------------------------

def get_welcome_screen(token: str, guild_id: str) -> dict:
    r = _req("GET", f"/guilds/{guild_id}/welcome-screen", token)
    if not r.get("ok"): return r
    return {"ok": True, "welcome_screen": {
        "description": r.get("description"),
        "welcome_channels": r.get("welcome_channels") or [],
    }}


def modify_welcome_screen(token: str, guild_id: str, *,
                           description: str | None = None,
                           welcome_channels: list[dict] | None = None,
                           enabled: bool | None = None) -> dict:
    body = {}
    if description is not None:      body["description"] = str(description)[:140]
    if welcome_channels is not None: body["welcome_channels"] = welcome_channels[:5]
    if enabled is not None:          body["enabled"] = bool(enabled)
    if not body: return {"ok": False, "error": "no fields"}
    return _req("PATCH", f"/guilds/{guild_id}/welcome-screen", token, body)


# ----- Member roles + reactions (for the verify polling worker) -------------

def add_member_role(token: str, guild_id: str, user_id: str, role_id: str) -> dict:
    """Grant a role to a member. Idempotent — re-adding a role they already
    have is a no-op (Discord returns 204)."""
    return _req("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", token, {})


def remove_member_role(token: str, guild_id: str, user_id: str, role_id: str) -> dict:
    return _req("DELETE", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", token)


def add_reaction(token: str, channel_id: str, message_id: str, emoji: str) -> dict:
    """Add a reaction AS the bot. emoji must be url-encoded for unicode (e.g. ✅
    → %E2%9C%85). For unicode emojis we encode them via urllib.parse.quote."""
    import urllib.parse
    e = urllib.parse.quote(emoji, safe="")
    return _req("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{e}/@me", token, None)


def get_reactions(token: str, channel_id: str, message_id: str, emoji: str, *, limit: int = 100) -> dict:
    """List users who reacted with a given emoji. Returns {ok, users: [{id, username}]}."""
    import urllib.parse
    e = urllib.parse.quote(emoji, safe="")
    r = _req("GET", f"/channels/{channel_id}/messages/{message_id}/reactions/{e}?limit={limit}", token)
    if not r.get("ok"): return r
    users = r.get("_list") or []
    return {"ok": True, "users": [{"id": u.get("id"), "username": u.get("username","")} for u in users]}


def remove_user_reaction(token: str, channel_id: str, message_id: str, emoji: str, user_id: str) -> dict:
    """Remove a specific user's reaction so we don't re-process them. Requires Manage Messages."""
    import urllib.parse
    e = urllib.parse.quote(emoji, safe="")
    return _req("DELETE", f"/channels/{channel_id}/messages/{message_id}/reactions/{e}/{user_id}", token)
