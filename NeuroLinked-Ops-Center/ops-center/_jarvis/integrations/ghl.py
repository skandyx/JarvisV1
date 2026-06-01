"""
GoHighLevel integration for Jarvis.

Uses the v2 LeadConnector API (services.leadconnectorhq.com) with a Private
Integration Token (the `pit-...` style key). Single-location/sub-account model
— for agency multi-location, swap to OAuth and pass the right locationId per
call.

Setup:
    Open the gear icon in the dashboard → paste GHL Location ID + GHL API
    Key. Or set them in config.json under `ghl_location_id` and `ghl_api_key`.

Public API (all return JSON-serializable dicts; errors come back as
{"error": "..."}):

    init(location_id, api_key)
    is_configured() -> bool
    api(method, path, body=None, params=None)    # escape hatch

    Contacts:
        search_contacts(query, limit=10)
        get_contact(contact_id)
        create_contact(first_name, last_name, email, phone, tags, source)
        update_contact(contact_id, **fields)
        add_tag(contact_id, tag)
        remove_tag(contact_id, tag)

    Conversations:
        send_sms(contact_id, message)
        send_email(contact_id, subject, body, from_email=None)

    Opportunities (sales pipeline):
        list_pipelines()
        list_opportunities(pipeline_id=None, query=None, limit=20)
        create_opportunity(pipeline_id, stage_id, contact_id, name, value=0, status="open")
        update_opportunity(opportunity_id, **fields)

    Calendars:
        list_calendars()
        list_free_slots(calendar_id, start_date_iso, end_date_iso, timezone=None)
        book_appointment(calendar_id, contact_id, start_time_iso, end_time_iso, title=None)
"""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib import request as _urlreq, parse as _urlparse
from urllib.error import URLError, HTTPError


_BASE_URL = "https://services.leadconnectorhq.com"
_API_VERSION = "2021-07-28"

_LOCATION_ID: Optional[str] = None
_API_KEY: Optional[str] = None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def init(location_id: str, api_key: str) -> bool:
    """Load credentials. Re-callable to swap creds at runtime via /api/settings."""
    global _LOCATION_ID, _API_KEY
    _LOCATION_ID = (location_id or "").strip() or None
    _API_KEY = (api_key or "").strip() or None
    return is_configured()


def is_configured() -> bool:
    return bool(_LOCATION_ID and _API_KEY)


def status() -> dict:
    return {
        "configured": is_configured(),
        "location_id": _LOCATION_ID or "",
    }


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_API_KEY}",
        "Version": _API_VERSION,
        "Accept": "application/json",
        "Content-Type": "application/json",
        # GoHighLevel sits behind Cloudflare; Python's default
        # `Python-urllib/3.x` UA gets challenged. Use a normal UA.
        "User-Agent": "NeuroLinked-Jarvis/1.0 (+https://neurolinked.local)",
    }


