"""Automatic upload submission: no external database or authentication service."""
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from sqlalchemy.dialects import postgresql
from db import job as jobs
from db.models import JobStatusEnum
from routers import transcriber as route

class SubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_submission_does_not_requeue_running_job(self):
        identity = uuid4()
        with patch.object(route, 'job_get', AsyncMock(return_value=dict(status='in_progress', external_id='ui-upload:' + identity.hex))), patch.object(route, 'job_update', AsyncMock()) as update, patch.object(route, 'user_get_quota_left', AsyncMock()) as quota:
            response = await route.update_transcription_status(None, None, 'job', identity, {'user_id': 'owner'})
        self.assertEqual(json.loads(response.body)['result']['status'], 'in_progress')
        update.assert_not_awaited()
        quota.assert_not_awaited()

    async def test_wrong_upload_identity_rejected(self):
        with patch.object(route, 'job_get', AsyncMock(return_value=dict(status='uploaded', external_id='different'))), patch.object(route, 'job_update', AsyncMock()) as update:
            response = await route.update_transcription_status(None, None, 'job', uuid4(), {'user_id': 'owner'})
        self.assertEqual(response.status_code, 403)
        update.assert_not_awaited()

    async def test_uploaded_file_queues_with_atomic_precondition(self):
        identity = uuid4()
        result = dict(uuid='job', status='pending', filename='encrypted', job_type='transcription', language='English', model_type='model', output_format='SRT')
        item = SimpleNamespace(language='English', speakers=2, output_format='SRT', encryption_password='')
        with patch.object(route, 'job_get', AsyncMock(return_value=dict(status='uploaded', external_id='ui-upload:' + identity.hex))), patch.object(route, 'job_update', AsyncMock(return_value=result)) as update, patch.object(route, 'user_get_quota_left', AsyncMock(return_value=100)), patch.object(route, 'user_get_private_key', AsyncMock(return_value=None)):
            response = await route.update_transcription_status(None, item, 'job', identity, {'user_id': 'owner'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(update.call_args.kwargs['expected_status'], JobStatusEnum.UPLOADED)
        self.assertEqual(update.call_args.kwargs['speakers'], 2)

    async def test_lock_protects_precondition_when_status_changes_after_route_read(self):
        # Simulates the state seen under the lock after another request won.
        row = SimpleNamespace(status=JobStatusEnum.IN_PROGRESS, language='original')
        row.as_dict = lambda: dict(status=row.status, language=row.language)
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: row))))
        @asynccontextmanager
        async def isolated_session():
            yield session
        with patch.object(jobs, 'get_async_session', isolated_session):
            result = await jobs.job_update('job', user_id='owner', status='pending', language='changed', expected_status=JobStatusEnum.UPLOADED)
        self.assertEqual(result['status'], JobStatusEnum.IN_PROGRESS)
        self.assertEqual(row.language, 'original')
        sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        self.assertIn('FOR UPDATE', sql)
