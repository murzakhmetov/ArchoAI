"""Files of artifacts: copy photos and 3D models to the other server and register models there."""
import re
from typing import NamedTuple

from .client import FATAL, ServerError
from .util import BUCKET_OK, MODEL_BUCKET, managed, parse_public, public_url, ref_text

UNCHANGED = object()  # model_file_id of the target row stays as it is
LIMIT_CODES = ("53100", "54000")    # storage_full / file too large: what pick_model_bucket answers when it refuses
MISSING_FUNCTION = ("PGRST202", "42883")


class Side(NamedTuple):
    name: str
    server: object
    origins: tuple          # URL prefixes under which this server's public storage is known
    public: object = None   # origin written into links stored on this server (None: unknown)


class Carried(NamedTuple):
    url: object
    file_id: object = None


def require_public(side):
    if not side.public:
        raise ServerError("item", f"{side.name}: public address unknown, set REPLICA_LOCAL_PUBLIC_URL in server/replica.env")


MD5 = re.compile(r"[0-9a-f]{32}")


def _md5(etag):
    text = (etag or "").strip().lower()
    text = text[2:] if text.startswith("w/") else text
    text = text.strip('"')
    return text if MD5.fullmatch(text) else None


def same(first, second):
    """Same file as far as the servers tell: size, and etag only when both are md5 (storage backends
    compute other etags in their own way, which says nothing about the content)."""
    both = _md5(first.etag) and _md5(second.etag)
    return first.size == second.size and (not both or _md5(first.etag) == _md5(second.etag))


def newer(source, placed):
    """True only when both servers tell their Last-Modified and the source's file is the newer one."""
    return bool(source.modified and placed.modified and source.modified > placed.modified)


def replaceable(source, placed, defer, label, log):
    """Whether a different file on the target may be replaced now (as a deferred job). Never destroys a
    file that is not known to be older: without timestamps, or when the target's is newer, it is kept."""
    if defer is None:
        return False
    if not newer(source, placed):
        log(f"{label} differs from {'the' if placed.modified else 'an undated'} copy on the target but is not known to be "
            "newer: the target's file is kept")
        return False
    return True


def copy_object(src, dst, bucket, path, log, defer=None):
    """Makes sure the target has the file. A missing one is copied now. A different one is replaced only
    by a job appended to `defer` (run after the row was written) and only when the source's is newer.
    True when the target has the file."""
    source, placed = src.object_info(bucket, path), dst.object_info(bucket, path)
    if source is None or placed is not None and same(placed, source):
        return placed is not None
    if placed is not None:
        if replaceable(source, placed, defer, f"{bucket}/{path}", log):
            defer.append(lambda: _put(src, dst, bucket, path, True))
        return True
    return _put(src, dst, bucket, path, False)


def _put(src, dst, bucket, path, upsert):
    fetched = src.download(bucket, path)
    return fetched is not None and dst.upload(bucket, path, *fetched, upsert=upsert) in ("ok", "exists")


def cap_allows(dst, size, log):
    """(allowed, batch the target names) from the target's own limits (RPC pick_model_bucket).
    The body code decides, whatever the status (PostgREST answers 503 for 53100 and 500 for 54000).
    Without the function there is nothing to ask; any other failure is an item error (retried next run)."""
    answer = dst.rpc("pick_model_bucket", {"p_size_bytes": size})
    text = answer.body[:200].decode("utf-8", "replace")
    if answer.status == 200:
        picked = answer.json()
        return True, picked if isinstance(picked, str) and MODEL_BUCKET.match(picked) and BUCKET_OK.fullmatch(picked) else None
    if any(code in text for code in LIMIT_CODES):
        log(f"{dst.name} has no room for {size} bytes: {text}")
        return False, None
    if answer.status == 404 or any(code in text for code in MISSING_FUNCTION):
        return True, None
    raise ServerError("item", f"{dst.name}: pick_model_bucket answered HTTP {answer.status}: {text}")


def _place_model(src, dst, bucket, path, order, upsert, log):
    """Cap check, download, upload into the first bucket of `order` that takes it: (bucket, size) or None."""
    info = src.object_info(bucket, path)
    allowed, picked = cap_allows(dst, info.size if info else 0, log)
    fetched = src.download(bucket, path) if allowed else None
    if fetched is None:
        return None
    data, content_type = fetched
    for name in ([picked, *order] if picked and not upsert else order):
        if dst.upload(name, path, data, content_type, upsert=upsert) in ("ok", "exists"):
            return name, len(data)
    return None


