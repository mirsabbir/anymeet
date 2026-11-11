"""<meeting>/job.json: a background job on a meeting folder (backup, restore, free, forget,
summary) and how far it is. It is also the lock: one job per folder at a time.

  running  {"kind": "backup", "done": 123, "total": 456, "file": "display-1.mp4", "updatedAt": "…"}
  queued   {"kind": "backup", "waiting": true, "updatedAt": "…"}
  failed   {"kind": "backup", "error": "why, in words a user can act on", "updatedAt": "…"}

done and total are bytes (0 and 0 when the job can't count, like a summary). updatedAt is
rewritten every second while the job lives, so a file without an error that is older than
STALE seconds was left by a job that was killed. The file goes away when a job ends well.
Creating <meeting>/job.stop stops that meeting's job (the apps' Stop button)."""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

FILE, STOP = "job.json", "job.stop"
STALE = 30


def read(folder: Path) -> dict | None:
    try:
        return json.loads((folder / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def running(folder: Path) -> bool:
    """True while another process holds this folder's job."""
    job = read(folder)
    if not job or job.get("error"):
        return False
    try:
        updated = datetime.strptime(job["updatedAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    return (datetime.now(timezone.utc) - updated).total_seconds() < STALE


class Jobs:
    """The job files of one run over one or more folders, worked on one at a time. A thread
    keeps them fresh and turns <folder>/job.stop into `stop` for the folder being worked on;
    a queued folder asked to stop is just dropped from the run."""

    def __init__(self, kind: str, folders: list[Path], every: float = 1.0):
        self.kind, self.lock, self.stop = kind, threading.Lock(), threading.Event()
        self.held: dict[Path, dict] = {}
        self.current: Path | None = None
        self.skipped: list[Path] = []
        for folder in folders:
            if running(folder):
                self.skipped.append(folder)
                continue
            folder.mkdir(parents=True, exist_ok=True)
            (folder / STOP).unlink(missing_ok=True)
            self.held[folder] = {"kind": kind, "waiting": True}
            self._write(folder)
        self.ended = threading.Event()
        threading.Thread(target=self._tick, args=(every,), daemon=True).start()

    @property
    def folders(self) -> list[Path]:
        return list(self.held)

    def begin(self, folder: Path) -> bool:
        """Starts work on a folder; False when it was dropped (stopped while queued)."""
        with self.lock:
            if folder not in self.held:
                return False
            self.current = folder
            self.stop.clear()
            self.held[folder] = {"kind": self.kind, "done": 0, "total": 0}
        self._write(folder)
        return True

    def progress(self, **fields) -> None:
        """total=, file=, done= for the folder being worked on."""
        with self.lock:
            if self.current in self.held:
                self.held[self.current].update(fields)

    def add(self, done: int) -> None:
        with self.lock:
            if self.current in self.held:
                self.held[self.current]["done"] += done

    def end(self, folder: Path, error: str = "") -> None:
        """Releases the folder: job.json stays only to say why it failed."""
        with self.lock:
            self.held.pop(folder, None)
            if self.current == folder:
                self.current = None
            if error:
                self._dump(folder, {"kind": self.kind, "error": error})
            else:
                (folder / FILE).unlink(missing_ok=True)
            (folder / STOP).unlink(missing_ok=True)

    def close(self) -> None:
        self.ended.set()
        for folder in self.folders:
            self.end(folder)

    def _tick(self, every: float) -> None:
        while not self.ended.wait(every):
            for folder in self.folders:
                if (folder / STOP).exists():
                    self._stop(folder)
                else:
                    self._write(folder)

    def _stop(self, folder: Path) -> None:
        if folder == self.current:
            self.stop.set()
        else:
            self.end(folder)

    def _write(self, folder: Path) -> None:
        with self.lock:
            if folder in self.held:  # under the lock: a write racing end() must not bring the file back
                self._dump(folder, self.held[folder])

    @staticmethod
    def _dump(folder: Path, data: dict) -> None:
        stamped = {**data, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        tmp = folder / (FILE + ".tmp")
        tmp.write_text(json.dumps(stamped), encoding="utf-8")
        os.replace(tmp, folder / FILE)  # readers never see half a file
