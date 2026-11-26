"""Small helpers: timestamps, public storage links, logging."""
import datetime as dt
import ipaddress
import re
import urllib.parse
from typing import NamedTuple, Optional

UTC = dt.timezone.utc
PUBLIC = "/storage/v1/object/public/"
MODEL_BUCKET = re.compile(r"^(models-\d+|3d1)$")
_TS = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\s*(Z|z|[+-]\d{2}(?::?\d{2})?)?$")


def log(message):
    print(message, flush=True)


def _offset(zone):
    if not zone or zone in ("Z", "z"):
        return dt.timedelta(0)
    digits = zone[1:].replace(":", "")
    delta = dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
    return -delta if zone[0] == "-" else delta


def parse_ts(value):
    """Timestamp text of PostgREST (any fraction length, any offset, no offset means UTC) as UTC datetime."""
    match = _TS.match(str(value).strip())
    if not match:
        raise ValueError(f"unrecognised timestamp {value!r}")
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    try:
        base = dt.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), micro, tzinfo=UTC)
        return base - _offset(zone)
    except (ValueError, OverflowError) as err:  # month 13, year 0000, 9999-12-31T23:59:59-05:00, ...
        raise ValueError(f"timestamp out of range {value!r}") from err


def fmt_ts(moment):
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ts_key(value):
    """UTC microsecond text of a timestamp: equal instants give equal text."""
    return fmt_ts(parse_ts(value))


class Sides(dict):
    """{side name: origins}. A public link of an unknown origin counts as belonging to `fallback`
    (the Pi's address may change; its old links must still be recognised)."""

    def __init__(self, mapping, fallback=None):
        super().__init__(mapping)
        self.fallback = fallback


class PublicRef(NamedTuple):
    side: str
    bucket: str
    path: str


def public_url(origin, bucket, path):
    return f"{origin}{PUBLIC}{bucket}/{urllib.parse.quote(path, safe='/')}"


def parse_public(url, sides) -> Optional[PublicRef]:
    """PublicRef when url points at the public storage of one of the sides ({name: origins})."""
    if not isinstance(url, str):
        return None
    clean = url.split("?", 1)[0]
    for name, origins in sides.items():
        for origin in origins:
            prefix = origin + PUBLIC
            if clean.startswith(prefix):
                bucket, _, path = urllib.parse.unquote(clean[len(prefix):]).partition("/")
                if bucket and path:
                    return PublicRef(name, bucket, path)
    fallback = getattr(sides, "fallback", None)
    if fallback and PUBLIC in clean and clean.startswith(("http://", "https://")):
        bucket, _, path = urllib.parse.unquote(clean.split(PUBLIC, 1)[1]).partition("/")
        if bucket and path:
            return PublicRef(fallback, bucket, path)
    return None


def ref_text(ref):
    """Server independent name of a stored file. A model keeps its path only: the bucket (batch) may
    differ between servers when one of them put it into another batch."""
    return f"model:{ref.path}" if MODEL_BUCKET.match(ref.bucket) else f"{ref.bucket}/{ref.path}"


BUCKET_OK = re.compile(r"(artifact-images|3d1|models-\d{2})")
PATH_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,198}")


def managed(ref):
    """ref when bucket and path are safe to put into a storage request (whitelisted bucket, the path rule of
    the storage policies, no empty, . or .. segment, .glb in the model buckets); None otherwise."""
    if ref is None or not BUCKET_OK.fullmatch(ref.bucket) or not PATH_OK.fullmatch(ref.path):
        return None
    if any(part in ("", ".", "..") for part in ref.path.split("/")):
        return None
    if MODEL_BUCKET.match(ref.bucket) and not ref.path.endswith(".glb"):
        return None
    return ref


def is_loopback(origin):
    host = urllib.parse.urlsplit(origin).hostname or ""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
