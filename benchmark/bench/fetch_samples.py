"""Download a few Google FLEURS Bengali test clips as a smoke test.

FLEURS is read Wikipedia sentences, not meetings: it proves the pipeline works,
it does not tell us which provider is best for our audio.

    uv run python -m bench.fetch_samples -n 5"""
import argparse
import csv
import io
import tarfile

import requests

from .run import CLIPS

BASE = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/bn_in"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    n = ap.parse_args().n
    tsv = requests.get(f"{BASE}/test.tsv", timeout=60).text
    refs = {row[1]: row[2] for row in csv.reader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE)}
    CLIPS.mkdir(parents=True, exist_ok=True)
    # The archive is ~660 MB; stream it and stop after n clips.
    with requests.get(f"{BASE}/audio/test.tar.gz", stream=True, timeout=60) as resp:
        resp.raise_for_status()
        saved = 0
        with tarfile.open(fileobj=resp.raw, mode="r|gz") as tar:
            for member in tar:
                name = member.name.rsplit("/", 1)[-1]
                if not member.isfile() or name not in refs:
                    continue
                stem = f"fleurs_{name.removesuffix('.wav')}"
                (CLIPS / f"{stem}.wav").write_bytes(tar.extractfile(member).read())
                (CLIPS / f"{stem}.txt").write_text(refs[name], encoding="utf-8")
                saved += 1
                print(stem, refs[name][:60])
                if saved == n:
                    break


if __name__ == "__main__":
    main()
