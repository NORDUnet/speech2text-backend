"""Automatic upload submission: no external database or authentication service."""
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from sqlalchemy.dialects import postgresql
from fastapi import HTTPException
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
        with patch.object(route, 'job_get', AsyncMock(return_value=dict(status='uploaded', external_id='ui-upload:' + identity.hex))), patch.object(route, 'job_update', AsyncMock(return_value=result)) as update, patch.object(route, 'user_get_quota_left', AsyncMock(return_value=100)), patch.object(route, 'user_get_private_key', AsyncMock(return_value=None)), patch.object(route, 'needs_duration', AsyncMock(return_value=False)):
            response = await route.update_transcription_status(None, item, 'job', identity, {'user_id': 'owner'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(update.call_args.kwargs['expected_status'], JobStatusEnum.UPLOADED)
        self.assertEqual(update.call_args.kwargs['speakers'], 2)

    async def test_quota_rejection_persists_reason_even_after_duration_retry(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                identity = uuid4()
                detail = {'code': 'quota_exceeded', 'message': 'Shared monthly quota exceeded.'}
                rejection = HTTPException(403, detail)
                effects = [route.MediaDurationRequired(), rejection] if retry else [rejection]
                item = SimpleNamespace(language='English', speakers=0, output_format='TXT')
                with patch.object(route, 'job_get', AsyncMock(return_value=dict(status='uploaded', external_id='ui-upload:' + identity.hex))), \
                     patch.object(route, 'user_get_quota_left', AsyncMock(return_value=100)), \
                     patch.object(route, 'needs_duration', AsyncMock(return_value=False)), \
                     patch.object(route, 'probe_duration', AsyncMock(return_value=60)), \
                     patch.object(route, 'job_update', AsyncMock(side_effect=effects)), \
                     patch.object(route, 'fail_unqueued_upload', AsyncMock()) as fail:
                    with self.assertRaises(HTTPException) as error:
                        await route.update_transcription_status(None, item, 'job', identity, {'user_id': 'owner'})
                self.assertEqual(error.exception.detail, detail)
                fail.assert_awaited_once_with('owner', identity.hex, detail['message'])

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


    async def test_failed_job_cannot_be_requeued(self):
        with patch.object(route, 'job_get', AsyncMock(return_value=dict(status='failed'))), patch.object(route,'job_update',AsyncMock()) as update:
            response = await route.update_transcription_status(None,None,'job',None,{'user_id':'owner'})
        self.assertEqual(response.status_code,409)
        update.assert_not_awaited()

    async def test_finalize_failure_does_not_overwrite_accepted_queue(self):
        for status in ('pending','in_progress','completed','uploading','uploaded'):
            row=SimpleNamespace(uuid='job',status=status,error='')
            row.as_dict=lambda:dict(uuid=row.uuid,status=row.status,error=row.error)
            session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(first=lambda:row))))
            @asynccontextmanager
            async def isolated_session():
                yield session
            with patch.object(jobs,'get_async_session',isolated_session), patch('pathlib.Path.unlink') as unlink:
                result=await jobs.fail_unqueued_upload('owner','upload-id')
            self.assertEqual(result['status'],'failed' if status in ('uploaded','uploading') else status)
            self.assertEqual(unlink.call_count,int(status=='uploaded'))
            sql=str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
            self.assertIn('FOR UPDATE',sql)
            self.assertIn('user_id',sql)
            self.assertIn('external_id',sql)


    async def test_late_upload_completion_cannot_revive_failed_job(self):
        row=SimpleNamespace(status=JobStatusEnum.FAILED)
        row.as_dict=lambda:dict(status=row.status)
        session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(first=lambda:row))))
        @asynccontextmanager
        async def isolated_session():
            yield session
        with patch.object(jobs,'get_async_session',isolated_session):
            result=await jobs.job_update('job',status=JobStatusEnum.UPLOADED,expected_status=JobStatusEnum.UPLOADING)
        self.assertEqual(result['status'],JobStatusEnum.FAILED)
