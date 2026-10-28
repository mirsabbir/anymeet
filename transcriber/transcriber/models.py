"""Local models: the files each one needs, whether they are on this computer, and
downloading them with progress for the apps' Settings page. Only huggingface_hub is
imported here, so the apps can ask without loading torch."""
import sys
from dataclasses import dataclass
from importlib import metadata
from typing import Callable

# What transformers' Whisper pipeline reads from a model repository.
WHISPER_FILES = ("config.json", "generation_config.json", "preprocessor_config.json", "tokenizer.json",
                 "tokenizer_config.json", "vocab.json", "merges.txt", "added_tokens.json",
                 "special_tokens_map.json", "normalizer.json", "model.safetensors")


@dataclass(frozen=True)
class LocalModel:
    repo: str
    language: str = ""
    base: str | None = None  # older fine-tunes ship a generation config without language tokens
    names: tuple[str, ...] = WHISPER_FILES

    def files(self) -> list[tuple[str, str]]:
        files = [(self.repo, f) for f in self.names]
        return files + [(self.base, "generation_config.json")] if self.base else files


LOCAL = {
    # Bangla fine-tune of Whisper-medium; best local model in benchmark/. Writes English words in Bangla letters.
    "local-whisper-bangla": LocalModel("bengaliAI/tugstugi_bengaliai-asr_whisper-medium", "bengali", "openai/whisper-medium"),
    # English only: Bangla speech comes out translated or garbled.
    "local-whisper-english": LocalModel("openai/whisper-large-v3-turbo", "english"),
}
# Tells apart the people in the system audio (speakers.py). Gated: the user accepts its
# terms on huggingface.co once and saves a token (HF_TOKEN) in the app's settings.
SPEAKERS = LocalModel("pyannote/speaker-diarization-community-1", names=(
    "config.yaml", "segmentation/pytorch_model.bin", "embedding/pytorch_model.bin",
    "plda/plda.npz", "plda/xvec_transform.npz"))
SPEAKERS_ID = "speakers-pyannote"
DOWNLOADS = {**LOCAL, SPEAKERS_ID: SPEAKERS}
GPU_SUPPORT_ID = "gpu-support"  # Windows: PyTorch built for CUDA, installed by uv, not downloaded here


def downloaded(model: LocalModel) -> bool:
    from huggingface_hub import try_to_load_from_cache

    return all(isinstance(try_to_load_from_cache(repo, f), str) for repo, f in model.files())


def status() -> list[dict]:
    items = [{"id": name, "downloaded": downloaded(model)} for name, model in DOWNLOADS.items()]
    if sys.platform == "win32":
        items.append({"id": GPU_SUPPORT_ID, "downloaded": _has_cuda_torch()})
    return items


def _has_cuda_torch() -> bool:
    try:
        return "+cu" in metadata.version("torch")
    except metadata.PackageNotFoundError:
        return False


def download(model: LocalModel, report: Callable[[int, int], None]) -> None:
    """Fetches the files that aren't cached yet, calling report(done_bytes, total_bytes) as bytes arrive."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError
    from tqdm.auto import tqdm

    files = model.files()
    try:
        infos = [hf_hub_download(repo, f, dry_run=True) for repo, f in files]
    except (GatedRepoError, HfHubHTTPError) as e:
        status = getattr(e.response, "status_code", None)
        if isinstance(e, GatedRepoError) or status in (401, 403):
            raise SystemExit(f"Hugging Face refused the download ({status}): accept the terms at "
                             f"https://huggingface.co/{model.repo} and save a token in Settings") from None
        raise
    total = sum(i.file_size for i in infos)
    done = sum(i.file_size for i in infos if not i.will_download)
    report(done, total)
    for (repo, f), info in zip(files, infos):
        if not info.will_download:
            continue
        got = 0

        class Bar(tqdm):
            def update(self, n=1):
                nonlocal got
                result = super().update(n)
                got = max(got, min(self.n, info.file_size))  # several bars may report the same file
                report(done + got, total)
                return result

        hf_hub_download(repo, f, tqdm_class=Bar)
        done += info.file_size
        report(done, total)
