"""Remember file checksums between launches so unchanged books are not reread.

Each entry is keyed by path and trusted only while the file's inode, size,
modification time, and status-change time all match. Any rewrite, rename, or
replacement changes at least one of those, so the checksum is recomputed.
Destructive operations still verify contents with a fresh read.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from library_fs import open_state_text, private_state_directory

VERSION = 1
MAX_ENTRIES = 250_000
MAX_AGE = 60 * 60 * 24 * 90


class Cancelled(ValueError):
    """A comparison stopped early so another operation could proceed."""

    def __init__(self):
        super().__init__('The comparison stopped so another action could finish. Try again.')


class DigestIndex:
    def __init__(self, location, compute):
        # `location` returns the state file path, so tests can redirect state.
        self.location = location
        self.compute = compute
        self.lock = threading.Lock()
        self.entries = None
        self.dirty = False
        self.cancel = threading.Event()

    def _load(self):
        if self.entries is not None:
            return
        self.entries = {}
        try:
            with open_state_text(self.location()) as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get('version') != VERSION or not isinstance(data.get('entries'), dict):
            return
        for key, value in data['entries'].items():
            if (isinstance(key, str) and isinstance(value, list) and len(value) == 6
                    and all(isinstance(number, int) for number in value[:4])
                    and isinstance(value[4], str) and len(value[4]) == 64 and isinstance(value[5], (int, float))):
                self.entries[key] = value

    def digest(self, path, info):
        if self.cancel.is_set():
            raise Cancelled()
        key = str(path)
        signature = [info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        with self.lock:
            self._load()
            entry = self.entries.get(key)
            if entry is not None and entry[:4] == signature:
                entry[5] = int(time.time())
                return entry[4]
        digest = self.compute(path)
        with self.lock:
            self.entries[key] = [*signature, digest, int(time.time())]
            self.dirty = True
        return digest

    def save(self):
        with self.lock:
            if not self.dirty or self.entries is None:
                return
            cutoff = int(time.time()) - MAX_AGE
            entries = {key: value for key, value in self.entries.items() if value[5] >= cutoff}
            if len(entries) > MAX_ENTRIES:
                newest = sorted(entries.items(), key=lambda item: item[1][5], reverse=True)[:MAX_ENTRIES]
                entries = dict(newest)
            self.entries = entries
            self.dirty = False
            payload = json.dumps({'version': VERSION, 'entries': entries}, separators=(',', ':'))
        path = Path(self.location())
        try:
            private_state_directory(path.parent)
            with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.digests-',
                                             delete=False, encoding='utf-8') as stream:
                temporary = Path(stream.name)
                try:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(payload)
                    stream.flush()
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
        except (OSError, ValueError):
            with self.lock:
                self.dirty = True
