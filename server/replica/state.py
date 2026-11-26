"""State file: watermarks per direction and the readings the replicator copied into each server."""
import datetime as dt
import json
import os
import pathlib

from .util import parse_ts

WINDOW = dt.timedelta(hours=1)  # a rescan starts this long before the watermark
OTHER = {"local": "remote", "remote": "local"}
MAX_POISON = 1000  # readings remembered per server as refused for good


class State:
    def __init__(self, path, data=None):
        self.path = pathlib.Path(path)
        data = data if isinstance(data, dict) else {}
        self.watermark = dict(data.get("watermark") or {})
        copied = data.get("copied_into") or {}
        self.copied_into = {side: set(copied.get(side) or ()) for side in OTHER}
        poison = data.get("poison") or {}
        self.poison = {side: set(poison.get(side) or ()) for side in OTHER}  # readings a server refused for good
        self._saved = None

    @classmethod
    def load(cls, path, log):
        path = pathlib.Path(path)
        if not path.exists():
            return cls(path)
        try:
            state = cls(path, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError) as err:
            log(f"state file {path} unreadable ({err}); starting with an empty state")
            return cls(path)
        state._saved = state._text()
        return state

    def record_copied(self, side, key):
        self.copied_into[side].add(key)

    def add_poison(self, side, key):
        self.poison[side].add(key)

    def prune_guard(self):
        """Keeps the readings a rescan can still reach: those not older than the watermark of the
        direction that scans this server, minus the window. Older keys cannot match anything."""
        for side, keys in self.copied_into.items():
            self.copied_into[side] = self._recent(keys, self.watermark.get(f"{side}->{OTHER[side]}"))
        for side, keys in self.poison.items():  # scanned from the other side, towards `side`
            recent = self._recent(keys, self.watermark.get(f"{OTHER[side]}->{side}"))
            self.poison[side] = set(sorted(recent)[-MAX_POISON:])

    @staticmethod
    def _recent(keys, mark):
        if not mark:
            return keys
        floor = parse_ts(mark) - WINDOW
        return {key for key in keys if parse_ts(key.split("|", 1)[0]) >= floor}

    def _text(self):
        return json.dumps({"version": 2, "watermark": self.watermark,
                           "copied_into": {side: sorted(keys) for side, keys in self.copied_into.items()},
                           "poison": {side: sorted(keys) for side, keys in self.poison.items()}}, indent=1)

    def save(self):
        """Writes the file when something changed (no needless SD card wear); fsync before the rename."""
        self.prune_guard()
        text = self._text()
        if text == self._saved:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        self._saved = text
