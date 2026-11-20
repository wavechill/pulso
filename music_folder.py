"""Local music files, with a small disk index to avoid reimporting them."""

import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path


def signature(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def publish_audio(source, folder, title, suffix, fallback="Faixa"):
    """Move a finished file into a free name without replacing user files."""
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title).strip(" .")[:86].rstrip(" .")
    if not stem or re.fullmatch(r"(CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³])(?:\..*)?", stem, re.I):
        stem = fallback
    index = 1
    while True:
        extra = f" ({index})" if index > 1 else ""
        target = folder / (stem + extra + suffix)
        try:
            if os.name == "nt":
                source.rename(target)
            else:
                os.link(source, target)
                source.unlink()
            return target
        except FileExistsError:
            index += 1


class MusicFolder:
    def __init__(self, data, extensions, json_write):
        self.path = data / "Músicas"
        self.path.mkdir(parents=True, exist_ok=True)
        self.index_path = data / "music-files.json"
        self.extensions = extensions
        self.write = json_write
        self.lock = threading.RLock()
        self.seen = {}
        self.pending = {}
        try:
            self.records = json.loads(self.index_path.read_text(encoding="utf-8"))
            if not isinstance(self.records, dict):
                self.records = {}
        except (OSError, ValueError):
            self.records = {}

    def scan(self, enqueue, retry=False):
        """Give copies time to settle and hand ready files to the import worker."""
        now = time.monotonic()
        names = set()
        queued = 0
        with self.lock:
            for path in self.path.iterdir():
                if path.suffix.lower() not in self.extensions or path.is_symlink() or not path.is_file():
                    continue
                try:
                    stamp = signature(path)
                except OSError:
                    continue
                if not stamp[0]:
                    continue
                name = path.name
                names.add(name)
                existing = self.records.get(name)
                if retry and isinstance(existing, dict) and existing.get("error"):
                    existing = None
                    self.records.pop(name)
                if name in self.pending or (isinstance(existing, dict) and existing.get("stamp") == stamp):
                    continue
                last, observed_at = self.seen.get(name, (None, now))
                if last != stamp:
                    self.seen[name] = (stamp, now)
                    continue
                if now - observed_at < 0.65:
                    continue
                self.pending[name] = enqueue(path, stamp)
                self.seen.pop(name, None)
                queued += 1
            self.seen = {name: item for name, item in self.seen.items() if name in names}
            stale = self.records.keys() - names
            if stale:
                for name in stale:
                    self.records.pop(name, None)
                self.write(self.index_path, self.records)
        return queued

    def complete(self, name, stamp, song_id, error):
        with self.lock:
            self.pending.pop(name, None)
            if song_id or error:
                self.records[name] = {"stamp": stamp, "songId": song_id, "error": error}
                self.write(self.index_path, self.records)

    def status(self):
        with self.lock:
            return len(self.pending) + len(self.seen)

    def original(self, name, song_id):
        if not name or Path(name).name != name:
            return None
        path = self.path / name
        with self.lock:
            record = self.records.get(name, {})
            try:
                if isinstance(record, dict) and record.get("songId") == song_id and not path.is_symlink() and signature(path) == record.get("stamp"):
                    return path
            except OSError:
                pass
        return None

    def originals(self, song_id):
        with self.lock:
            return [path for name in self.records if (path := self.original(name, song_id)) is not None]

    def save(self, source, title, song_id):
        """Copy a download to a usable filename without overwriting a user file."""
        with tempfile.NamedTemporaryFile(dir=self.path, suffix=".partial", delete=False) as f:
            scratch = Path(f.name)
        try:
            shutil.copyfile(source, scratch)
            with self.lock:
                target = publish_audio(scratch, self.path, title, source.suffix.lower(), "Faixa " + song_id[:8])
                self.records[target.name] = {"stamp": signature(target), "songId": song_id}
                self.write(self.index_path, self.records)
            return target.name
        finally:
            scratch.unlink(missing_ok=True)
