"""Whisper running on this computer (Apple GPU through MPS, else CPU). No API key,
no internet after the first model download (~1.5 GB, cached by Hugging Face).

Needs the optional dependencies: `uv sync --extra local` (the desktop apps pass
`--extra local` when a local model is selected)."""
from pathlib import Path


class LocalWhisperTranscriber:
    max_workers = 1  # one model in memory; parallel calls would only fight over the GPU

    def __init__(self, name: str, model: str, base: str | None = None):
        try:
            import torch
            from transformers import GenerationConfig, pipeline
        except ImportError:
            raise SystemExit("Local model not installed: run `uv sync --extra local` in the transcriber folder")
        self.name = name
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.pipe = pipeline("automatic-speech-recognition", model=model, device=device,
                             dtype=torch.float16 if device == "mps" else torch.float32)
        if base:  # older fine-tunes ship a generation config without language tokens
            self.pipe.model.generation_config = GenerationConfig.from_pretrained(base)

    def transcribe(self, audio: Path, glossary: list[str]) -> str:
        # The glossary is not used: Whisper's text prompt tends to leak into the output.
        result = self.pipe(str(audio), chunk_length_s=30,
                           generate_kwargs={"language": "bengali", "task": "transcribe"})
        return result["text"].strip()
