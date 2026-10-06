"""Existing quota is checked only at the start of a file transfer."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from db.models import JobStatusEnum
from routers import transcriber as route


class UploadQuotaTests(unittest.IsolatedAsyncioTestCase):
    async def test_quota_endpoint_checks_authenticated_user(self):
        for allowed in (True, False):
            with patch.object(route, 'user_get_quota_left', AsyncMock(return_value=allowed)) as quota, \
                 patch.object(route, 'remaining_seconds_for_user', AsyncMock(return_value=14400)) as remaining:
                response = await route.upload_quota({'user_id': 'owner'})
            quota.assert_awaited_once_with('owner')
            remaining.assert_awaited_once_with('owner')
            result = json.loads(response.body)['result']
            self.assertEqual(result['allowed'], allowed)
            self.assertEqual(result['remaining_seconds'], 14400)
            self.assertEqual(bool(result['error']), not allowed)

    async def test_already_exhausted_quota_rejects_without_reading_body(self):
        request = SimpleNamespace(headers={}, stream=AsyncMock())
        with patch.object(route, 'user_get_quota_left', AsyncMock(return_value=False)), \
             patch.object(route, 'job_create', AsyncMock()) as create:
            response = await route.transcribe_file_stream(request, 'meeting.mp4', None, {'user_id': 'owner'})
        self.assertEqual(response.status_code, 403)
        request.stream.assert_not_called()
        create.assert_not_awaited()

    async def test_allowed_upload_checks_quota_once_before_reading_body(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            quota = stack.enter_context(patch.object(route, 'user_get_quota_left', AsyncMock(side_effect=[True, False])))
            consumed = []

            async def stream():
                quota.assert_awaited_once_with('owner')
                for chunk in (b'first', b'second', b'third'):
                    consumed.append(chunk)
                    yield chunk

            async def encrypt(key, chunks, dest, **kwargs):
                with open(dest, 'wb') as output:
                    async for chunk in chunks:
                        output.write(chunk)

            request = SimpleNamespace(headers={}, stream=stream)
            stack.enter_context(patch.object(route, 'api_file_storage_dir', directory))
            stack.enter_context(patch.object(route, 'user_get_public_key', AsyncMock(return_value='key')))
            stack.enter_context(patch.object(route, 'deserialize_public_key_from_pem', return_value='key'))
            stack.enter_context(patch.object(route, 'encrypt_string', return_value='encrypted-name'))
            stack.enter_context(patch.object(route, 'user_get', AsyncMock(return_value={'user_id': 'api'})))
            job = dict(uuid='job', user_id='owner', status='uploaded', job_type='transcription')
            stack.enter_context(patch.object(route, 'job_create', AsyncMock(return_value=job)))
            update = stack.enter_context(patch.object(route, 'job_update', AsyncMock(return_value=job)))
            stack.enter_context(patch.object(route, 'encrypt_async_byte_stream_to_file', encrypt))
            response = await route.transcribe_file_stream(request, 'meeting.mp4', None, {'user_id': 'owner'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual((Path(directory) / 'owner' / 'job').read_bytes(), b'firstsecondthird')
            self.assertEqual(len(consumed), 3)
            quota.assert_awaited_once_with('owner')
            update.assert_awaited_once_with('job', status=JobStatusEnum.UPLOADED,
                                           expected_status=JobStatusEnum.UPLOADING)
