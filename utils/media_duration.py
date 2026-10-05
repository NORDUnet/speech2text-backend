"""Probe encrypted uploads before opening a quota transaction."""
import asyncio
import json
import math
import subprocess
import tempfile

from fastapi import HTTPException
from db.user import user_get, user_get_private_key
from utils.crypto import decrypt_data_from_file, deserialize_private_key_from_pem
from utils.settings import get_settings


_probe_slots = asyncio.Semaphore(2)


def _probe(path, private_key):
    settings = get_settings()
    # Unlinked, mode-0600 scratch file: ffprobe needs seeks for formats such as MP4.
    # The encrypted upload is preserved. Closing the descriptor removes scratch data.
    with tempfile.TemporaryFile(dir=settings.UPLOAD_TMP_DIR or None) as media:
        for chunk in decrypt_data_from_file(private_key, str(path)):
            media.write(chunk)
        media.flush()
        media.seek(0)
        result = subprocess.run(
            [settings.FFPROBE_PATH, "-v", "error", "-protocol_whitelist", "file,pipe",
             "-show_entries", "format=duration:stream=duration,codec_type", "-of", "json",
             f"/dev/fd/{media.fileno()}"],
            pass_fds=(media.fileno(),), capture_output=True, timeout=60, check=True,
        )
        data = json.loads(result.stdout)
        if not any(s.get("codec_type") == "audio" for s in data.get("streams", [])):
            raise ValueError("Media has no audio stream")
        duration = float(data.get("format", {}).get("duration", "nan"))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Media duration is unavailable")
        return math.ceil(duration)


async def probe_duration(path):
    api_user = await user_get(username="api_user")
    if not api_user:
        raise HTTPException(503, "Media duration service is unavailable")
    settings = get_settings()
    raw_key = await user_get_private_key(api_user["user_id"])
    private_key = deserialize_private_key_from_pem(raw_key, settings.API_PRIVATE_KEY_PASSWORD)
    try:
        async with _probe_slots:
            return await asyncio.to_thread(_probe, path, private_key)
    except FileNotFoundError:
        raise HTTPException(503, "Media file or duration probe is unavailable") from None
    except (ValueError, subprocess.SubprocessError):
        raise HTTPException(422, "Unable to determine media duration; the job has not been queued") from None
