"""artifacts: copy missing rows with their files, and let the newer version of a changed row win."""
import collections

from .client import FATAL, ServerError
from .files import UNCHANGED, Side, carry_image, carry_model
from .util import PUBLIC, Sides, is_loopback, managed, parse_public, parse_ts, ref_text, ts_key

# Not part of the comparison: updated_at is bumped by the database on every write, the other two
# are specific to one server (a path on a device, a row of this server's model_files).
EXCLUDED = frozenset({"updated_at", "local_model_path", "model_file_id"})
LINKS = ("image_path", "model_url")


def normalize(row, sides):
    """Content of an artifact in server independent form (links to own storage become bucket/path)."""
    content = {}
    for name, value in row.items():
        if name in EXCLUDED:
            continue
        if name == "created_at" and value is not None:
            value = ts_key(value)
        elif name in LINKS and value:
            ref = parse_public(value, sides)
            value = ref_text(ref) if ref else value
        content[name] = value
    return content


def infer_public(rows, remote_origins):
    """Origin of the links that artifacts of the local server already use (most common one). Loopback
    addresses are no use to a phone and are ignored."""
    counts = collections.Counter()
    for row in rows:
        for name in LINKS:
            url = row.get(name)
            if isinstance(url, str) and PUBLIC in url and not any(url.startswith(o) for o in remote_origins):
                origin = url.split(PUBLIC, 1)[0]
                if not is_loopback(origin):
                    counts[origin] += 1
    return counts.most_common(1)[0][0] if counts else None


def _stamp(row):
    return parse_ts(row.get("updated_at") or row.get("created_at"))


def winner(local_row, remote_row):
    """Side of the newer version; the Pi wins a tie."""
    return "local" if _stamp(local_row) >= _stamp(remote_row) else "remote"


def _guard(row):
    """Match for an update of a row we read: only while nobody changed it since."""
    return {"id": row["id"], **({"updated_at": row["updated_at"]} if row.get("updated_at") else {})}


def push(row, existing, src, dst, sides, log):
    """Writes one artifact to dst (insert, or update of the existing row), files first.
    False when the target row changed since it was read (nothing written; the next run plans again)."""
    content = {name: value for name, value in row.items() if name not in EXCLUDED}
    defer = []  # replacements of different files: only after the row was written, never before
    image = carry_image(row, existing, src, dst, sides, log, defer)
    model = carry_model(row, existing, src, dst, sides, log, defer)
    content.update(image_path=image, model_url=model.url)
    if model.file_id is not UNCHANGED:
        content["model_file_id"] = model.file_id
    if existing is not None:
        content.pop("id")
        if not dst.server.patch("artifacts", _guard(existing), content):
            return False
    else:
        stamp = {"updated_at": row["updated_at"]} if row.get("updated_at") else {}
        dst.server.insert("artifacts", [dict(content, local_model_path=None, **stamp)])
        if model.file_id:
            dst.server.patch("model_files", {"id": model.file_id}, {"artifact_id": row["id"]})
    _replace_files(defer, dst, log)
    return True


def _replace_files(defer, dst, log):
    for job in defer:
        try:
            job()
        except ServerError as err:
            if err.kind in FATAL:
                raise
            log(f"{dst.name}: a changed file was not replaced: {err}")


def points_elsewhere(row, side, sides):
    """True when a link of this row names a managed file on the other server's storage."""
    for name in LINKS:
        ref = managed(parse_public(row.get(name), sides))
        if ref and ref.side != side:
            return True
    return False


def repair(row, src, dst, sides):
    """Finishes a copy that failed earlier: the row on dst still links to src's storage. The row is
    written only when the file arrived, so an equal pair of rows never loops. True when written."""
    quiet = lambda _message: None  # noqa: E731  (a refusal that persists would repeat in every run)
    image = carry_image(row, None, src, dst, sides, quiet)
    model = carry_model(row, None, src, dst, sides, quiet)
    changes = {}
    if image != (row.get("image_path") or ""):
        changes["image_path"] = image
    if model.url != row.get("model_url"):
        changes.update(model_url=model.url, model_file_id=model.file_id)
    if not changes or not dst.server.patch("artifacts", _guard(row), changes):
        return False
    if changes.get("model_file_id"):
        dst.server.patch("model_files", {"id": changes["model_file_id"]}, {"artifact_id": row["id"]})
    return True


def _plan(local_rows, remote_rows, sides, log):
    """Actions ("push", id, source side, row on the target) and ("repair", id, side holding the stale link)."""
    plan = []
    for art_id in sorted(set(local_rows) | set(remote_rows)):
        local_row, remote_row = local_rows.get(art_id), remote_rows.get(art_id)
        try:
            if remote_row is None:
                plan.append(("push", art_id, "local", None))
            elif local_row is None:
                plan.append(("push", art_id, "remote", None))
            elif normalize(local_row, sides) != normalize(remote_row, sides):
                newer = winner(local_row, remote_row)
                plan.append(("push", art_id, newer, remote_row if newer == "local" else local_row))
            else:
                plan += [("repair", art_id, name, None) for name, row in (("local", local_row), ("remote", remote_row))
                         if points_elsewhere(row, name, sides)]
        except ValueError as err:
            log(f"artifact {art_id} skipped: {err}")
    return plan


def _run_action(action, rows, sides, refs, log):
    kind, art_id, source, existing = action
    if kind == "repair":
        other = "remote" if source == "local" else "local"
        done = repair(rows[source][art_id], sides[other], sides[source], refs)
        if done:
            log(f"artifact {art_id}: finished the copy of its files to {source}")
        return done
    target = "remote" if source == "local" else "local"
    row = rows[source][art_id]
    done = push(row, existing, sides[source], sides[target], refs, log)
    log(f"artifact {art_id} ({row.get('name')}): " + (f"{'updated' if existing else 'copied'} {source}->{target}"
                                                      if done else f"{target} changed meanwhile, left for the next run"))
    return done


MAX_NETWORK_SKIPS = 5  # more skipped artifacts than this in one run end it with exit 2


def sync_artifacts(local, remote, *, local_url, local_public, remote_url, log):
    """Both directions in one pass. Returns (written, failed)."""
    rows = {name: {row["id"]: row for row in server.select("artifacts", {"select": "*", "order": "id.asc"})}
            for name, server in (("local", local), ("remote", remote))}
    public = local_public or infer_public(rows["local"].values(), (remote_url,))
    origins = tuple(dict.fromkeys(o for o in (public, local_url) if o))
    sides = {"local": Side("local", local, origins, public), "remote": Side("remote", remote, (remote_url,), remote_url)}
    refs = Sides({name: side.origins for name, side in sides.items()}, fallback="local")
    written = failed = 0
    local.tolerant = remote.tolerant = True  # a network failure on one file skips that artifact only
    local.net_skips = remote.net_skips = 0
    try:
        for action in _plan(rows["local"], rows["remote"], refs, log):
            try:
                written += bool(_run_action(action, rows, sides, refs, log))
            except ServerError as err:
                if err.kind in FATAL:
                    raise
                failed += 1
                log(f"artifact {action[1]} skipped: {err}")
    finally:
        local.tolerant = remote.tolerant = False
    log(f"artifacts: {len(rows['local'])} local, {len(rows['remote'])} remote, {written} written, {failed} failed")
    if local.net_skips + remote.net_skips > MAX_NETWORK_SKIPS:
        raise ServerError("unreachable", f"{local.net_skips + remote.net_skips} artifacts were skipped because of network failures")
    return written, failed
