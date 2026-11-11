"""Backs up meeting folders to an AnyMeet Cloud server and brings them back.

The server (ANYMEET_CLOUD_URL, with ANYMEET_CLOUD_TOKEN) hands out presigned S3 URLs, so files
go straight to S3 in parts of PART bytes, WORKERS at a time; nothing big passes through it.
Every part carries its Content-MD5, so S3 refuses damaged bytes, and a finished file counts
as backed up only when the ETag S3 reports equals the one computed here from the bytes sent.
A part that fails is sent again with backoff until the connection has been gone for PATIENCE
seconds; parts already in S3 stay there, so a killed or failed run resumes where it stopped.

<meeting>/cloud.json records what is in the cloud:
  {"files": {"display-1.mp4": {"size": 1851113576, "etag": "…-111", "mtimeNs": 1731…}},
   "uploads": {"mic.m4a": {"uploadId": "…", "size": …, "mtimeNs": …, "partSize": …}}}
files: verified cloud copies; mtimeNs ties one to the local file it was made from or saved as,
and is absent when the file was never on this computer. uploads: started, not finished."""
import base64
import hashlib
import json
import os
import shutil
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event, Lock

import httpx

from .job import FILE as JOB_FILE, STOP as JOB_STOP, Jobs, running

MiB = 1024 * 1024
PART = 16 * MiB
WORKERS = 4  # parts uploading at once
DOWNLOAD_WORKERS = 8  # ranges downloading at once
PIECE = 256 * 1024  # a part goes out in pieces this big, so Stop ends an upload in flight at once
PATIENCE = float(os.environ.get("ANYMEET_CLOUD_PATIENCE", 600))
BACKOFF = 1.0  # the first wait after a failure; it doubles up to 30 s
STATE = "cloud.json"
# Never backed up: this app's own bookkeeping, OS litter, and live/ (removed after a live run).
SKIP = {STATE, JOB_FILE, JOB_STOP, "progress.json", "transcribe.stop", ".DS_Store", "Thumbs.db", "desktop.ini"}
SKIP_DIRS = {"live"}
AUDIO = {".m4a", ".mp3", ".wav", ".flac", ".aac"}


class CloudError(Exception):
    """A problem in words the user can act on; the apps show it on the meeting page."""


class Stopped(Exception):
    pass


class _Retry(Exception):
    pass


class _Gone(Exception):
    """The server has no such object or upload (any more)."""


def is_recording(rel: str) -> bool:
    """The big files `free` removes: the videos and audio tracks the app records."""
    return "/" not in rel and (rel.startswith("display-") or Path(rel).suffix.lower() in AUDIO)


