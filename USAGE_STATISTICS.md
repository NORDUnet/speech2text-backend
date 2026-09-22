# Anonymous feature statistics

Available under Activity overview, BOFH only (enforced by the API).
Deploy backend and UI together. Apply `alembic upgrade head` using the normal
release procedure; the new usage_counters table is also included in the existing
metadata create_all startup. No worker changes or new environment variables.

Only UTC Monday/week, fixed metric name and integer count are persisted.
There are no individual events, identity columns, raw sizes, languages, models,
filenames, URLs or content. The API accepts only predefined UI metrics, requires
normal authentication and does not retain that identity in the statistics.
Existing operational/authentication logs are separate and may still identify
requests; do not enable request-body logging or telemetry payload capture.
This change does not alter existing page-view analytics.

UI counters flush every 30 seconds per active page, only when there are actions.
Backend counters flush every 30 seconds per process in a single atomic upsert.
Concurrent processes add safely. Queue counts are recorded after committing a
transition to pending, excluding repeated requests while already pending.
A deliberate requeue after completion/failure counts as another queued job.
No upload reread, encryption change or worker request is introduced.

The panel uses complete weeks (1, 4 or 12); no live event timeline. Positive
counts below five are returned as <5. No percentages/totals reveal hidden cells.
Current global group/rule counts are calculated on demand with the same masking.
These aggregates are not unique-user measurements and cannot establish anonymity
against all external knowledge about a small installation.

Counters are best-effort, not billing/audit records: a closed page, crash, failed
batch or ambiguous commit can lose counts; ambiguous batches are not retried.
No history is backfilled. Current-week activity first appears the following Monday.
Upload batch counts measure starts, file-size bands measure completed UI/API uploads
through /transcriber endpoints. Legacy worker/mTLS file transfers are not uploads
through that UI flow. Bulk exports count each file, not the ZIP as one export.
Provisioning counts real matched rules and changed applications, not rule tests.
Group limit blocks count queue requests rejected by the group quota check.
