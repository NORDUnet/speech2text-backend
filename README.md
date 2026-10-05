# scribe-backend

Backend built on FastAPI for the Sunet transcription service (Sunet Scribe).

## Author

This project is developed by [Sunet](https://www.sunet.se). Contributor: Kristofer Hallin.

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.

Copyright (c) 2025-2026 Sunet. Contributor: Kristofer Hallin.

## Contributing

Contributions are welcome! Please feel free to open issues or submit pull requests.

## Features

- **Transcription Jobs**: Create, manage, and track audio/video transcription jobs
- **User Management**: Multi-tenant user system with realms, groups, and role-based access
- **OIDC Authentication**: OpenID Connect integration for secure authentication
- **Email Notifications**: Automated notifications for job status updates
- **File Encryption**: RSA-based encryption for secure file storage
- **External Integrations**: Support for Kaltura and other external services
- **Database Migrations**: Alembic-powered schema migrations

## Requirements

- Python 3.13+
- [uv](https://github.com/astral-sh/uv) (recommended package manager)
- PostgreSQL (production) or SQLite (development)

## Development Environment Setup

### 1. Clone and Install Dependencies

```bash
git clone <repository-url>
cd scribe-backend
uv sync
```

### 2. Configure Environment Variables

Create a `.env` file in the project root with the following settings:

```env
# API configuration
API_DATABASE_URL="sqlite:///jobs.db"
API_DEBUG=True
API_PREFIX="/api/v1"
API_VERSION="0.1.0"
API_TITLE="Sunet Scribe REST backend"
API_DESCRIPTION="A REST API for the Sunet Scribe service."
API_FILE_STORAGE_DIR=<Your file storage directory>
API_SECRET_KEY=<Your secret key>
API_CLIENT_VERIFICATION_ENABLED=True
API_CLIENT_VERIFICATION_HEADER="x-client-legacy"
API_WORKER_CLIENT_DN=<Your worker client DN>
API_KALTURA_CLIENT_DN=<Your Kaltura client DN>
API_PRIVATE_KEY_PASSWORD=<Your private key password>

# SMTP configuration
API_SMTP_HOST=<Your SMTP host>
API_SMTP_PORT=<Your SMTP port>
API_SMTP_USERNAME=<Your SMTP username>
API_SMTP_PASSWORD=<Your SMTP password>
API_SMTP_SENDER=<Your SMTP sender address>
API_SMTP_SSL=<True or False for SSL usage>

# OIDC configuration
OIDC_CLIENT_ID=<Your OIDC client ID>
OIDC_CLIENT_SECRET=<Your OIDC client secret>
OIDC_METADATA_URL=<Your OIDC provider metadata URL>
OIDC_SCOPE=openid,profile,email
OIDC_REFRESH_URI=<Your token refresh endpoint>
OIDC_REDIRECT_URI=<Your OIDC redirect endpoint>
OIDC_FRONTEND_URI=<Your frontend application URI>
```

### 3. Run the Application

```bash
uv run uvicorn app:app --reload
```

The API will be available at `http://localhost:8000`.

## API Documentation

Once the application is running, access the interactive API documentation:

- **Swagger UI**: `http://localhost:8000/api/docs`
- **OpenAPI Spec**: `http://localhost:8000/api/openapi.json`

### API Endpoints

| Tag | Description |
|-----|-------------|
| `/api/v1/transcriber` | Transcription operations |
| `/api/v1/job` | Job management operations |
| `/api/v1/user` | User management operations |
| `/api/v1/external` | External service operations |
| `/api/v1/healthcheck` | Health check operations |
| `/api/v1/admin` | Administrative operations |

### Authentication Endpoints

| Endpoint | Description |
|----------|-------------|
| `/api/login` | Initiate OIDC login flow |
| `/api/auth` | OIDC callback endpoint |
| `/api/logout` | Logout and redirect to frontend |
| `/api/refresh` | Refresh access token |

## Database

### Migrations with Alembic

Run database migrations:

```bash
uv run alembic upgrade head
```

Create a new migration:

```bash
uv run alembic revision --autogenerate -m "Description of changes"
```

### Database Models

- **Job**: Transcription job with status tracking, language settings, and output format
- **JobResult**: Stores transcription results (JSON and SRT formats)
- **User**: User accounts with encryption keys and notification preferences
- **Group**: User groups with quotas and model access permissions
- **Customer**: Customer organizations with pricing plans
- **Model**: Available transcription model types

## Docker

Build and run with Docker:

```bash
docker build -t scribe-backend .
docker run -p 8000:8000 --env-file .env scribe-backend
```

## Testing

Run tests with pytest:

```bash
uv run pytest
```

## Project Structure

```
scribe-backend/
├── app.py              # FastAPI application entry point
├── alembic/            # Database migrations
├── auth/               # Authentication (OIDC, client verification)
├── db/                 # Database models and operations
├── routers/            # API route handlers
├── utils/              # Utilities (crypto, logging, settings)
└── tests/              # Test files
```

## Shared monthly realm quotas

BOFH users manage shared pools at **Admin → Shared quotas**. Each realm can belong
to at most one pool. Limits are configured in hours in the UI; the API and database
use integer seconds. `quota_seconds: null` means unlimited; `0` prevents new
reservations. An unassigned realm has no shared-pool restriction. Existing group
quotas still apply. REACH/external jobs are excluded from both reservation and
completed usage, based on their server-created external job ID.

`GET /api/v1/admin/quotas` returns the current UTC month, limit, completed,
reserved, and remaining seconds. Realm admins can read the aggregate totals of
pools associated with their managed realms (including retained current-month
charges after a realm move). BOFH can read all pools and use `POST /admin/quotas`
and `PUT /admin/quotas/{id}` to set a name, `quota_seconds`, and a `realms` list.
The API rejects assigning a realm that already belongs to another pool. Remove it
from the old pool first. Setting an empty realm list retires a pool for new jobs
without deleting its accounting history.

Each submitted non-external job is assigned once to its realm's current pool and
the UTC month in which it is first successfully queued. Membership changes apply
to subsequent submissions, including when a realm is first assigned to a pool;
existing jobs and usage are not transferred. Jobs already queued/completed before
this feature is deployed are not retroactively charged. Failed admission does not
pin a job to a month or pool. A retry after an admitted job fails retains its
original assignment. Editing a limit changes the current month's allowance and
the default for future months; previous months retain their allowance. Reducing a
limit does not cancel admitted work, even if completed plus reserved usage already
exceeds the new limit.

For a realm without a pool, admission skips media probing and reservation
accounting. A small `quota_exemptions` record remembers that assignment, so a later
pool assignment cannot charge the job or its retries retroactively. Membership is
rechecked under lock at admission. If a pool was assigned after the initial check,
the request probes outside the transaction and retries admission with the duration.
Assigned pools still track duration even when their limit is unlimited.

For jobs assigned to a pool, admission probes the encrypted upload before queueing.
It rounds the full media
duration up to whole seconds, then reserves capacity and changes job status in
one short database transaction. Completion moves that reservation to completed
usage exactly once; worker-reported speech duration continues to feed existing
user statistics. Failed jobs and cancelled queued jobs release reservations.
Completed usage survives job deletion and retention cleanup. Running jobs cannot
be deleted, and cleanup skips queued/running jobs. Reservations do not expire on
a timer: a disconnected worker might still be running. For an abandoned job,
verify that its worker has stopped before reporting failure through the existing
worker status endpoint; this releases its reservation.

Deployment:

1. Install `ffprobe` on backend hosts (included through `ffmpeg` in the Dockerfile).
   `FFPROBE_PATH` defaults to `ffprobe` and can specify an absolute executable path.
2. Provision `UPLOAD_TMP_DIR` with sufficient scratch capacity. Duration probing
   decrypts into a private, unlinked temporary file to support seekable media
   formats. The descriptor is closed on success or failure. Probes run outside
   database transactions, with at most two probes per API process; the ffprobe
   subprocess has a 60-second timeout.
3. Apply `alembic upgrade head` before rolling out the backend and UI. Migration
   `f2a4c6e8b0d1` adds the quota tables; `a4d6e8f0b2c3` adds the no-quota assignment
   markers. Both leave existing jobs untouched. No quota is
   enabled automatically. Create pools and assign realms in the BOFH UI.

Counters are indexed by `(quota_id, period_start)`. Admission, completion, and
limit edits serialize briefly on the pool row; different pools do not share that
lock. Durable `quota_charges` records preserve attribution after job cleanup and
allow counters to be audited. No job-table aggregation is needed during admission
or ordinary quota dashboard reads.

Run the isolated tests from the workspace root with `QUOTA_TEST_DATABASE_URL`
set to an empty, disposable PostgreSQL database using a `postgresql+asyncpg://` URL:

```sh
OIDC_SCOPE=openid API_FILE_STORAGE_DIR=/tmp transcribe-backend/.venv/bin/python -m pytest \
  transcribe-backend/tests/test_quotas.py transcribe-backend/tests/test_media_duration.py \
  transcribe-backend/tests/test_quota_migration.py
```

The quota and migration suites require PostgreSQL and skip when
`QUOTA_TEST_DATABASE_URL` is unset. They create and drop their model tables;
never point them at an application database.