def part_size(size: int) -> int:
    """PART, or bigger for files over ~140 GB, so no file needs more than 9,000 parts."""
    need = -(-size // 9000)
    return PART if need <= PART else -(-need // MiB) * MiB


def retry(attempt, halt: Event):
    """attempt() until it works; transient failures are tried again with backoff (1 s up to 30 s)
    until they have gone on for PATIENCE seconds. Setting `halt` ends it with Stopped."""
    delay, since = BACKOFF, None
    while True:
        if halt.is_set():
            raise Stopped()
        try:
            return attempt()
        except (httpx.TransportError, _Retry) as e:
            since = since or time.monotonic()
            if time.monotonic() - since > PATIENCE:
                raise CloudError(f"No connection to the cloud for {PATIENCE / 60:.0f} min ({str(e)[:120] or type(e).__name__}). "
                                 "Back up again later: what is already uploaded stays uploaded.") from e
            if halt.wait(delay):
                raise Stopped() from e
            delay = min(delay * 2, 30)


class Api:
    def __init__(self, url: str, token: str, transport: httpx.BaseTransport | None = None, halt: Event | None = None):
        if not url or not token:
            raise CloudError("Cloud backup isn't set up: add the server address and token in Settings → Cloud backup.")
        self.base, self.auth = url.rstrip("/"), {"Authorization": f"Bearer {token}"}
        timeout = httpx.Timeout(60, connect=15)
        self.http = httpx.Client(timeout=timeout, transport=transport)
        self.s3 = httpx.Client(timeout=timeout, transport=transport)  # presigned URLs: no Authorization header
        self.halt = halt or Event()  # set to end every retry loop: Stop, or another part failed

    def call(self, method: str, path: str, **kw) -> dict:
        def attempt() -> dict:
            r = self.http.request(method, self.base + path, headers=self.auth, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                raise _Retry(f"the server answered {r.status_code}")
            body = _json(r)
            if r.status_code == 401:
                raise CloudError("The cloud refused the token: check it in Settings → Cloud backup.")
            if r.status_code == 404:
                raise _Gone(body.get("error", "not found"))
            if r.status_code >= 400:
                raise CloudError(f"The cloud said: {body.get('error', r.status_code)}")
            return body
        return retry(attempt, self.halt)

    def meetings(self, meeting: str | None = None) -> dict[str, dict]:
        """{meeting id: {path: {size, etag, modified}}}, of every meeting or just one."""
        return self.call("GET", "/meetings", params={"id": meeting} if meeting else None)["meetings"]


def _json(r: httpx.Response) -> dict:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def load(folder: Path) -> dict:
    try:
        state = json.loads((folder / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return {"files": state.get("files", {}), "uploads": state.get("uploads", {})}


def save(folder: Path, state: dict) -> None:
    _dump(folder / STATE, state)


def local_files(folder: Path) -> dict[str, Path]:
    """What a backup sends: every non-empty file in the folder, by its path inside it."""
    found = {}
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not (Path(root) == folder and d in SKIP_DIRS) and not d.startswith("."))
        for name in names:
            path = Path(root) / name
            if name not in SKIP and not name.endswith((".part", ".part.json", ".tmp")) and not path.is_symlink() \
                    and path.stat().st_size > 0:
                found[path.relative_to(folder).as_posix()] = path
    return found


def in_cloud(entry: dict | None, remote: dict | None, path: Path) -> bool:
    """The cloud holds this very file: verified when sent (or saved here from the cloud), still
    there with the same ETag, and the local file unchanged since."""
    if not entry or not remote or entry["etag"] != remote["etag"]:
        return False
    st = path.stat()
    return entry["size"] == st.st_size == remote["size"] and entry.get("mtimeNs") == st.st_mtime_ns


# Backup ------------------------------------------------------------------------------------

def backup(api: Api, folder: Path, jobs: Jobs) -> int:
    """Sends what is new or changed; returns how many files were sent."""
    state, remote = load(folder), api.meetings(folder.name).get(folder.name, {})
    state["files"] = {rel: e for rel, e in state["files"].items() if remote.get(rel, {}).get("etag") == e["etag"]}
    todo = sorted(((rel, path) for rel, path in local_files(folder).items()
                   if not in_cloud(state["files"].get(rel), remote.get(rel), path)),
                  key=lambda item: item[1].stat().st_size)  # the transcript is safe long before the video
    jobs.progress(total=sum(path.stat().st_size for _, path in todo))
    for rel, path in todo:
        jobs.progress(file=rel)
        try:
            state["files"][rel] = _upload(api, folder, rel, path, state, jobs)
        except _Gone:  # the unfinished upload was cleaned up meanwhile: start the file again
            state["uploads"].pop(rel, None)
            state["files"][rel] = _upload(api, folder, rel, path, state, jobs)
        state["uploads"].pop(rel, None)
        save(folder, state)
    save(folder, state)
    return len(todo)


def _upload(api: Api, folder: Path, rel: str, path: Path, state: dict, jobs: Jobs) -> dict:
    key, st = f"{folder.name}/{rel}", path.stat()
    mark = {"size": st.st_size, "mtimeNs": st.st_mtime_ns, "partSize": part_size(st.st_size)}
    upload_id, have = _resume(api, key, state["uploads"].get(rel), mark)
    if not upload_id:
        upload_id = api.call("POST", "/uploads", json={"key": key, "size": mark["size"], "partSize": mark["partSize"]})["uploadId"]
        state["uploads"][rel] = {**mark, "uploadId": upload_id}
        save(folder, state)
    count = -(-mark["size"] // mark["partSize"])
    digests = _send_parts(api, path, key, upload_id, mark["partSize"], count, have, jobs)
    etag = f"{hashlib.md5(b''.join(digests)).hexdigest()}-{count}"
    try:
        done = api.call("POST", "/uploads/complete", json={
            "key": key, "uploadId": upload_id, "parts": [{"n": n, "etag": d.hex()} for n, d in enumerate(digests, 1)]})
    except _Gone:  # a retried complete whose first try went through
        done = api.call("GET", "/files", params={"key": key})
    if (done["etag"], done["size"]) != (etag, mark["size"]):
        raise CloudError(f"The cloud copy of {rel} doesn't match the file here; back up again to send it again.")
    after = path.stat()
    if (after.st_size, after.st_mtime_ns) != (mark["size"], mark["mtimeNs"]):
        raise CloudError(f"{rel} changed while it was being uploaded; back up again.")
    return {"size": mark["size"], "etag": etag, "mtimeNs": mark["mtimeNs"]}


def _resume(api: Api, key: str, pending: dict | None, mark: dict) -> tuple[str | None, dict[int, str]]:
    """An unfinished upload of this same file, and the parts S3 already has: {n: md5 hex}."""
    if not pending or {k: pending.get(k) for k in mark} != mark:
        return None, {}
    try:
        parts = api.call("GET", "/uploads/parts", params={"key": key, "uploadId": pending["uploadId"]})["parts"]
    except _Gone:
        return None, {}
    return pending["uploadId"], {p["n"]: p["etag"] for p in parts}


def _send_parts(api: Api, path: Path, key: str, upload_id: str, size: int, count: int,
                have: dict[int, str], jobs: Jobs) -> list[bytes]:
    """Each part's MD5 digest, in order. The first part that fails for good ends the rest."""
    def send(n: int) -> bytes:
        with open(path, "rb") as f:
            f.seek((n - 1) * size)
            data = f.read(size)
        digest = hashlib.md5(data).digest()
        if have.get(n) != digest.hex():  # S3 doesn't have this part, or not these bytes
            _put_part(api, key, upload_id, n, data, digest)
        jobs.add(len(data))
        return digest

    return _parallel(api, send, range(1, count + 1), WORKERS)


def _parallel(api: Api, fn, items, workers: int) -> list:
    """fn(item) for every item, `workers` at a time; results in order. The first failure ends the rest."""
    pool = ThreadPoolExecutor(workers)
    futures = [pool.submit(fn, item) for item in items]
    done, _ = wait(futures, return_when=FIRST_EXCEPTION)
    failed = next((f for f in done if f.exception()), None)
    if failed:
        api.halt.set()  # the others stop retrying now
    pool.shutdown(wait=True, cancel_futures=True)
    if failed:
        api.halt.clear()
        raise failed.exception()
    return [f.result() for f in futures]


def _put_part(api: Api, key: str, upload_id: str, n: int, data: bytes, digest: bytes) -> None:
    md5 = base64.b64encode(digest).decode()

    def attempt() -> None:
        url = api.call("POST", "/uploads/sign", json={"key": key, "uploadId": upload_id, "n": n, "md5": md5})["url"]
        r = api.s3.put(url, content=_pieces(data, api.halt), headers={"Content-MD5": md5, "Content-Length": str(len(data))})
        if r.status_code == 404:
            raise _Gone("upload")
        if r.status_code != 200:  # BadDigest, RequestTimeout, an expired URL, S3 busy: send it again
            raise _Retry(f"S3 answered {r.status_code} for part {n}")
        if r.headers.get("etag", "").strip('"') != digest.hex():
            raise _Retry(f"S3 stored different bytes for part {n}")
    retry(attempt, api.halt)


def _pieces(data: bytes, halt: Event):
    """The part in PIECE-sized pieces, ending the request as soon as `halt` is set. Content-Length
    is given, so S3 gets a plain body, not chunked encoding (which presigned PUTs refuse)."""
    for at in range(0, len(data), PIECE):
        if halt.is_set():
            raise Stopped()
        yield data[at:at + PIECE]


# Restore ------------------------------------------------------------------------------------

def restore(api: Api, folder: Path, names: list[str], jobs: Jobs) -> int:
    """Downloads the named files (default: every cloud file missing here); returns how many."""
    remote = api.meetings(folder.name).get(folder.name)
    if not remote:
        raise CloudError("This meeting isn't in the cloud.")
    unknown = [n for n in names if n not in remote]
    if unknown:
        raise CloudError(f"Not in the cloud: {', '.join(unknown)}")
    wanted = names or [rel for rel in remote if not (folder / rel).exists()]
    jobs.progress(total=sum(remote[rel]["size"] for rel in wanted))
    state = load(folder)
    for rel in wanted:
        jobs.progress(file=rel)
        state["files"][rel] = _download(api, folder, rel, remote[rel], jobs)
        save(folder, state)
    return len(wanted)


def _download(api: Api, folder: Path, rel: str, remote: dict, jobs: Jobs) -> dict:
    """Fetches the file in ranges of PART bytes, DOWNLOAD_WORKERS at a time: some networks hold
    each connection to S3 to ~100 KB/s, and parallel ranges add up. <file>.part holds the bytes
    and <file>.part.json the ranges done, so a failed or killed restore goes on from there."""
    key, dest, size = f"{folder.name}/{rel}", folder / rel, remote["size"]
    tmp, marks = dest.with_name(dest.name + ".part"), dest.with_name(dest.name + ".part.json")
    chunk, lock = part_size(size), Lock()
    done = _done_ranges(tmp, marks, remote, chunk)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not done:
        with open(tmp, "wb") as f:
            f.truncate(size)
    jobs.add(sum(min(chunk, size - i * chunk) for i in done))

    def fetch(i: int) -> None:
        if i not in done:
            retry(lambda: _fetch_range(api, key, tmp, i * chunk, min(size, (i + 1) * chunk), jobs), api.halt)
            with lock:
                done.add(i)
                _dump(marks, {"size": size, "etag": remote["etag"], "chunk": chunk, "done": sorted(done)})
    _parallel(api, fetch, range(-(-size // chunk)), DOWNLOAD_WORKERS)

    info = api.call("GET", "/files", params={"key": key})
    if file_etag(tmp, info.get("partSize") or part_size(size), remote["etag"]) != remote["etag"]:
        tmp.unlink()
        marks.unlink(missing_ok=True)
        raise CloudError(f"{rel} came down damaged and was thrown away; download it again.")
    os.replace(tmp, dest)
    marks.unlink(missing_ok=True)
    return {"size": size, "etag": remote["etag"], "mtimeNs": dest.stat().st_mtime_ns}


def _done_ranges(tmp: Path, marks: Path, remote: dict, chunk: int) -> set[int]:
    """The ranges an earlier restore of this same cloud file finished."""
    try:
        state = json.loads(marks.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    same = (state.get("size"), state.get("etag"), state.get("chunk")) == (remote["size"], remote["etag"], chunk)
    return set(state["done"]) if same and tmp.exists() and tmp.stat().st_size == remote["size"] else set()


def _fetch_range(api: Api, key: str, tmp: Path, start: int, end: int, jobs: Jobs) -> None:
    url = api.call("POST", "/downloads", json={"key": key})["url"]
    got = 0
    try:
        with api.s3.stream("GET", url, headers={"Range": f"bytes={start}-{end - 1}"}) as r:
            if r.status_code != 206:
                raise _Retry(f"S3 answered {r.status_code}")
            with open(tmp, "r+b") as f:
                f.seek(start)
                for data in r.iter_bytes(MiB):
                    if api.halt.is_set():
                        raise Stopped()
                    f.write(data)
                    got += len(data)
                    jobs.add(len(data))
        if got != end - start:
            raise _Retry("the download ended early")
    except BaseException:
        jobs.add(-got)  # this range starts again from its beginning
        raise


def _dump(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def file_etag(path: Path, size: int, like: str) -> str:
    """The ETag S3 gives this file: MD5, or for a multipart upload the MD5 of the parts' MD5s."""
    whole, digests = hashlib.md5(), []
    with open(path, "rb") as f:
        while chunk := f.read(size):
            whole.update(chunk)
            digests.append(hashlib.md5(chunk).digest())
    if "-" not in like:
        return whole.hexdigest()
    return f"{hashlib.md5(b''.join(digests)).hexdigest()}-{len(digests)}"


# Free, forget, sync -----------------------------------------------------------------------

def free(api: Api, folder: Path) -> tuple[int, list[str]]:
    """Deletes the recordings here that the cloud holds, checked with the server first;
    returns the bytes freed and the recordings kept because they aren't in the cloud."""
    state, remote = load(folder), api.meetings(folder.name).get(folder.name, {})
    freed, kept = 0, []
    for rel, path in local_files(folder).items():
        if not is_recording(rel):
            continue
        if in_cloud(state["files"].get(rel), remote.get(rel), path):
            freed += path.stat().st_size
            path.unlink()
        else:
            kept.append(rel)
    return freed, kept


def forget(api: Api, folder: Path) -> bool:
    """Deletes the meeting's cloud copy. Returns True when the folder held nothing but the
    cloud's records, so the meeting is gone and its folder can go too."""
    api.call("DELETE", "/meetings", params={"id": folder.name})
    save(folder, {"files": {}, "uploads": {}})
    return not local_files(folder)


def sync(api: Api, root: Path) -> dict:
    """Matches cloud.json to the server for every meeting: a meeting only in the cloud gets a
    folder holding just its cloud.json, so the apps list it; one gone from the cloud loses
    its records, and its folder too if that was all it held. Folders with a job are left alone."""
    meetings = api.meetings()
    folders = {p for p in root.iterdir() if (p / STATE).exists()} | {root / m for m in meetings}
    for folder in sorted(folders):
        if not running(folder):
            _sync_one(folder, meetings.get(folder.name, {}))
    return totals(meetings)


def totals(meetings: dict[str, dict]) -> dict:
    return {"meetings": len(meetings), "bytes": sum(f["size"] for files in meetings.values() for f in files.values())}


def _sync_one(folder: Path, remote: dict) -> None:
    state = load(folder)
    known = {rel: e for rel, e in state["files"].items() if remote.get(rel, {}).get("etag") == e["etag"]}
    state["files"] = {rel: known.get(rel) or {"size": r["size"], "etag": r["etag"]} for rel, r in remote.items()}
    if not state["files"] and not local_files(folder):
        shutil.rmtree(folder)
        return
    folder.mkdir(exist_ok=True)
    save(folder, state)
