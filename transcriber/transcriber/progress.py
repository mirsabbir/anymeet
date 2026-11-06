"""<meeting>/progress.json while a run is going: what it is doing and how far it is.
The apps read it to show progress on the meeting page (MeetingStore); it is removed
when the run ends, so its absence means nothing is running.

{"stage": "preparing" | "speakers" | "transcribing", "done": 12, "total": 640,
 "failed": 1, "waitSec": 0, "updatedAt": "2026-11-06T16:20:00Z"}

waitSec > 0 while every request is held back for a provider's per-minute limit."""
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class Progress:
    def __init__(self, path: Path | None, waiting: Callable[[], float] = lambda: 0.0, every: float = 2.0):
        self.path, self.waiting = path, waiting
        self.state = {"stage": "preparing", "done": 0, "total": 0, "failed": 0}
        self.lock = threading.Lock()
        self.closed = threading.Event()
        if path:
            self._write()
            # Rewritten every few seconds too, so a wait for a rate limit counts down on screen.
            threading.Thread(target=self._tick, args=(every,), daemon=True).start()

    def _tick(self, every: float) -> None:
        while not self.closed.wait(every):
            self._write()

    def _write(self) -> None:
        if not self.path:
            return
        with self.lock:
            if self.closed.is_set():  # checked under the lock: a write racing close() must not bring the file back
                return
            data = {**self.state, "waitSec": round(self.waiting()),
                    "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)  # readers never see half a file

    def stage(self, name: str, total: int | None = None) -> None:
        with self.lock:
            self.state["stage"] = name
            if total is not None:
                self.state.update(total=total, done=0, failed=0)
        self._write()

    def add_total(self, n: int = 1) -> None:
        """Live mode: parts are found as the recording grows."""
        with self.lock:
            self.state["total"] += n
        self._write()

    def part_done(self, failed: bool) -> None:
        with self.lock:
            self.state["done"] += 1
            self.state["failed"] += int(failed)
        self._write()

    def close(self) -> None:
        with self.lock:
            self.closed.set()
            if self.path:
                self.path.unlink(missing_ok=True)
