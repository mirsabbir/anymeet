r"""transcribe <audio file or meeting folder> [--glossary FILE] [--transcriber NAME] [--live]
transcribe --models | --download MODEL

Settings: API keys in <config>/.env, the glossary in <config>/glossary.txt, where
<config> is ~/Library/Application Support/AnyMeet (macOS),
%APPDATA%\AnyMeet (Windows) or ~/.config/anymeet.

A file is one speaker. In a folder, mic.* is "Me", system.* is "Others" and
any other audio file is labelled with its own name. Output goes next to the
input: <file>.transcript.md/.json, or transcript.md/.json inside the folder."""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from . import base, live, models, pipeline

AUDIO_EXT = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".opus", ".aac", ".webm", ".mp4", ".mov"}
SPEAKERS = {"mic": "Me", "system": "Others"}
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
    ap.add_argument("--download", metavar="MODEL", choices=models.LOCAL,
                    help="download a local model, printing progress as JSON lines")
    args = ap.parse_args()
    if args.models:
        print(json.dumps(models.status()))
        return
    if args.download:
        models.download(models.LOCAL[args.download], _progress_printer())
        return
    if args.path is None:
        ap.error("path is required")

    transcriber = base.get(args.transcriber)
    glossary = _glossary(args.glossary)
    started = time.monotonic()
    workers = getattr(transcriber, "max_workers", 8)
    if args.live:
        folder = args.path.expanduser().resolve()
        print(f"{transcriber.name}: live, {len(glossary)} glossary term(s)", flush=True)
        failed = live.run(folder, SPEAKERS, transcriber, glossary, workers=workers)
        print(f"live transcript done in {time.monotonic() - started:.0f}s -> {folder / 'transcript'}.md")
        if failed:
            print(f"WARNING: {failed} segment(s) could not be transcribed; they are marked ⚠️ in the transcript")
            sys.exit(1)
        return
    tracks, out = _tracks(args.path.expanduser().resolve())
    print(f"{transcriber.name}: {len(tracks)} track(s), {len(glossary)} glossary term(s)")
    segments, length = pipeline.transcribe_tracks(tracks, transcriber, glossary, workers=workers)
    failed = pipeline.write(segments, length, transcriber.name, args.path.stem, out)
    print(f"{len(segments)} segment(s) from {length / 60:.1f} min of audio in "
          f"{time.monotonic() - started:.0f}s -> {out}.md")
    if failed:
        print(f"WARNING: {failed} segment(s) could not be transcribed; they are marked ⚠️ in the transcript")
        sys.exit(1)


if __name__ == "__main__":
    main()
