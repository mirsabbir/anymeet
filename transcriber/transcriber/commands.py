r"""anymeet backup <folder>...             send what is new or changed to the cloud
anymeet restore <folder> [--files P…]   download from the cloud what is missing here (or the files named)
anymeet free <folder>...               delete the recordings here that are safely in the cloud
anymeet forget <folder>...             delete the meetings' cloud copies; this computer's copies stay
anymeet sync <meetings folder> [--resume]  match every meeting to the cloud; --resume then finishes
                                         backups and downloads that were cut off
anymeet check                          test the server address and token
anymeet summarize <folder> [--language auto|en|bn] [--backup]

The server address and token are ANYMEET_CLOUD_URL and ANYMEET_CLOUD_TOKEN, and the Claude key
ANTHROPIC_API_KEY, in the settings .env (see transcriber.cli). Jobs on a meeting folder report in
<folder>/job.json (job.py); check and sync print JSON. The last line of a failure is its reason."""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

from . import cloud, job, summary
from .cli import PROJECT, config_dir


def main() -> None:
    load_dotenv(config_dir() / ".env")
    load_dotenv(PROJECT / ".env")  # developer fallback; never overrides the user's settings
    args = _parser().parse_args()
    try:
        failed = args.run(args)
    except (cloud.CloudError, summary.SummaryError) as e:
        sys.exit(str(e))
    if failed:
        sys.exit(f"{failed} meeting(s) failed; each meeting page says why.")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="anymeet", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    many = {"type": Path, "nargs": "+", "help": "meeting folders"}
    sub.add_parser("backup").add_argument("folders", **many)
    restore = sub.add_parser("restore")
    restore.add_argument("folder", type=Path)
    restore.add_argument("--files", nargs="+", default=[], help="paths inside the meeting folder")
    sub.add_parser("free").add_argument("folders", **many)
    sub.add_parser("forget").add_argument("folders", **many)
    sync = sub.add_parser("sync")
    sync.add_argument("root", type=Path)
    sync.add_argument("--resume", action="store_true")
    sub.add_parser("check")
    summarize = sub.add_parser("summarize")
    summarize.add_argument("folder", type=Path)
    summarize.add_argument("--language", choices=summary.LANGUAGES, default="auto")
    summarize.add_argument("--backup", action="store_true", help="back the meeting up afterwards")
    for name, run in [("backup", _backup), ("restore", _restore), ("free", _free), ("forget", _forget),
                      ("sync", _sync), ("check", _check), ("summarize", _summarize)]:
        sub.choices[name].set_defaults(run=run)
    return ap


def _api(jobs: job.Jobs | None = None) -> cloud.Api:
    return cloud.Api(os.environ.get("ANYMEET_CLOUD_URL", ""), os.environ.get("ANYMEET_CLOUD_TOKEN", ""),
                     halt=jobs.stop if jobs else None)


def _each(kind: str, folders: list[Path], work) -> int:
    """work(api, folder, jobs) for each folder in turn, one job file per folder; returns how many failed."""
    jobs = job.Jobs(kind, [f.expanduser().resolve() for f in folders])
    for folder in jobs.skipped:
        print(f"{folder.name}: already busy with another job", flush=True)
    failed = 0
    try:
        api = _api(jobs)
        for folder in jobs.folders:
            failed += _one(api, folder, jobs, work)
    except cloud.CloudError as e:  # not set up: every meeting of the run says so
        for folder in jobs.folders:
            jobs.end(folder, str(e))
        raise
    finally:
        jobs.close()
    return failed


def _one(api: cloud.Api, folder: Path, jobs: job.Jobs, work) -> int:
    if not jobs.begin(folder):
        return 0
    try:
        work(api, folder, jobs)
    except cloud.Stopped:
        print(f"{folder.name}: stopped", flush=True)
    except (cloud.CloudError, summary.SummaryError, OSError) as e:
        reason = f"{e.strerror}: {e.filename}" if isinstance(e, OSError) and e.strerror else str(e)
        print(f"{folder.name}: {reason}", flush=True)
        jobs.end(folder, reason)
        return 1
    jobs.end(folder)
    return 0


def _backup(args) -> int:
    def work(api, folder, jobs):
        print(f"{folder.name}: {cloud.backup(api, folder, jobs)} file(s) backed up", flush=True)
    return _each("backup", args.folders, work)


def _restore(args) -> int:
    def work(api, folder, jobs):
        print(f"{folder.name}: {cloud.restore(api, folder, args.files, jobs)} file(s) downloaded", flush=True)
    return _each("restore", [args.folder], work)


def _free(args) -> int:
    def work(api, folder, _jobs):
        freed, kept = cloud.free(api, folder)
        print(f"{folder.name}: freed {freed / 1e9:.2f} GB", flush=True)
        if kept:
            print(f"{folder.name}: kept, not in the cloud yet: {', '.join(kept)}", flush=True)
    return _each("free", args.folders, work)


def _forget(args) -> int:
    emptied = []

    def work(api, folder, _jobs):
        if cloud.forget(api, folder):
            emptied.append(folder)
        print(f"{folder.name}: deleted from the cloud", flush=True)
    failed = _each("forget", args.folders, work)
    for folder in emptied:  # it held only the cloud's records: the meeting is gone
        shutil.rmtree(folder, ignore_errors=True)
    return failed


def _sync(args) -> int:
    root = args.root.expanduser().resolve()
    print(json.dumps(cloud.sync(_api(), root)), flush=True)
    if not args.resume:
        return 0
    folders = [f for f in root.iterdir() if f.is_dir()]
    uploads = [f for f in folders if cloud.load(f)["uploads"] or (job.read(f) or {}).get("kind") == "backup"]
    downloads = [f for f in folders if next(f.rglob("*.part.json"), None)]
    failed = _backup(argparse.Namespace(folders=uploads)) if uploads else 0
    for folder in downloads:
        failed += _restore(argparse.Namespace(folder=folder, files=[]))
    return failed


def _check(_args) -> int:
    print(json.dumps(cloud.totals(_api().meetings())), flush=True)
    return 0


def _summarize(args) -> int:
    def work(_api, folder, _jobs):
        print(f"{folder.name}: summary “{summary.summarize(folder, args.language)['title']}”", flush=True)
    jobs = job.Jobs("summary", [args.folder.expanduser().resolve()])
    try:
        failed = sum(_one(None, folder, jobs, work) for folder in jobs.folders)
    finally:
        jobs.close()
    if args.backup and os.environ.get("ANYMEET_CLOUD_URL"):  # backed up whether or not the summary worked
        failed += _backup(argparse.Namespace(folders=[args.folder]))
    return failed


if __name__ == "__main__":
    main()
