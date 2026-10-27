"""Transcribe every clip in data/clips with each provider, cache the output and
print a scored table.

    uv run python -m bench.run --list
    uv run python -m bench.run                       # every provider that has a key
    uv run python -m bench.run -p soniox,sarvam-v4   # just these

A clip is any audio file in data/clips; its reference is the .txt file with the
same name. Clips without a reference are transcribed but not scored."""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from . import audio
from .providers import PROVIDERS, Provider, available, describe
from .text import score

ROOT = Path(__file__).resolve().parent.parent
CLIPS, WORK, RESULTS = ROOT / "data" / "clips", ROOT / "work", ROOT / "results"
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".mp4"}


def transcribe_all(p: Provider, wavs: list[Path]) -> None:
    out_dir = RESULTS / p.name
    out_dir.mkdir(parents=True, exist_ok=True)
    for wav in wavs:
        out = out_dir / f"{wav.stem}.txt"
        if out.exists():
            continue
        try:
            started = time.monotonic()
            parts = audio.split(wav, p.max_seconds, WORK / "chunks") if p.max_seconds else [wav]
            text = " ".join(p.transcribe(part).strip() for part in parts).strip()
            if not text:
                raise RuntimeError("empty transcript")
            out.write_text(text, encoding="utf-8")
            (out_dir / f"{wav.stem}.error").unlink(missing_ok=True)
            print(f"[{p.name}] {wav.stem}: {time.monotonic() - started:.1f}s, {len(parts)} part(s)")
        except Exception as e:  # keep going; the clip shows up as failed in the table
            (out_dir / f"{wav.stem}.error").write_text(str(e), encoding="utf-8")
            print(f"[{p.name}] {wav.stem}: FAILED {e}")


def report(names: list[str], wavs: list[Path]) -> str:
    minutes = sum(audio.duration(w) for w in wavs) / 60
    rows = []
    for name in names:
        refs, hyps, failed = [], [], 0
        for wav in wavs:
            ref, hyp = CLIPS / f"{wav.stem}.txt", RESULTS / name / f"{wav.stem}.txt"
            if not ref.exists():
                continue
            if not hyp.exists():
                failed += 1
                continue
            refs.append(ref.read_text(encoding="utf-8"))
            hyps.append(hyp.read_text(encoding="utf-8"))
        if refs or failed:
            rows.append((name, score(refs, hyps) if refs else dict.fromkeys(["cer", "wer", "cer_bn", "cer_en"]), len(refs), failed))
    rows.sort(key=lambda r: float("inf") if r[1]["cer"] is None else r[1]["cer"])

    def pct(v):
        return "–" if v is None else f"{v:.1%}"

    lines = [
        f"Audio scored: {minutes:.1f} min. Lower is better; CER is the primary metric.", "",
        "| # | Provider | CER | WER | CER Bangla part | CER English part | Clips | Failed | $/hour |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (name, s, n, failed) in enumerate(rows, 1):
        price = PROVIDERS[name].usd_per_hour
        lines.append(
            f"| {i} | {name} | {pct(s['cer'])} | {pct(s['wer'])} | {pct(s['cer_bn'])} | "
            f"{pct(s['cer_en'])} | {n} | {failed} | {'?' if price is None else f'{price:.2f}'} |"
        )
    return "\n".join(lines)


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--providers", help="comma-separated names; default: all with a key")
    ap.add_argument("--list", action="store_true", help="show providers and key status")
    args = ap.parse_args()
    if args.list:
        print(describe())
        return

    names = args.providers.split(",") if args.providers else [n for n, p in PROVIDERS.items() if available(p) and p.key_env]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise SystemExit(f"unknown provider(s): {unknown}. Try --list")
    sources = sorted(f for f in CLIPS.iterdir() if f.suffix.lower() in AUDIO_EXT)
    if not sources:
        raise SystemExit(f"no audio in {CLIPS}")
    wavs = [audio.to_wav(f, WORK / "wav" / f"{f.stem}.wav") for f in sources]

    # Providers run in parallel, clips within one provider run in order.
    with ThreadPoolExecutor(len(names)) as pool:
        list(pool.map(lambda n: transcribe_all(PROVIDERS[n], wavs), names))

    table = report(names, wavs)
    (RESULTS / "report.md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)


if __name__ == "__main__":
    main()
