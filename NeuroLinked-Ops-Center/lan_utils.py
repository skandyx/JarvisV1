"""LAN Mode — Active l'accès réseau local pour tous les services NeuroLinked.

Utilisation :
  export LAN_MODE=1    # Active le mode LAN (bind 0.0.0.0)
  ./start.sh           # Les services seront accessibles depuis le LAN

Sans LAN_MODE, les services restent sur 127.0.0.1 (localhost only).
"""
import os
import socket

def _get_lan_ip():
    """Détecte l'IP LAN de la machine (première IPv4 non-loopback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LAN_IP = _get_lan_ip()
LAN_MODE = bool(os.environ.get("LAN_MODE", "").strip())

def get_bind_host():
    return "0.0.0.0" if LAN_MODE else "127.0.0.1"

def get_allowed_hosts():
    hosts = {"localhost", "127.0.0.1", "[::1]"}
    if LAN_MODE:
        hosts.add(LAN_IP)
        hosts.add("0.0.0.0")
    return hosts

def get_allowed_origins():
    origins = {
        "http://localhost:8010", "http://127.0.0.1:8010",
        "http://localhost:8020", "http://127.0.0.1:8020",
        "http://localhost:8340", "http://127.0.0.1:8340",
    }
    if LAN_MODE:
        for port in (8010, 8020, 8340):
            origins.add(f"http://{LAN_IP}:{port}")
    return origins
