"""Normalization and scoring. CER is the primary metric: Bangla word spacing is
inconsistent, so WER over-penalizes correct letters split differently."""
import re
import unicodedata

import jiwer

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_ZERO_WIDTH = re.compile("[​‌‍﻿]")
_SPACES = re.compile(r"\s+")
_NOT_BN = re.compile(r"[^ঀ-৿ ]")
_NOT_LATIN = re.compile(r"[^a-z0-9 ]")


def normalize(text: str) -> str:
    text = _ZERO_WIDTH.sub("", unicodedata.normalize("NFC", text))
    text = text.translate(_BN_DIGITS).lower()
    # Drop punctuation and symbols by Unicode category; a \w regex would also
    # strip Bangla vowel signs, which are combining marks, not letters.
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text)
    return _SPACES.sub(" ", text).strip()


def _only(pattern: re.Pattern, text: str) -> str:
    return _SPACES.sub(" ", pattern.sub(" ", text)).strip()


def score(refs: list[str], hyps: list[str]) -> dict:
    """Corpus-level rates. cer_bn / cer_en score only the Bangla-script or
    Latin-script parts, which shows how each provider handles code-mixing."""
    refs = [normalize(r) for r in refs]
    hyps = [normalize(h) for h in hyps]
    result = {"cer": jiwer.cer(refs, hyps), "wer": jiwer.wer(refs, hyps)}
    for name, pattern in (("cer_bn", _NOT_BN), ("cer_en", _NOT_LATIN)):
        pairs = [(_only(pattern, r), _only(pattern, h)) for r, h in zip(refs, hyps)]
        pairs = [p for p in pairs if p[0]]
        result[name] = jiwer.cer(*map(list, zip(*pairs))) if pairs else None
    return result
