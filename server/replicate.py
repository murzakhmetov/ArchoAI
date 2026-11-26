#!/usr/bin/env python3
"""Two-way replication between the ArchoAI server on this Pi and the Supabase cloud project.

    python3 server/replicate.py [--dry-run] [--full]

Runs on the Pi (archoai-replicate.timer, every 2 minutes). Inert until server/replica.env exists:

    REPLICA_URL=https://<project ref>.supabase.co
    REPLICA_SECRET_KEY=<secret key of the cloud project>
    REPLICA_LOCAL_PUBLIC_URL=http://<pi address>:54321   (optional: the address stored in photo and model links)
    REPLICA_STATE_FILE=...                               (optional; default: the service's /var/lib/archoai when it
                                                          is writable, else server/.replica_state.json)

Copied both ways, additively (nothing is ever deleted, no schema change):
  * sensor_data rows the other server lacks (the target assigns the id, created_at is kept);
  * artifacts the other server lacks, with their photos and 3D models; a changed artifact is
    overwritten by its newer version (updated_at); identical content is never rewritten.
--dry-run prints the planned writes and writes nothing. --full ignores the watermarks (rescans all).
Exit code 2 when a server is unreachable or refuses the key; one failing item is only logged.
"""
import argparse
import contextlib
import datetime as dt
import os
import pathlib
import signal
import sys
from typing import NamedTuple, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from replica import artifacts, sensors  # noqa: E402
from replica.client import Server, ServerError, urllib_transport  # noqa: E402
from replica.state import State  # noqa: E402
from replica.util import UTC, log  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
SERVICE_STATE_DIR = pathlib.Path("/var/lib/archoai")  # StateDirectory of archoai-replicate.service
LOCAL_API = "http://127.0.0.1:54331"  # [api] port of supabase/config.toml, as in backup.py
NOT_CONFIGURED = "replication not configured (server/replica.env)"


class ConfigError(Exception):
    pass


class Config(NamedTuple):
    local_url: str
    local_key: str
    remote_url: str
    remote_key: str
    local_public: Optional[str]
    state_file: pathlib.Path


def read_env(path):
    """KEY=VALUE lines of an env file ({} when it does not exist); # starts a comment line."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                key = key.strip()
                key = key[len("export "):].strip() if key.startswith("export ") else key
                values[key] = value.strip().strip('"').strip("'")
    return values


def _url(value, name):
    value = value.rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ConfigError(f"{name} must start with http:// or https://")
    return value


def state_file(repo, env, environ):
    """One state (and one lock beside it) for the service and for manual runs: REPLICA_STATE_FILE, else the
    service's state directory (set by systemd, or /var/lib/archoai when it exists and is writable), else server/."""
    explicit = environ.get("REPLICA_STATE_FILE") or env.get("REPLICA_STATE_FILE")
    if explicit:
        return repo / explicit
    directory = environ.get("STATE_DIRECTORY")
    if not directory and SERVICE_STATE_DIR.is_dir() and os.access(SERVICE_STATE_DIR, os.W_OK | os.X_OK):
        directory = str(SERVICE_STATE_DIR)
    return pathlib.Path(directory) / "replica_state.json" if directory else repo / "server" / ".replica_state.json"


def load_config(repo, environ):
    """Config, or None when replication is not configured (replica.env missing or a value empty)."""
    env = read_env(repo / "server" / "replica.env")
    if not env.get("REPLICA_URL") or not env.get("REPLICA_SECRET_KEY"):
        return None
    local_key = read_env(repo / "supabase" / ".env").get("ARCHOAI_SECRET_KEY")
    if not local_key:
        raise ConfigError("supabase/.env has no ARCHOAI_SECRET_KEY: run server/setup.sh first")
    public = env.get("REPLICA_LOCAL_PUBLIC_URL")
    state = state_file(repo, env, environ)
    return Config(LOCAL_API, local_key, _url(env["REPLICA_URL"], "REPLICA_URL"), env["REPLICA_SECRET_KEY"],
                  _url(public, "REPLICA_LOCAL_PUBLIC_URL") if public else None, state)


def sync_sensors(local, remote, state, *, now, full, dry_run, log):
    """Both directions of sensor_data; the state is saved even when a fatal error ends the run."""
    if "sensor_data" in local.missing | remote.missing:
        log("sensor_data: skipped, a server has no such table")
        return 0
    failed = 0
    try:
        for src, dst, src_side, dst_side in ((local, remote, "local", "remote"), (remote, local, "remote", "local")):
            failed += sensors.sync_direction(src, dst, src_side=src_side, dst_side=dst_side, state=state,
                                             now=now, full=full, log=log)[1]
    finally:
        if not dry_run:
            state.save()  # also after an error or SIGTERM: the copied readings must stay remembered
    return failed


def run(config, transport, *, dry_run, full, now, log=log):
    """One replication pass. Raises ServerError (unreachable / auth) when a server cannot be used."""
    local = Server("local", config.local_url, config.local_key, transport, dry_run=dry_run, log=log)
    remote = Server("remote", config.remote_url, config.remote_key, transport, dry_run=dry_run, log=log)
    local.check()
    remote.check()
    state = State.load(config.state_file, log)
    if full:
        log("--full: readings that were deleted on their origin before the guard horizon (the watermark minus 1 hour) "
            "can come back")
    failed = sync_sensors(local, remote, state, now=now, full=full, dry_run=dry_run, log=log)
    art_failed = 0
    if {"artifacts"} & (local.missing | remote.missing):
        log("artifacts: skipped, a server has no such table")
    else:
        art_failed = artifacts.sync_artifacts(local, remote, local_url=config.local_url, local_public=config.local_public,
                                              remote_url=config.remote_url, log=log)[1]
    verb = "planned" if dry_run else "done"
    log(f"replication {verb}: {local.writes} writes on local, {remote.writes} on remote, {failed + art_failed} items skipped")
    return local.writes + remote.writes


class Terminated(Exception):
    pass


@contextlib.contextmanager
def terminating():
    """SIGTERM (systemctl stop, timeout) raises Terminated, so the finally blocks run and the state is saved."""
    def handler(signum, frame):
        raise Terminated("stopped by SIGTERM")
    try:
        previous = signal.signal(signal.SIGTERM, handler)
    except ValueError:  # not the main thread
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextlib.contextmanager
def single_run(lock_path):
    """Yields False when another run holds the lock (a manual run while the timer fires). Needs
    fcntl (Linux, the Pi); elsewhere there is no lock."""
    try:
        import fcntl
    except ImportError:
        yield True
        return
    pathlib.Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def main(argv=None, *, repo=REPO, environ=None, transport=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="print the planned writes, change nothing")
    parser.add_argument("--full", action="store_true", help="ignore the watermarks and rescan everything")
    args = parser.parse_args(argv)
    try:
        config = load_config(repo, os.environ if environ is None else environ)
        if config is None:
            log(NOT_CONFIGURED)
            return 0
        with terminating(), single_run(config.state_file.with_name(config.state_file.name + ".lock")) as free:
            if not free:
                log("another replication run is active; this one is skipped")
                return 0
            run(config, transport or urllib_transport, dry_run=args.dry_run, full=args.full,
                now=now or dt.datetime.now(UTC))
    except Terminated as err:
        log(f"replication {err}")
        return 143
    except (ConfigError, ServerError) as err:
        log(f"replication failed: {err}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
