"""Local models: the files each one needs, whether they are on this computer, and
downloading them with progress for the apps' Settings page. Only huggingface_hub is
imported here, so the apps can ask without loading torch."""
from dataclasses import dataclass
from typing import Callable

# What transformers' Whisper pipeline reads from a model repository.
WHISPER_FILES = ("config.json", "generation_config.json", "preprocessor_config.json", "tokenizer.json",
                 "tokenizer_config.json", "vocab.json", "merges.txt", "added_tokens.json",
                 "special_tokens_map.json", "normalizer.json", "model.safetensors")


@dataclass(frozen=True)
class LocalModel:
    repo: str
    language: str
    base: str | None = None  # older fine-tunes ship a generation config without language tokens

    def files(self) -> list[tuple[str, str]]:
        files = [(self.repo, f) for f in WHISPER_FILES]
        return files + [(self.base, "generation_config.json")] if self.base else files


LOCAL = {
    # Bangla fine-tune of Whisper-medium; best local model in benchmark/. Writes English words in Bangla letters.
    "local-whisper-bangla": LocalModel("bengaliAI/tugstugi_bengaliai-asr_whisper-medium", "bengali", "openai/whisper-medium"),
    # English only: Bangla speech comes out translated or garbled.
    "local-whisper-english": LocalModel("openai/whisper-large-v3-turbo", "english"),
}


def downloaded(model: LocalModel) -> bool:
    from huggingface_hub import try_to_load_from_cache

    return all(isinstance(try_to_load_from_cache(repo, f), str) for repo, f in model.files())


def status() -> list[dict]:
    return [{"id": name, "downloaded": downloaded(model)} for name, model in LOCAL.items()]


def download(model: LocalModel, report: Callable[[int, int], None]) -> None:
    """Fetches the files that aren't cached yet, calling report(done_bytes, total_bytes) as bytes arrive."""
    from huggingface_hub import hf_hub_download
    from tqdm.auto import tqdm

    files = model.files()
    infos = [hf_hub_download(repo, f, dry_run=True) for repo, f in files]
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
