"""sensor_data: copy the readings the other server lacks, one direction per call."""
from .client import FATAL, ServerError
from .state import WINDOW
from .util import fmt_ts, parse_ts, ts_key

TEST_HUMIDITY = 33.25   # the end-to-end tests of the app write this reading and delete it again
TEST_AIR_QUALITY = 3999
BATCH = 500
COLUMNS = ("created_at", "temperature", "humidity", "air_quality")


def row_key(row):
    """Identity of a reading across servers (ids differ): instant in UTC microseconds + the values."""
    values = (float(row[name]) for name in COLUMNS[1:])
    return "|".join([ts_key(row["created_at"]), *(repr(value) for value in values)])


def is_test_row(row):
    return row["humidity"] == TEST_HUMIDITY and row["air_quality"] == TEST_AIR_QUALITY


def usable(row):
    """False for a row that cannot be keyed (null value, 'infinity', a year outside 1..9999)."""
    try:
        row_key(row)
        return True
    except (KeyError, TypeError, ValueError):
        return False


def fetch(server, since, log):
    params = {"select": "id," + ",".join(COLUMNS), "order": "created_at.asc,id.asc",
              "created_at": ["not.is.null"]}  # created_at is nullable and NULLs sort last: keyset paging needs a value
    if since is not None:
        params["created_at"].append("gte." + fmt_ts(since))
    rows = server.select("sensor_data", params, keys=("created_at", "id"))
    good = [row for row in rows if usable(row)]
    if len(good) != len(rows):
        log(f"{server.name}: {len(rows) - len(good)} sensor_data row(s) with an unusable value or time skipped")
    return good


def payload(row):
    return {name: row[name] for name in COLUMNS}


def missing_rows(src_rows, dst_keys, guard, poison=()):
    """(key, row) pairs the target lacks; test readings, readings that came from the target and readings
    the target refused for good are left out."""
    seen, wanted = set(dst_keys), []
    for row in src_rows:
        key = row_key(row)
        if key in seen or key in guard or key in poison or is_test_row(row):
            continue
        seen.add(key)
        wanted.append((key, row))
    return wanted


def rejected_for_good(err):
    """The server refuses this row for a reason that will not go away (a 4xx other than 408/429)."""
    return err.kind == "http" and err.status is not None and 400 <= err.status < 500 and err.status not in (408, 429)


def _insert_one_by_one(dst, dst_side, chunk, state, log):
    """Returns (copied, poisoned) readings; a reading refused for good is remembered and logged once."""
    done = poisoned = 0
    for key, row in chunk:
        try:
            dst.insert("sensor_data", [payload(row)])
            state.record_copied(dst_side, key)
            done += 1
        except ServerError as err:
            if err.kind in FATAL:
                raise
            if rejected_for_good(err):
                state.add_poison(dst_side, key)
                poisoned += 1
                log(f"reading from {row['created_at']} refused for good by {dst.name} and skipped from now on: {err}")
            else:
                log(f"skipped a reading from {row['created_at']}: {err}")
    return done, poisoned


def push(dst, dst_side, wanted, state, log):
    """Inserts the readings (without id, with their created_at). Returns (copied, poisoned)."""
    copied = poisoned = 0
    for start in range(0, len(wanted), BATCH):
        chunk = wanted[start:start + BATCH]
        try:
            dst.insert("sensor_data", [payload(row) for _, row in chunk])
            for key, _ in chunk:
                state.record_copied(dst_side, key)
            copied += len(chunk)
        except ServerError as err:
            if err.kind in FATAL:
                raise
            log(f"batch of {len(chunk)} readings refused ({err}); retrying one by one")
            done, bad = _insert_one_by_one(dst, dst_side, chunk, state, log)
            copied, poisoned = copied + done, poisoned + bad
    return copied, poisoned


def sync_direction(src, dst, *, src_side, dst_side, state, now, full, log):
    """Copies readings src -> dst. Returns (copied, failed)."""
    direction = f"{src_side}->{dst_side}"
    mark = state.watermark.get(direction)
    since = None if full or not mark else parse_ts(mark) - WINDOW
    src_rows = fetch(src, since, log)
    dst_keys = {row_key(row) for row in fetch(dst, since, log)}
    wanted = missing_rows(src_rows, dst_keys, state.copied_into[src_side], state.poison[dst_side])
    copied, poisoned = push(dst, dst_side, wanted, state, log)
    failed = len(wanted) - copied - poisoned
    if src_rows and not failed:
        newest = min(max(parse_ts(row["created_at"]) for row in src_rows), now)
        state.watermark[direction] = fmt_ts(newest)
    log(f"sensor_data {direction}: {len(src_rows)} scanned, {copied} copied, {failed} failed")
    return copied, failed
