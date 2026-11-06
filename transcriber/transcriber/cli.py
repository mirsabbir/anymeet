r"""transcribe <audio file or meeting folder> [--glossary FILE] [--transcriber NAME] [--live] [--speakers]
transcribe <meeting folder> --only-failed
transcribe <meeting folder> --rename-speaker OLD NEW
transcribe --models | --download MODEL | --device

Settings: API keys in <config>/.env, the glossary in <config>/glossary.txt, where
<config> is ~/Library/Application Support/AnyMeet (macOS),
%APPDATA%\AnyMeet (Windows) or ~/.config/anymeet.

A file is one speaker. In a folder, mic.* is "Me", system.* is "Others" and
any other audio file is labelled with its own name. Output goes next to the
input: <file>.transcript.md/.json, or transcript.md/.json inside the folder.
--speakers splits "Others" into Speaker 1, Speaker 2… (speakers.py).
--only-failed sends again only the parts the transcript marks ⚠️.

While a folder is being transcribed, <folder>/progress.json says how far it is
(progress.py), and creating <folder>/transcribe.stop stops it: nothing more is
sent, and the parts already sent finish. A whole run that ends with more parts
missing than the transcript it would replace keeps the earlier transcript."""
import argparse
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

from . import base, device, live, models, pipeline, speakers
from .progress import Progress

PROGRESS = "progress.json"
STOP_FILE = "transcribe.stop"
AUDIO_EXT = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".webm", ".mp4", ".mov"}
SPEAKERS = {"mic": "Me", "system": pipeline.OTHERS}
PROJECT = Path(__file__).resolve().parent.parent


