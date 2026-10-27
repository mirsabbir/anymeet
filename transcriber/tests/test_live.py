"""Live mode: audio files that grow while the test runs, the way the apps write them."""
import json
import struct
import threading
import time

import numpy as np

from transcriber import live
from tests.test_pipeline import FakeTranscriber


def wav_header(rate: int, channels: int, float32: bool) -> bytes:
    """Header with zero sizes, as a writer that hasn't closed the file yet leaves it."""
    bits = 32 if float32 else 16
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", 3 if float32 else 1, channels, rate, rate * block, block, bits)
    return b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", 0)


def tone(seconds: float, rate: int) -> np.ndarray:
    return (0.3 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * rate)) / rate)).astype(np.float32)


def quiet(seconds: float, rate: int) -> np.ndarray:
    return np.zeros(int(seconds * rate), np.float32)


def encode(samples: np.ndarray, channels: int, float32: bool) -> bytes:
    interleaved = np.repeat(samples, channels)
    return interleaved.astype("<f4").tobytes() if float32 else (interleaved * 32767).astype("<i2").tobytes()


def test_growing_wav_reads_appended_audio_as_16k_mono(tmp_path):
    path = tmp_path / "system.wav"
    path.write_bytes(wav_header(48_000, 2, float32=True))
    reader = live.GrowingWav(path)
    assert len(reader.read_new()) == 0
    with path.open("ab") as f:
        f.write(encode(tone(1.0, 48_000), 2, True))
    first = reader.read_new()
    with path.open("ab") as f:
        f.write(encode(tone(0.5, 48_000), 2, True))
    second = reader.read_new()
    assert abs(len(first) - 16_000) <= 2 and abs(len(second) - 8_000) <= 2
    assert 0.25 < float(np.abs(first).max()) < 0.35


def test_growing_wav_reads_pcm16(tmp_path):
    path = tmp_path / "mic.wav"
    path.write_bytes(wav_header(16_000, 1, float32=False) + encode(tone(0.5, 16_000), 1, False))
    samples = live.GrowingWav(path).read_new()
    assert len(samples) == 8_000 and 0.25 < float(np.abs(samples).max()) < 0.35


def test_speech_regions_in_memory():
    samples = np.concatenate([tone(2, 16_000), quiet(1.5, 16_000), tone(3, 16_000)])
    regions = live._speech_regions(samples)
    assert len(regions) == 2
    assert regions[0][1] < 2.1 and 3.4 < regions[1][0] < 3.6


def test_live_run_sends_chunks_during_the_recording(tmp_path):
    folder = tmp_path / "meeting"
    (folder / "live").mkdir(parents=True)
    mic, system = folder / "live" / "mic.wav", folder / "live" / "system.wav"
    mic.write_bytes(wav_header(48_000, 1, float32=True))
    system.write_bytes(wav_header(48_000, 2, float32=True))
    # Me speaks at 0-2 s, Others at 4-7 s; each piece is appended over time like a recorder would.
    mic_audio = np.concatenate([tone(2, 48_000), quiet(8, 48_000)])
    system_audio = np.concatenate([quiet(4, 48_000), tone(3, 48_000), quiet(3, 48_000)])
    sent_at, stopped_at = [], {}

    class TimedFake(FakeTranscriber):
        def transcribe(self, audio, glossary):
            sent_at.append(time.monotonic())
            return super().transcribe(audio, glossary)

    def record():
        step = 4_800  # 0.1 s
        for i in range(0, len(mic_audio), step):
            with mic.open("ab") as f:
                f.write(encode(mic_audio[i:i + step], 1, True))
            with system.open("ab") as f:
                f.write(encode(system_audio[i:i + step], 2, True))
            time.sleep(0.01)  # ~10x faster than real time
        stopped_at["t"] = time.monotonic()
        (folder / "live" / "done").touch()

    recorder = threading.Thread(target=record)
    recorder.start()
    failed = live.run(folder, {"mic": "Me", "system": "Others"}, TimedFake("আমি", "Others"), ["API"], workers=1, poll=0.05)
    recorder.join()

    assert failed == 0
    assert len(sent_at) == 2
    assert sent_at[0] < stopped_at["t"], "first chunk must be sent before recording stops"
    data = json.loads((folder / "transcript.json").read_text(encoding="utf-8"))
    assert [s["speaker"] for s in data["segments"]] == ["Me", "Others"]
    assert abs(data["segments"][0]["start"]) < 0.1 and 3.8 < data["segments"][1]["start"] < 4.2
    assert 9.5 < data["duration"] < 10.5
    assert not (folder / "live").exists()


def test_live_run_writes_progress_before_the_end(tmp_path):
    folder = tmp_path / "meeting"
    (folder / "live").mkdir(parents=True)
    mic = folder / "live" / "mic.wav"
    mic.write_bytes(wav_header(16_000, 1, float32=False) + encode(np.concatenate([tone(2, 16_000), quiet(3, 16_000)]), 1, False))
    seen = {}

    def finish_later():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (folder / "transcript.json").exists():
            time.sleep(0.02)
        seen["progress"] = (folder / "transcript.json").exists()
        (folder / "live" / "done").touch()

    threading.Thread(target=finish_later).start()
    live.run(folder, {"mic": "Me"}, FakeTranscriber("প্রথম"), [], workers=1, poll=0.05)
    assert seen["progress"], "transcript.json should appear while still recording"


def test_growing_wav_reads_windows_extensible_float(tmp_path):
    """WASAPI writes WAVE_FORMAT_EXTENSIBLE; the real format (float) is in the SubFormat GUID."""
    rate, channels = 48_000, 2
    float_guid = struct.pack("<IHH", 3, 0x0000, 0x0010) + bytes([0x80, 0x00, 0x00, 0xAA, 0x00, 0x38, 0x9B, 0x71])
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * 8, 8, 32, 22, 32, 3) + float_guid
    header = b"RIFF" + struct.pack("<I", 0) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 0)
    path = tmp_path / "system.wav"
    path.write_bytes(header + encode(tone(1.0, rate), channels, True))
    samples = live.GrowingWav(path).read_new()
    assert abs(len(samples) - 16_000) <= 2 and 0.25 < float(np.abs(samples).max()) < 0.35
