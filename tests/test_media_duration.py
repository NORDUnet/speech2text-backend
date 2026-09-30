import io
import shutil
import subprocess
import wave

import pytest
from utils.crypto import generate_rsa_keypair, encrypt_data_to_file
from utils.media_duration import _probe
from utils.settings import get_settings


def test_encrypted_media_probe_rounds_up_and_removes_scratch(tmp_path, monkeypatch):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe is required for the media integration test")
    monkeypatch.setattr(get_settings(), "FFPROBE_PATH", ffprobe)
    monkeypatch.setattr(get_settings(), "UPLOAD_TMP_DIR", str(tmp_path))
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 10000)
    private, public = generate_rsa_keypair()
    path = tmp_path / "encrypted"
    encrypt_data_to_file(public, audio.getvalue(), path)
    assert _probe(path, private) == 2
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_media_does_not_leave_plaintext(tmp_path, monkeypatch):
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("ffprobe is required")
    monkeypatch.setattr(get_settings(), "FFPROBE_PATH", ffprobe)
    monkeypatch.setattr(get_settings(), "UPLOAD_TMP_DIR", str(tmp_path))
    private, public = generate_rsa_keypair()
    path = tmp_path / "encrypted"
    encrypt_data_to_file(public, b"invalid media", path)
    with pytest.raises((ValueError, subprocess.SubprocessError)):
        _probe(path, private)
    assert list(tmp_path.iterdir()) == [path]
