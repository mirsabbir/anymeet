"""Cloud backup against an in-process fake of the AnyMeet Cloud API and S3 (httpx.MockTransport):
what is sent and what is skipped, resuming after a crash, dropped connections, damaged bytes,
a long outage, Stop, freeing disk space, restoring with interrupted downloads, and sync."""
import base64
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest

from transcriber import cloud, job

KiB = 1024
TOKEN = "secret"


class Crash(BaseException):
    """Ends the run the way a killed process would: nothing after it runs."""


class FakeCloud:
    """The anymeet-cloud API (api.test) and S3 (s3.test). `fault(request)` may raise or return a
    response before the fake handles a request."""

    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.uploads: dict[str, dict] = {}
        self.puts = 0
        self.fault = lambda request: None

    def handle(self, request: httpx.Request) -> httpx.Response:
        early = self.fault(request)
        if early is not None:
            return early
        if request.url.host == "s3.test":
            return self.s3(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": "token"})
        q = {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}
        body = json.loads(request.content) if request.content else {}
        route = getattr(self, f"{request.method}{request.url.path}".replace("/", "_").lower(), None)
        return route(q, body) if route else httpx.Response(404, json={"error": "route"})

    # API
    def get_meetings(self, q, _):
        meetings: dict = {}
        for key, o in self.objects.items():
            meeting, _, path = key.partition("/")
            if q.get("id") in (None, meeting):
                meetings.setdefault(meeting, {})[path] = {"size": len(o["data"]), "etag": o["etag"]}
        return httpx.Response(200, json={"meetings": meetings})

    def delete_meetings(self, q, _):
        gone = [k for k in self.objects if k.startswith(q["id"] + "/")]
        for k in gone:
            del self.objects[k]
        return httpx.Response(200, json={"deleted": len(gone)})

    def get_files(self, q, _):
        o = self.objects.get(q["key"])
        if not o:
            return httpx.Response(404, json={"error": "NoSuchKey"})
        return httpx.Response(200, json={"size": len(o["data"]), "etag": o["etag"], "partSize": o["partSize"]})

    def post_uploads(self, _, b):
        for uid in [u for u, up in self.uploads.items() if up["key"] == b["key"]]:
            del self.uploads[uid]
        uid = f"u{len(self.uploads)}-{random.random()}"
        self.uploads[uid] = {"key": b["key"], "partSize": b["partSize"], "parts": {}}
        return httpx.Response(200, json={"uploadId": uid})

    def get_uploads_parts(self, q, _):
        up = self.uploads.get(q["uploadId"])
        if not up:
            return httpx.Response(404, json={"error": "NoSuchUpload"})
        return httpx.Response(200, json={"parts": [{"n": n, "etag": hashlib.md5(d).hexdigest(), "size": len(d)}
                                                   for n, d in sorted(up["parts"].items())]})

    def post_uploads_sign(self, _, b):
        return httpx.Response(200, json={"url": f"https://s3.test/part/{b['uploadId']}/{b['n']}?md5={quote(b['md5'], safe='')}"})

    def post_uploads_complete(self, _, b):
        up = self.uploads.pop(b["uploadId"], None)
        if not up:
            return httpx.Response(404, json={"error": "NoSuchUpload"})
        parts = [up["parts"][p["n"]] for p in sorted(b["parts"], key=lambda p: p["n"])]
        etag = hashlib.md5(b"".join(hashlib.md5(p).digest() for p in parts)).hexdigest() + f"-{len(parts)}"
        self.objects[up["key"]] = {"data": b"".join(parts), "etag": etag, "partSize": up["partSize"]}
        return httpx.Response(200, json={"size": len(self.objects[up["key"]]["data"]), "etag": etag})

    def post_downloads(self, _, b):
        if b["key"] not in self.objects:
            return httpx.Response(404, json={"error": "NoSuchKey"})
        return httpx.Response(200, json={"url": f"https://s3.test/obj/{b['key']}"})

    # S3
    def s3(self, request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            _, uid, n = request.url.path.rsplit("/", 2)
            signed = parse_qs(urlparse(str(request.url)).query)["md5"][0]
            if request.headers.get("content-md5") != signed:
                return httpx.Response(403, text="SignatureDoesNotMatch")
            if base64.b64encode(hashlib.md5(request.content).digest()).decode() != signed:
                return httpx.Response(400, text="BadDigest")
            if uid not in self.uploads:
                return httpx.Response(404, text="NoSuchUpload")
            self.uploads[uid]["parts"][int(n)] = request.content
            self.puts += 1
            return httpx.Response(200, headers={"ETag": f'"{hashlib.md5(request.content).hexdigest()}"'})
        data = self.objects[request.url.path.removeprefix("/obj/")]["data"]
        ranged = re.match(r"bytes=(\d+)-(\d*)", request.headers.get("range", ""))
        if not ranged:
            return httpx.Response(200, content=data)
        start, end = int(ranged.group(1)), int(ranged.group(2) or len(data) - 1)
        return httpx.Response(206, content=data[start:end + 1])


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(cloud, "PART", 64 * KiB)  # many parts from small files
    monkeypatch.setattr(cloud, "BACKOFF", 0.01)
    monkeypatch.setattr(cloud, "PATIENCE", 5)  # a test that hangs on retries fails instead
    return FakeCloud()


def api_for(fake: FakeCloud, jobs: job.Jobs | None = None) -> cloud.Api:
    return cloud.Api("https://api.test", TOKEN, transport=httpx.MockTransport(fake.handle), halt=jobs.stop if jobs else None)


@pytest.fixture
def meeting(tmp_path) -> Path:
    folder = tmp_path / "2026-11-11_1434"
    (folder / "screens").mkdir(parents=True)
    (folder / "live").mkdir()
    files = {"display-1.mp4": 1000 * KiB + 5, "mic.m4a": 200 * KiB, "system.m4a": 64 * KiB, "transcript.json": 3 * KiB,
             "screens/00-01 slide.png": 10 * KiB, "live/mic.wav": KiB, ".DS_Store": 10, "progress.json": 10}
    for name, size in files.items():
        (folder / name).write_bytes(os.urandom(size))
    (folder / "empty.txt").touch()
    return folder


def run(work, fake, folder, kind="backup"):
    jobs = job.Jobs(kind, [folder], every=0.05)
    jobs.begin(folder)
    try:
        return work(api_for(fake, jobs), folder, jobs)
    finally:
        jobs.close()


def backup(fake, folder):
    return run(cloud.backup, fake, folder)


def test_backup_sends_the_meeting_and_skips_bookkeeping(fake, meeting):
    assert backup(fake, meeting) == 5
    sent = {k.partition("/")[2] for k in fake.objects}
    assert sent == {"display-1.mp4", "mic.m4a", "system.m4a", "transcript.json", "screens/00-01 slide.png"}
    for rel in sent:
        assert fake.objects[f"{meeting.name}/{rel}"]["data"] == (meeting / rel).read_bytes()
    state = cloud.load(meeting)
    assert state["uploads"] == {} and state["files"]["display-1.mp4"]["etag"].endswith("-16")
    assert not (meeting / job.FILE).exists()


def test_a_second_backup_sends_only_what_changed(fake, meeting):
    backup(fake, meeting)
    puts = fake.puts
    assert backup(fake, meeting) == 0 and fake.puts == puts
    (meeting / "transcript.json").write_text('{"segments": []}')
    assert backup(fake, meeting) == 1
    assert fake.objects[f"{meeting.name}/transcript.json"]["data"] == b'{"segments": []}'


def test_a_crash_mid_upload_resumes_without_sending_parts_again(fake, meeting):
    total_parts = 16 + 4 + 1 + 1 + 1
    sent = []

    def crash_after_six(request):
        if request.method == "PUT":
            if len(sent) == 6:
                raise Crash()
            sent.append(1)
    fake.fault = crash_after_six
    with pytest.raises(Crash):
        backup(fake, meeting)
    assert cloud.load(meeting)["uploads"], "the unfinished upload is remembered"
    fake.fault = lambda request: None
    before = fake.puts
    assert backup(fake, meeting) >= 1
    assert fake.puts - before <= total_parts - 6 + cloud.WORKERS  # at most the parts in flight go again
    assert fake.objects[f"{meeting.name}/display-1.mp4"]["data"] == (meeting / "display-1.mp4").read_bytes()


def test_dropped_connections_and_server_errors_are_tried_again(fake, meeting):
    rng = random.Random(7)

    def flaky(request):
        roll = rng.random()
        if roll < 0.2:
            raise httpx.ConnectError("network down")
        if roll < 0.3:
            raise httpx.WriteTimeout("stalled")
        if roll < 0.35:
            return httpx.Response(503)
        return None
    fake.fault = flaky
    assert backup(fake, meeting) == 5
    for rel in ("display-1.mp4", "mic.m4a"):
        assert fake.objects[f"{meeting.name}/{rel}"]["data"] == (meeting / rel).read_bytes()


def test_bytes_damaged_on_the_way_are_refused_and_sent_again(fake, meeting):
    damaged = set()

    def corrupt_once(request):  # what S3 answers when the bytes don't match their Content-MD5
        if request.method == "PUT" and request.url.path not in damaged:
            damaged.add(request.url.path)
            return httpx.Response(400, text="BadDigest")
        return None
    fake.fault = corrupt_once
    assert backup(fake, meeting) == 5
    assert fake.objects[f"{meeting.name}/display-1.mp4"]["data"] == (meeting / "display-1.mp4").read_bytes()


def test_a_long_outage_fails_with_a_reason_and_keeps_what_was_sent(fake, meeting, monkeypatch):
    monkeypatch.setattr(cloud, "PATIENCE", 0.3)
    puts = []

    def outage_after_three(request):
        if request.method == "PUT":
            if len(puts) >= 3:
                raise httpx.ConnectError("no route to host")
            puts.append(1)
        return None
    fake.fault = outage_after_three
    with pytest.raises(cloud.CloudError, match="No connection to the cloud"):
        backup(fake, meeting)
    assert cloud.load(meeting)["uploads"]
    fake.fault = lambda request: None
    assert backup(fake, meeting) >= 1


def test_stop_ends_the_upload_within_moments(fake, meeting):
    def slow(request):
        if request.method == "PUT":
            time.sleep(0.05)
            (meeting / job.STOP).touch()
        return None
    fake.fault = slow
    started = time.monotonic()
    with pytest.raises(cloud.Stopped):
        backup(fake, meeting)
    assert time.monotonic() - started < 3
    assert cloud.load(meeting)["uploads"]


def test_the_upload_is_restarted_when_the_server_dropped_it(fake, meeting):
    sent = []

    def crash_once(request):
        if request.method == "PUT" and len(sent) == 2:
            sent.append(1)
            raise Crash()
        if request.method == "PUT":
            sent.append(1)
    fake.fault = crash_once
    with pytest.raises(Crash):
        backup(fake, meeting)
    fake.uploads.clear()  # S3's lifecycle rule aborted it meanwhile
    fake.fault = lambda request: None
    assert backup(fake, meeting) >= 1
    assert fake.objects[f"{meeting.name}/display-1.mp4"]["data"] == (meeting / "display-1.mp4").read_bytes()


def test_a_complete_that_went_through_but_whose_answer_was_lost(fake, meeting):
    lost = []

    def lose_first_complete_answer(request):
        if request.url.path == "/uploads/complete" and not lost:
            lost.append(1)
            fake.post_uploads_complete(None, json.loads(request.content))  # done on the server…
            raise httpx.ReadTimeout("answer lost")  # …but the answer never arrives
        return None
    fake.fault = lose_first_complete_answer
    assert backup(fake, meeting) == 5


def test_free_deletes_only_recordings_that_are_safely_in_the_cloud(fake, meeting):
    backup(fake, meeting)
    os.utime(meeting / "system.m4a", ns=(1, 1))  # changed since the backup
    del fake.objects[f"{meeting.name}/mic.m4a"]  # gone from the cloud
    freed, kept = run(lambda api, f, j: cloud.free(api, f), fake, meeting, "free")
    assert freed == 1000 * KiB + 5
    assert sorted(kept) == ["mic.m4a", "system.m4a"]
    assert not (meeting / "display-1.mp4").exists()
    assert (meeting / "transcript.json").exists() and (meeting / "screens/00-01 slide.png").exists()


def test_restore_brings_back_what_is_missing_and_resumes_a_cut_download(fake, meeting):
    backup(fake, meeting)
    original = {rel: (meeting / rel).read_bytes() for rel in ("display-1.mp4", "mic.m4a", "system.m4a", "transcript.json")}
    run(lambda api, f, j: cloud.free(api, f), fake, meeting, "free")
    (meeting / "transcript.json").unlink()
    cuts = []

    class Cut(httpx.SyncByteStream):
        def __iter__(self):
            yield b"z" * 10 * KiB
            raise httpx.ReadError("connection reset")

    def cut_some_ranges(request):  # every third range breaks off once, after 10 KiB
        if request.url.host == "s3.test" and request.method == "GET" and len(cuts) < 6 and random.random() < 0.34:
            cuts.append(request.headers["range"])
            return httpx.Response(206, stream=Cut())
        return None
    fake.fault = cut_some_ranges
    assert run(lambda api, f, j: cloud.restore(api, f, [], j), fake, meeting, "restore") == 4
    assert cuts and all((meeting / rel).read_bytes() == data for rel, data in original.items())
    assert not list(meeting.glob("*.part"))
    puts = fake.puts
    assert backup(fake, meeting) == 0 and fake.puts == puts  # restored files count as backed up


def test_a_killed_restore_goes_on_from_the_ranges_it_finished(fake, meeting):
    backup(fake, meeting)
    original = (meeting / "display-1.mp4").read_bytes()
    (meeting / "display-1.mp4").unlink()
    fetched = []

    def crash_after_five(request):
        if request.url.host == "s3.test" and request.method == "GET":
            if len(fetched) == 5:
                raise Crash()
            fetched.append(request.headers["range"])
        return None
    fake.fault = crash_after_five
    with pytest.raises(Crash):
        run(lambda api, f, j: cloud.restore(api, f, [], j), fake, meeting, "restore")
    finished = json.loads((meeting / "display-1.mp4.part.json").read_text())["done"]
    assert finished and not (meeting / "display-1.mp4").exists()
    again = []
    fake.fault = lambda request: again.append(request.headers.get("range")) if request.url.host == "s3.test" else None
    run(lambda api, f, j: cloud.restore(api, f, [], j), fake, meeting, "restore")
    assert len(again) == 16 - len(finished)  # only the ranges not finished before
    assert (meeting / "display-1.mp4").read_bytes() == original
    assert not (meeting / "display-1.mp4.part").exists() and not (meeting / "display-1.mp4.part.json").exists()


def test_a_damaged_download_is_thrown_away(fake, meeting):
    backup(fake, meeting)
    (meeting / "mic.m4a").unlink()
    fake.objects[f"{meeting.name}/mic.m4a"]["data"] = b"Y" + fake.objects[f"{meeting.name}/mic.m4a"]["data"][1:]
    with pytest.raises(cloud.CloudError, match="came down damaged"):
        run(lambda api, f, j: cloud.restore(api, f, ["mic.m4a"], j), fake, meeting, "restore")
    assert not (meeting / "mic.m4a").exists() and not (meeting / "mic.m4a.part").exists()


def test_sync_lists_cloud_only_meetings_and_forget_removes_them(fake, meeting, tmp_path):
    backup(fake, meeting)
    fake.objects["2026-10-01_0900/display-1.mp4"] = {"data": b"v" * 10, "etag": "abc-1", "partSize": cloud.PART}
    assert cloud.sync(api_for(fake), tmp_path) == {"meetings": 2, "bytes": sum(len(o["data"]) for o in fake.objects.values())}
    stub = tmp_path / "2026-10-01_0900"
    assert [p.name for p in stub.iterdir()] == [cloud.STATE]
    assert cloud.load(stub)["files"] == {"display-1.mp4": {"size": 10, "etag": "abc-1"}}

    assert cloud.forget(api_for(fake), stub) is True  # nothing here but the records
    assert cloud.forget(api_for(fake), meeting) is False
    assert fake.objects == {} and cloud.load(meeting)["files"] == {}
    fake.objects["2026-10-01_0900/x.mp4"] = {"data": b"v", "etag": "e-1", "partSize": cloud.PART}
    cloud.sync(api_for(fake), tmp_path)
    del fake.objects["2026-10-01_0900/x.mp4"]
    cloud.sync(api_for(fake), tmp_path)
    assert not stub.exists() and meeting.exists()


def test_wrong_token_and_missing_setup_say_what_to_do(fake):
    with pytest.raises(cloud.CloudError, match="Settings → Cloud backup"):
        cloud.Api("", "")
    bad = cloud.Api("https://api.test", "wrong", transport=httpx.MockTransport(fake.handle))
    with pytest.raises(cloud.CloudError, match="refused the token"):
        bad.meetings()


def test_part_size_keeps_every_file_under_ten_thousand_parts():
    assert cloud.part_size(6 * 1024**3) == cloud.PART
    for size in (200 * 1024**3, 4 * 1024**4):
        assert -(-size // cloud.part_size(size)) <= 9000


def test_a_busy_folder_is_left_to_its_job_and_a_stale_one_is_taken_over(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    first = job.Jobs("backup", [folder], every=0.05)
    assert job.running(folder)
    assert job.Jobs("restore", [folder]).skipped == [folder]
    first.ended.set()  # a killed process: the file stops being refreshed
    stale = json.loads((folder / job.FILE).read_text())
    stale["updatedAt"] = "2020-01-01T00:00:00Z"
    (folder / job.FILE).write_text(json.dumps(stale))
    assert not job.running(folder)
    taken = job.Jobs("restore", [folder], every=0.05)
    assert taken.folders == [folder]
    taken.close()
    assert not (folder / job.FILE).exists()


def test_a_job_that_fails_says_why_in_job_json(tmp_path, monkeypatch):
    from transcriber import commands
    folder = tmp_path / "2026-11-11_1500"
    folder.mkdir()
    monkeypatch.setenv("ANYMEET_CLOUD_URL", "")
    with pytest.raises(cloud.CloudError):
        commands._backup(type("Args", (), {"folders": [folder]}))
    assert "Settings → Cloud backup" in job.read(folder)["error"]

    def vanished(api, folder, jobs):
        raise FileNotFoundError(2, "No such file or directory", str(folder / "mic.m4a"))
    monkeypatch.setenv("ANYMEET_CLOUD_URL", "https://api.test")
    monkeypatch.setenv("ANYMEET_CLOUD_TOKEN", TOKEN)
    assert commands._each("backup", [folder], vanished) == 1
    assert job.read(folder)["error"].startswith("No such file or directory: ")


def test_sync_resume_finishes_cut_off_backups_and_downloads(fake, meeting, tmp_path, monkeypatch):
    """What happens at the next launch after quitting (or a crash) mid-job."""
    from argparse import Namespace
    from transcriber import commands
    backup(fake, meeting)
    original = (meeting / "display-1.mp4").read_bytes()
    (meeting / "display-1.mp4").unlink()
    other = tmp_path / "2026-11-11_1600"
    other.mkdir()
    (other / "mic.m4a").write_bytes(os.urandom(300 * KiB))
    calls = []

    def crash_after_two(request):
        if request.url.host == "s3.test":
            if len(calls) == 2:
                raise Crash()
            calls.append(1)
    fake.fault = crash_after_two
    with pytest.raises(Crash):
        run(lambda api, f, j: cloud.restore(api, f, [], j), fake, meeting, "restore")
    calls.clear()
    with pytest.raises(Crash):
        backup(fake, other)
    assert (meeting / "display-1.mp4.part.json").exists() and cloud.load(other)["uploads"]

    fake.fault = lambda request: None
    monkeypatch.setattr(commands, "_api", lambda jobs=None: api_for(fake, jobs))
    assert commands._sync(Namespace(root=tmp_path, resume=True)) == 0
    assert (meeting / "display-1.mp4").read_bytes() == original
    assert fake.objects[f"{other.name}/mic.m4a"]["data"] == (other / "mic.m4a").read_bytes()