def config_dir() -> Path:
    """Per-user settings shared with the desktop apps: .env (API keys) and glossary.txt.
    Nothing user-specific lives in the code or the repository."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AnyMeet"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "AnyMeet"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "anymeet"


def _tracks(path: Path) -> tuple[dict[Path, str], Path]:
    if path.is_file():
        return {path: "Speaker"}, path.with_name(f"{path.stem}.transcript")
    files = sorted(f for f in path.iterdir() if f.suffix.lower() in AUDIO_EXT and not f.stem.startswith("display-"))
    if not files:
        raise SystemExit(f"no audio files in {path}")
    return {f: SPEAKERS.get(f.stem, f.stem) for f in files}, path / "transcript"


def _glossary(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _progress_printer(every: float = 0.25):
    """{"done": bytes, "total": bytes} lines for the apps, at most every `every` seconds, plus the last one."""
    last = -math.inf  # the first line always prints, however recently the machine booted

    def report(done: int, total: int) -> None:
        nonlocal last
        if done < total and time.monotonic() - last < every:
            return
        last = time.monotonic()
        print(json.dumps({"done": done, "total": total}), flush=True)
    return report


def _diarizer(enabled: bool):
    """speakers.diarize when it can run; a problem with it never costs the transcript."""
    if not enabled:
        return None
    if not speakers.available():
        print("WARNING: the speaker model is not downloaded (Settings → Speakers); keeping “Others”", flush=True)
        return None

    def diarize(wav: Path) -> list[speakers.Turn]:
        try:
            return speakers.diarize(wav)
        except Exception as e:
            print(f"WARNING: couldn't tell the speakers apart ({type(e).__name__}: {str(e)[:200]}); keeping “Others”", flush=True)
            return []
    return diarize


def main() -> None:
    load_dotenv(config_dir() / ".env")
    load_dotenv(PROJECT / ".env")  # developer fallback; never overrides the user's settings
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, nargs="?")
    ap.add_argument("--glossary", type=Path, default=config_dir() / "glossary.txt")
    ap.add_argument("--transcriber", default=os.environ.get("TRANSCRIBER", base.DEFAULT))
    ap.add_argument("--live", action="store_true",
                    help="transcribe <folder>/live/*.wav while they are still being recorded (see live.py)")
    ap.add_argument("--models", action="store_true", help="print which local models are downloaded, as JSON")
    ap.add_argument("--download", metavar="MODEL", choices=models.DOWNLOADS,
                    help="download a local model, printing progress as JSON lines")
    ap.add_argument("--speakers", action="store_true", help='tell apart the people in "Others" (needs --extra speakers)')
    ap.add_argument("--only-failed", action="store_true",
                    help="send again only the parts of <folder>/transcript.json that could not be transcribed")
    ap.add_argument("--rename-speaker", nargs=2, metavar=("OLD", "NEW"), help="rename a speaker in a finished transcript")
    ap.add_argument("--device", action="store_true",
                    help="print where local models run, as JSON; exits 1 when the GPU was asked for but can't be used")
    args = ap.parse_args()
    if args.models:
        print(json.dumps(models.status()))
        return
    if args.download:
        models.download(models.DOWNLOADS[args.download], _progress_printer())
        return
    if args.device:
        result = device.check()
        print(json.dumps(result))
        if "problem" in result:
            sys.exit(f"GPU not usable: {result['problem']}")  # stderr: the apps show its last line
    if args.path is None:
        ap.error("path is required")
    if args.rename_speaker:
        _, out = _tracks(args.path.expanduser().resolve())
        changed = pipeline.rename_speaker(out, args.path.expanduser().resolve().stem, *args.rename_speaker)
        print(f"{changed} segment(s) renamed in {out}.md")
        return

    transcriber = base.get(args.transcriber)
    transcriber.stop = pipeline.STOP  # a stopped run doesn't sit out a rate-limit wait
    glossary = _glossary(args.glossary)
    started = time.monotonic()
    workers = getattr(transcriber, "max_workers", 8)
    path = args.path.expanduser().resolve()
    folder = path if path.is_dir() else None
    progress = Progress(folder / PROGRESS if folder else None, getattr(transcriber, "waiting", lambda: 0.0))
    watching = watch_stop(folder) if folder else threading.Event()
    try:
        failed = _run(args, path, transcriber, glossary, workers, progress, started)
    finally:
        watching.set()
        progress.close()
        if folder:
            (folder / STOP_FILE).unlink(missing_ok=True)
    if not failed:
        return
    # The apps show the last line of the log under "N parts were not transcribed", so the
    # last line is only the reason, in words a user can act on.
    print(f"WARNING: {failed} part(s) could not be transcribed; they are marked ⚠️ in the transcript")
    if getattr(transcriber, "exhausted", ""):
        print(transcriber.exhausted)
    elif pipeline.STOP.is_set():
        print("Stopped before every part was sent. Try again sends only the parts not transcribed yet.")
    else:
        print(_main_reason(folder / "transcript" if folder else path.with_name(f"{path.stem}.transcript")))
    sys.exit(1)


def _main_reason(out: Path) -> str:
    """The commonest reason in the transcript for a part not being transcribed."""
    try:
        reasons = [s["failed"] for s in json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))["segments"] if s.get("failed")]
    except (OSError, ValueError, KeyError):
        reasons = []
    if not reasons:
        return "See transcribe.log in the meeting folder for why."
    reason = max(set(reasons), key=reasons.count)
    if reason == "empty transcript":  # pipeline._transcribe: speech-length audio, no words back, twice
        return ("Most of them came back with no words, twice: usually noise, music, or a voice too faint to "
                "make out. Play one to check; trying again rarely changes these.")
    return f"Most of them failed with: {reason}" if reasons.count(reason) > 1 else f"It failed with: {reason}"


def _run(args, path: Path, transcriber, glossary: list[str], workers: int, progress: Progress, started: float) -> int:
    """One run of the kind asked for; returns how many parts are still not transcribed."""
    if args.live:
        print(f"{transcriber.name}: live, {len(glossary)} glossary term(s)", flush=True)
        failed = live.run(path, SPEAKERS, transcriber, glossary, workers=workers, diarize=_diarizer(args.speakers),
                          progress=progress)
        print(f"live transcript done in {time.monotonic() - started:.0f}s -> {path / 'transcript'}.md")
        return failed
    tracks, out = _tracks(path)
    if args.only_failed:
        if not Path(f"{out}.json").exists():
            raise SystemExit("there is no transcript yet: transcribe the whole meeting first")
        print(f"{transcriber.name}: only the parts not transcribed yet", flush=True)
        tried, failed = pipeline.retry_failed(out, {f.stem: f for f in tracks}, transcriber, glossary, path.stem,
                                              workers=workers, progress=progress)
        print(f"{tried - failed} of {tried} part(s) transcribed in {time.monotonic() - started:.0f}s -> {out}.md")
        return failed
    print(f"{transcriber.name}: {len(tracks)} track(s), {len(glossary)} glossary term(s)", flush=True)
    segments, length = pipeline.transcribe_tracks(tracks, transcriber, glossary, workers=workers,
                                                  diarize=_diarizer(args.speakers), progress=progress)
    failed = sum(1 for s in segments if s.failed)
    before = _failed_in(out)
    if before is not None and failed > before:
        # A limit hit, or Stop, part-way through must not cost a transcript that was better.
        print(f"Kept the earlier transcript: it has {before} part(s) not transcribed, this run had {failed}.")
        return before
    pipeline.write(segments, length, transcriber.name, path.stem, out)
    print(f"{len(segments)} segment(s) from {length / 60:.1f} min of audio in "
          f"{time.monotonic() - started:.0f}s -> {out}.md")
    return failed


def _failed_in(out: Path) -> int | None:
    """How many parts of an existing transcript are not transcribed; None if there is none."""
    try:
        return sum(1 for s in json.loads(Path(f"{out}.json").read_text(encoding="utf-8"))["segments"] if s.get("failed"))
    except (OSError, ValueError, KeyError):
        return None


def watch_stop(folder: Path, every: float = 1.0) -> threading.Event:
    """The apps' Stop button writes <folder>/transcribe.stop; the run then sends nothing more
    and ends once the parts already sent are back. A leftover file from before is ignored.
    Returns an event that ends the watching."""
    (folder / STOP_FILE).unlink(missing_ok=True)
    ended = threading.Event()

    def watch() -> None:
        while not ended.wait(every):
            if (folder / STOP_FILE).exists():
                print("Stop asked: finishing the parts already sent", flush=True)
                pipeline.STOP.set()
                return
    threading.Thread(target=watch, daemon=True).start()
    return ended


if __name__ == "__main__":
    main()
