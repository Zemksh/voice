import math
import struct
import tempfile
import wave

import pytest
from pathlib import Path

from backend.app.config import Settings
from backend.app.inference import PracticeError, convert_audio


def make_wav(path: Path, seconds: float = 1.0, tone_hz: float = 440.0, sample_rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = int(sample_rate * seconds)
        for i in range(frames):
            value = int(9000 * math.sin(2 * math.pi * tone_hz * i / sample_rate))
            w.writeframes(struct.pack("<h", value))


def settings_for(tmp: Path, max_duration: int = 180) -> Settings:
    return Settings(
        tmp / "data",
        tmp / "models" / "small.en",
        gemma_model="gemma3:1b",
        max_duration=max_duration,
        mock=True,
    )


def test_convert_valid_wav(tmp_path: Path):
    src = tmp_path / "source.wav"
    make_wav(src, seconds=1.0)
    settings = settings_for(tmp_path)
    out = tmp_path / "out" / "normalized.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    duration = convert_audio(src, out, settings)
    assert out.is_file()
    assert 0.9 < duration < 1.1


def test_convert_enforces_duration_limit(tmp_path: Path):
    src = tmp_path / "source.wav"
    make_wav(src, seconds=5.0)
    settings = settings_for(tmp_path, max_duration=1)
    out = tmp_path / "out" / "normalized.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PracticeError, match="exceeds"):
        convert_audio(src, out, settings)


def test_convert_invalid_audio_rejected(tmp_path: Path):
    src = tmp_path / "noise.wav"
    src.write_bytes(b"not really a wav file at all" * 10)
    settings = settings_for(tmp_path)
    out = tmp_path / "out" / "normalized.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(PracticeError, match="decoded"):
        convert_audio(src, out, settings)


def test_convert_empty_file_rejected(tmp_path: Path):
    src = tmp_path / "empty.wav"
    src.write_bytes(b"")
    settings = settings_for(tmp_path)
    out = tmp_path / "out" / "normalized.wav"
    with pytest.raises(PracticeError):
        convert_audio(src, out, settings)