def api(method: str, path: str, body: Optional[dict] = None, params: Optional[dict] = None) -> dict:
    """Generic GHL API call. `path` should start with '/'. Auto-includes locationId
    when the path / params likely need it."""
    if not is_configured():
        return {"error": "GoHighLevel not configured. Set ghl_location_id and ghl_api_key in settings."}

    if path and not path.startswith("/"):
        path = "/" + path
    url = _BASE_URL + path
    if params:
        # auto-include locationId on GETs that look like they need it
        url += "?" + _urlparse.urlencode(params, doseq=True)

    data = None
    if body is not None:
        # auto-include locationId in POST bodies if not present
        if isinstance(body, dict) and "locationId" not in body and method.upper() in ("POST", "PUT"):
            body = {**body, "locationId": _LOCATION_ID}
        data = json.dumps(body).encode("utf-8")

    req = _urlreq.Request(url, data=data, headers=_headers(), method=method.upper())
    try:
        with _urlreq.urlopen(req, timeout=20) as r:
            txt = r.read().decode("utf-8", errors="replace")
            try:
                return json.loads(txt)
            except Exception:
                return {"raw": txt[:2000]}
    except HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            try:
                err_json = json.loads(err_body)
            except Exception:
                err_json = {"raw": err_body[:1000]}
        except Exception:
            err_json = {}
        return {"error": f"HTTP {e.code}", "detail": err_json}
    except URLError as e:
        return {"error": f"network: {e.reason}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def search_contacts(query: str = "", limit: int = 10) -> dict:
    """Search contacts by name / email / phone / company. Empty query lists most-recent."""
    body = {
        "locationId": _LOCATION_ID,
        "pageLimit": min(max(int(limit), 1), 100),
    }
    if query and query.strip():
        body["filters"] = [
            {"field": "searchAfter", "operator": "contains", "value": query.strip()}
        ]
        # The /contacts/search endpoint's accepted shape varies — using the simpler text query:
        # Actually fall back to the legacy GET /contacts/?query= which is more forgiving.
        params = {"locationId": _LOCATION_ID, "limit": body["pageLimit"], "query": query.strip()}
        return _summarize_contacts(api("GET", "/contacts/", params=params))
    params = {"locationId": _LOCATION_ID, "limit": body["pageLimit"]}
    return _summarize_contacts(api("GET", "/contacts/", params=params))


def _summarize_contacts(resp: dict) -> dict:
    if "error" in resp:
        return resp
    contacts = resp.get("contacts", []) or []
    out = []
    for c in contacts:
        out.append({
            "id": c.get("id"),
            "name": (f"{c.get('firstName','') or ''} {c.get('lastName','') or ''}").strip() or c.get("contactName") or "",
            "email": c.get("email"),
            "phone": c.get("phone"),
            "tags": c.get("tags", []),
            "source": c.get("source"),
        })
    return {"count": len(out), "total": (resp.get("meta") or {}).get("total"), "contacts": out}


def get_contact(contact_id: str) -> dict:
    r = api("GET", f"/contacts/{contact_id}")
    if "error" in r:
        return r
    c = r.get("contact") or r
    return {
        "id": c.get("id"),
        "first_name": c.get("firstName"),
        "last_name": c.get("lastName"),
        "email": c.get("email"),
        "phone": c.get("phone"),
        "tags": c.get("tags", []),
        "source": c.get("source"),
        "date_added": c.get("dateAdded"),
        "custom_fields": c.get("customFields", []),
    }


def create_contact(
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    phone: str = "",
    tags: Optional[list] = None,
    source: str = "Jarvis",
) -> dict:
    body = {
        "locationId": _LOCATION_ID,
        "firstName": first_name or "",
        "lastName": last_name or "",
        "email": email or "",
        "phone": phone or "",
        "tags": tags or [],
        "source": source or "Jarvis",
    }
    # Strip empties so GHL doesn't reject the payload
    body = {k: v for k, v in body.items() if v not in ("", None) or k == "locationId"}
    r = api("POST", "/contacts/", body=body)
    if "error" in r:
        return r
    c = r.get("contact") or r
    return {"ok": True, "id": c.get("id"), "name": (f"{c.get('firstName','')} {c.get('lastName','')}").strip()}


def update_contact(contact_id: str, **fields) -> dict:
    """Allowed fields: first_name, last_name, email, phone, tags, source, custom_fields"""
    body = {}
    if "first_name" in fields:  body["firstName"] = fields["first_name"]
    if "last_name"  in fields:  body["lastName"]  = fields["last_name"]
    if "email"      in fields:  body["email"]     = fields["email"]
    if "phone"      in fields:  body["phone"]     = fields["phone"]
    if "tags"       in fields:  body["tags"]      = fields["tags"]
    if "source"     in fields:  body["source"]    = fields["source"]
    if not body:
        return {"error": "no fields to update"}
    r = api("PUT", f"/contacts/{contact_id}", body=body)
    return {"ok": True, **r} if "error" not in r else r


def add_tag(contact_id: str, tag: str) -> dict:
    r = api("POST", f"/contacts/{contact_id}/tags", body={"tags": [tag]})
    return {"ok": True, **r} if "error" not in r else r


def remove_tag(contact_id: str, tag: str) -> dict:
    r = api("DELETE", f"/contacts/{contact_id}/tags", body={"tags": [tag]})
    return {"ok": True, **r} if "error" not in r else r


# ---------------------------------------------------------------------------
# Conversations / messaging
# ---------------------------------------------------------------------------

def send_sms(contact_id: str, message: str) -> dict:
    body = {
        "type": "SMS",
        "contactId": contact_id,
        "message": message,
    }
    r = api("POST", "/conversations/messages", body=body)
    return {"ok": True, "message_id": r.get("messageId"), **r} if "error" not in r else r


def send_email(contact_id: str, subject: str, body_html: str, from_email: Optional[str] = None) -> dict:
    body = {
        "type": "Email",
        "contactId": contact_id,
        "subject": subject,
        "html": body_html,
    }
    if from_email:
        body["emailFrom"] = from_email
    r = api("POST", "/conversations/messages", body=body)
    return {"ok": True, "message_id": r.get("messageId"), **r} if "error" not in r else r


# ---------------------------------------------------------------------------
# Opportunities (sales pipeline)
# ---------------------------------------------------------------------------

def list_pipelines() -> dict:
    r = api("GET", "/opportunities/pipelines", params={"locationId": _LOCATION_ID})
    if "error" in r:
        return r
    out = []
    for p in r.get("pipelines", []) or []:
        out.append({
            "id": p.get("id"),
            "name": p.get("name"),
            "stages": [{"id": s.get("id"), "name": s.get("name")} for s in p.get("stages", [])],
        })
    return {"count": len(out), "pipelines": out}


def list_opportunities(pipeline_id: Optional[str] = None, query: Optional[str] = None, limit: int = 20) -> dict:
    params: dict = {"location_id": _LOCATION_ID, "limit": min(max(int(limit), 1), 100)}
    if pipeline_id:
        params["pipeline_id"] = pipeline_id
    if query:
        params["q"] = query
    r = api("GET", "/opportunities/search", params=params)
    if "error" in r:
        return r
    out = []
    for o in r.get("opportunities", []) or []:
        out.append({
            "id": o.get("id"),
            "name": o.get("name"),
            "monetary_value": o.get("monetaryValue") or o.get("value"),
            "status": o.get("status"),
            "pipeline_id": o.get("pipelineId"),
            "stage_id": o.get("pipelineStageId"),
            "contact_id": o.get("contactId") or (o.get("contact") or {}).get("id"),
            "contact_name": (o.get("contact") or {}).get("name"),
        })
    return {"count": len(out), "opportunities": out}


def create_opportunity(
    pipeline_id: str,
    stage_id: str,
    contact_id: str,
    name: str,
    value: float = 0,
    status: str = "open",
) -> dict:
    body = {
        "pipelineId": pipeline_id,
        "pipelineStageId": stage_id,
        "contactId": contact_id,
        "name": name,
        "monetaryValue": value,
        "status": status,
    }
    r = api("POST", "/opportunities/", body=body)
    return {"ok": True, **r} if "error" not in r else r


def update_opportunity(opportunity_id: str, **fields) -> dict:
    body = {}
    if "stage_id" in fields:        body["pipelineStageId"] = fields["stage_id"]
    if "pipeline_id" in fields:     body["pipelineId"]      = fields["pipeline_id"]
    if "name" in fields:            body["name"]            = fields["name"]
    if "value" in fields:           body["monetaryValue"]   = fields["value"]
    if "status" in fields:          body["status"]          = fields["status"]
    if "contact_id" in fields:      body["contactId"]       = fields["contact_id"]
    if not body:
        return {"error": "no fields to update"}
    r = api("PUT", f"/opportunities/{opportunity_id}", body=body)
    return {"ok": True, **r} if "error" not in r else r


# ---------------------------------------------------------------------------
# Calendars / appointments
# ---------------------------------------------------------------------------

def list_calendars() -> dict:
    r = api("GET", "/calendars/", params={"locationId": _LOCATION_ID})
    if "error" in r:
        return r
    out = []
    for c in r.get("calendars", []) or []:
        out.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "slot_duration_min": c.get("slotDuration"),
            "timezone": c.get("timezone") or c.get("locationTimezone"),
            "is_active": c.get("isActive"),
        })
    return {"count": len(out), "calendars": out}


def list_free_slots(calendar_id: str, start_date_iso: str, end_date_iso: str, timezone: Optional[str] = None) -> dict:
    """ISO date strings (YYYY-MM-DD or full datetime). GHL returns slots grouped by date."""
    params = {
        "startDate": start_date_iso,
        "endDate": end_date_iso,
    }
    if timezone:
        params["timezone"] = timezone
    return api("GET", f"/calendars/{calendar_id}/free-slots", params=params)


def book_appointment(
    calendar_id: str,
    contact_id: str,
    start_time_iso: str,
    end_time_iso: str,
    title: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    body = {
        "calendarId": calendar_id,
        "locationId": _LOCATION_ID,
        "contactId": contact_id,
        "startTime": start_time_iso,
        "endTime": end_time_iso,
    }
    if title: body["title"] = title
    if notes: body["notes"] = notes
    r = api("POST", "/calendars/events/appointments", body=body)
    return {"ok": True, **r} if "error" not in r else r