def copy_model(src, dst, bucket, path, log, dst_bucket=None, defer=None, after=None):
    """Puts a model on the target. A missing one is placed now (the batch the target names first, then the
    source's bucket). A different one is replaced by a job in `defer`, only when the source's is newer;
    `after(bucket, size)` then updates the registry. Returns (bucket, size, uploaded) or None when refused."""
    target = dst_bucket or bucket
    source, placed = src.object_info(bucket, path), dst.object_info(target, path)
    if source is None:
        log(f"model {bucket}/{path} is not on {src.name}")
        return (target, placed.size, False) if placed else None
    if placed and not same(placed, source) and replaceable(source, placed, defer, f"model {bucket}/{path}", log):
        def replace():
            landed = _place_model(src, dst, bucket, path, [target], True, log)
            if landed and after:
                after(*landed)
        defer.append(replace)
    if placed:
        return target, placed.size, False
    landed = _place_model(src, dst, bucket, path, [bucket], False, log)
    return (*landed, True) if landed else None


def register_model(src, dst, source_file_id, bucket, path, size, artifact_id, log):
    """Upserts the model_files row on the target; returns its id, or None when that fails."""
    try:
        known = {}
        if source_file_id:
            rows = src.fetch("model_files", {"id": f"eq.{source_file_id}"})
            known = rows[0] if rows else {}
        row = {"bucket_id": bucket, "object_path": path, "size_bytes": size, "source": known.get("source") or "script"}
        for name in ("tripo_task_id", "sha256", "content_type"):
            if known.get(name):
                row[name] = known[name]
        if artifact_id:
            row["artifact_id"] = artifact_id  # omitted otherwise: an upsert must not clear an existing link
        return dst.insert("model_files", [row], upsert_on="bucket_id,object_path", returning=True)[0]["id"]
    except ServerError as err:
        if err.kind in FATAL or err.kind == "item":
            raise
        log(f"model {bucket}/{path} not registered on {dst.name}: {err}")
        return None


def _kept(existing, name, ref, sides, dst):
    """The ref the target's own row already holds for this file (a link to the target's storage), or None."""
    old = managed(parse_public((existing or {}).get(name), sides))
    return old if old and old.side == dst.name and ref_text(old) == ref_text(ref) else None


def carry_image(row, existing, src, dst, sides, log, defer=None):
    """Link for the target's image_path; the photo is copied (or replaced when it differs) as needed."""
    url = row.get("image_path") or ""
    ref = managed(parse_public(url, sides))
    if ref is None or ref.side == dst.name:
        return url
    require_public(dst)
    kept = _kept(existing, "image_path", ref, sides, dst)
    if copy_object(src.server, dst.server, ref.bucket, ref.path, log, defer):
        return existing["image_path"] if kept else public_url(dst.public, ref.bucket, ref.path)
    return existing["image_path"] if kept else url


def carry_model(row, existing, src, dst, sides, log, defer=None):
    """Link and model_files id for the target row (file_id UNCHANGED: leave the column alone)."""
    url = row.get("model_url")
    ref = managed(parse_public(url, sides))
    if ref is None or ref.side == dst.name:
        return Carried(url, None)
    require_public(dst)
    kept = _kept(existing, "model_url", ref, sides, dst)
    artifact_id = existing["id"] if existing else None

    def register(bucket, size):
        return register_model(src.server, dst.server, row.get("model_file_id"), bucket, ref.path, size, artifact_id, log)

    landed = copy_model(src.server, dst.server, ref.bucket, ref.path, log, kept.bucket if kept else None, defer, register)
    if landed is None:
        if kept:
            return Carried(existing["model_url"], UNCHANGED)
        log(f"model {ref.bucket}/{ref.path} skipped: {dst.name} keeps the link to {src.name}")
        return Carried(url, None)
    bucket, size, uploaded = landed
    if kept and not uploaded:
        return Carried(existing["model_url"], UNCHANGED)
    file_id = register(bucket, size)
    link = existing["model_url"] if kept and kept.bucket == bucket else public_url(dst.public, bucket, ref.path)
    return Carried(link, file_id if file_id else (UNCHANGED if kept else None))
