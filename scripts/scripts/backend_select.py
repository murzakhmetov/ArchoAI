"""Which Supabase server the scanner scripts write to: cloud, Raspberry Pi, or both.

Same rules as the app (archoai_app/lib/services/backend_config.dart), read from the same
archoai_app/.env:

    SUPABASE_URL (or SUPABASE_PROJECT_REF) + SUPABASE_KEY   first server, as before
    SUPABASE_FALLBACK_URL + SUPABASE_FALLBACK_KEY           optional second server
    SUPABASE_SERVER = auto (default) | primary | fallback

With two servers, ``auto`` uses the first one when it answers and the second one otherwise.
A script writes to ONE server; server/replicate.py on the Pi copies the data to the other one.
Only publishable keys belong here: the app bundles this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import requests

from supabase_models import normalize_base_url, resolve_supabase_url

CHOICES = ("auto", "primary", "fallback")
PROBE_TIMEOUT_SECONDS = 2.5
# Names of the first server's key. The scripts always read SUPABASE_KEY first; with a second
# server they read the app's order instead, so app and scripts pick the same server.
SCRIPT_KEY_ORDER = ("SUPABASE_KEY", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY")
APP_KEY_ORDER = ("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY", "SUPABASE_KEY")


class ServerConfigError(ValueError):
    """The first server is missing or unusable; the message names the setting."""


@dataclass(frozen=True)
class Server:
    choice: str  # "primary" or "fallback"
    url: str
    key: str

    @property
    def is_cloud(self) -> bool:
        host = (urlsplit(self.url).hostname or "").lower()
        return host.endswith(".supabase.co") or host.endswith(".supabase.in")

    @property
    def label(self) -> str:
        kind = "Supabase Cloud" if self.is_cloud else "Raspberry Pi"
        return f"{kind} ({self.url})"


@dataclass(frozen=True)
class ServerConfig:
    primary: Server
    fallback: Optional[Server]
    choice: str
    warnings: Tuple[str, ...] = ()


def _read(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def parse_choice(value: Optional[str]) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in CHOICES else "auto"


def _read_fallback(env: Mapping[str, str], primary: Server, warnings: list) -> Optional[Server]:
    raw_url = _read(env, "SUPABASE_FALLBACK_URL")
    key = _read(env, "SUPABASE_FALLBACK_KEY")
    if not raw_url:
        if key:
            warnings.append("SUPABASE_FALLBACK_KEY is set without SUPABASE_FALLBACK_URL; ignored.")
        return None
    if not key:
        warnings.append("SUPABASE_FALLBACK_URL is set without SUPABASE_FALLBACK_KEY; the second server is ignored.")
        return None
    url = normalize_base_url(raw_url)
    if url is None:
        warnings.append(f"SUPABASE_FALLBACK_URL {raw_url!r} is not a usable address; ignored.")
        return None
    if url == primary.url:
        warnings.append("SUPABASE_FALLBACK_URL is the same server as SUPABASE_URL; ignored.")
        return None
    return Server("fallback", url, key)


def load_server_config(env: Optional[Mapping[str, str]] = None) -> ServerConfig:
    """Reads the servers from ``env`` (default: the process environment).

    Raises ServerConfigError when the first server is missing. A half-configured second
    server is ignored with a warning, so it never breaks a setup that worked before.
    """
    env = os.environ if env is None else env
    url = resolve_supabase_url(env)
    two_servers = bool(_read(env, "SUPABASE_FALLBACK_URL") and _read(env, "SUPABASE_FALLBACK_KEY"))
    names = APP_KEY_ORDER if two_servers else SCRIPT_KEY_ORDER
    keys = [_read(env, name) for name in names]
    key = next((value for value in keys if value), "")
    if url is None:
        raise ServerConfigError("SUPABASE_URL (or SUPABASE_PROJECT_REF) is missing or not a usable address")
    if not key:
        raise ServerConfigError("SUPABASE_KEY (the server's publishable key) is missing")
    primary = Server("primary", url, key)
    warnings: list = []
    if len({value for value in keys if value}) > 1:
        warnings.append(f"{', '.join(names)} hold different keys; the first one set is used.")
    fallback = _read_fallback(env, primary, warnings)
    raw_choice = _read(env, "SUPABASE_SERVER")
    choice = parse_choice(raw_choice)
    if raw_choice and raw_choice.lower() != choice:
        warnings.append(f"Unknown SUPABASE_SERVER {raw_choice!r}, using auto.")
    return ServerConfig(primary, fallback, choice, tuple(warnings))


def probe_server(server: Server, timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when ``server`` answers an authenticated request in time (not just a TCP connect)."""
    try:
        response = requests.get(
            f"{server.url}/rest/v1/sensor_data?select=id&limit=1",
            headers={"apikey": server.key},
            timeout=timeout,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def select_server(config: ServerConfig, probe: Callable[[Server], bool] = probe_server) -> Server:
    """The server to use. A forced choice and a single server need no network."""
    if config.choice == "primary" or config.fallback is None:
        return config.primary
    if config.choice == "fallback":
        return config.fallback

    def reachable(server: Server) -> bool:
        try:
            return bool(probe(server))
        except Exception:  # a broken probe means "not reachable", never a crash
            return False

    if reachable(config.primary):
        return config.primary
    if reachable(config.fallback):
        return config.fallback
    return config.primary


def server_from_env(
    env: Optional[Mapping[str, str]] = None,
    probe: Callable[[Server], bool] = probe_server,
    log: Callable[[str], None] = print,
) -> Optional[Server]:
    """The server for this run, or None when the first server is not configured.

    Callers keep their own "not configured" message for None.
    """
    try:
        config = load_server_config(env)
    except ServerConfigError as error:
        log(f"Server: {error}")
        return None
    for warning in config.warnings:
        log(f"Server: {warning}")
    server = select_server(config, probe)
    if config.fallback is not None:
        log(f"Server: {server.label} ({config.choice})")
    return server
