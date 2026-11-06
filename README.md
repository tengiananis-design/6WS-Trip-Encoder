
# 6WS Trip Encoder

AI-assisted PDF Trip Ticket and Reimbursement Encoder.

## Features

- Upload multiple PDF documents.
- Extract text using PDF processing and Tesseract OCR.
- Analyze trip tickets using an AI model.
- Identify and group documents by driver.
- Process trip ticket information.
- Separate toll and parking reimbursements.
- Review extracted information before approval.
- Export approved results to Google Sheets.

## Technology

- Python 3.11
- FastAPI
- Tesseract OCR
- PyMuPDF
- Google Gemini API
- Google Sheets API
- Docker
- Render

## Project Structure

```text
6ws-trip-encoder/
├── app.py
├── requirements.txt
├── Dockerfile
├── render.yaml
├── .env.example
├── .gitignore
├── README.md
├── services/
├── templates/
├── static/
├── data/
└── credentials/
```

## Local Installation

1. Install Python 3.11.
2. Install Tesseract OCR.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Copy `.env.example` to `.env`.
5. Add your credentials.
6. Start the application:

```bash
uvicorn app:app --reload
```

7. Open:

```text
http://127.0.0.1:8000
```

## Render Deployment

1. Upload this project to a private GitHub repository.
2. Create a new Web Service in Render.
3. Connect the GitHub repository.
4. Select Docker as the runtime.
5. Add the required environment variables.
6. Deploy.

## Required Environment Variables

- `GEMINI_API_KEY`
- `GOOGLE_SHEETS_ID`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

Never commit API keys, service-account credentials, or private driver documents to GitHub.

## Important

This starter project requires configuration and testing before production use.

Always review extracted trip and reimbursement data before approving any Google Sheets export.


## Batch processing and job monitoring

The analysis endpoint now queues work through FastAPI background tasks:

- `POST /api/analyze/{batch_id}` starts analysis and returns a job record.
- `GET /api/jobs/{batch_id}` reports queued, processing, completed, or failed status.
- The dashboard polls the job endpoint and displays per-PDF progress.
- Each PDF is first analyzed separately to establish driver identity. Documents with the same confirmed driver are then analyzed together so trip chronology and reimbursement evidence can be reconciled across that driver's PDFs. Different drivers are never intentionally combined. Unidentified, conflicting, or low-confidence identities remain in review.

### Important operational limitation

This first background-job implementation is process-local, not a durable distributed queue. A restart, redeploy, or instance sleep can interrupt an active batch. Job status and uploaded PDFs are stored under `DATA_DIR`, which defaults to the application's local `data` directory. Local container storage is not durable across Render redeploys or instance replacement.

Before using this with real 100+ PDF batches, configure durable storage and move processing to a dedicated worker/queue with persistent job state. If using a mounted persistent disk, set `DATA_DIR` to its mount path. Do not assume the current free web-service filesystem is persistent.

### Batch sizing

The upload endpoint streams each file to disk, allows up to 150 PDFs per batch by default, and enforces a configurable 100 MB per-PDF limit (`MAX_UPLOAD_FILES`, `MAX_UPLOAD_SIZE_MB`). It does not yet provide resumable/chunked uploads, so very large combined upload requests should be tested against the hosting provider's request limits before relying on them.

### Verification status

The GitHub Actions unit-test workflow is configured for the development branch. A successful workflow run and end-to-end test with representative trip tickets, toll receipts, and parking receipts are still required before production use.


## Distributed worker architecture (development branch)

The branch now includes an optional Celery + Redis processing path and S3-compatible shared storage:

- `services/celery_app.py` configures the Redis broker/backend.
- `services/tasks.py` downloads a batch into isolated temporary worker storage, runs the existing PDF/AI pipeline, and saves the result to shared object storage.
- `worker.py` is the independent worker entry point.
- `services/storage.py` provides S3-compatible shared file and JSON storage, with local filesystem fallback for development.
- The web API queues a Celery task only when both shared object storage and `REDIS_URL` are configured. Otherwise it explicitly uses the single-process local fallback.

### Required environment configuration for distributed mode

Configure these on both the web service and worker:

- `REDIS_URL` — private connection string for a Redis-compatible queue.
- `S3_BUCKET` — bucket name.
- `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` — restricted bucket credentials.
- `S3_ENDPOINT_URL` — provider endpoint when using an S3-compatible provider other than AWS.
- `S3_REGION` — bucket region (defaults to `auto`).
- Optional `S3_PREFIX` — defaults to `6ws`.

Use a private Redis instance with a no-eviction policy for queue data. The worker requires access to the same object bucket as the web service. Do not put credentials in source control.

### Render service setup

The existing `render.yaml` intentionally remains a web-only free-plan configuration. It does not provision a Redis service or a worker, because those resources may incur charges and require an explicit budget decision.

To enable distributed processing, create a separate Render Background Worker using this repository and branch, with start command:

```bash
python worker.py
```

Install the same environment variables on the web service and worker. Connect a private Redis-compatible queue and configure shared S3-compatible object storage first. Do not enable the distributed path until both services can access the same bucket and queue.

Celery handles normal worker SIGTERM shutdown/draining; tasks use late acknowledgement, one-job prefetch, and bounded task time limits. The task can be retried after a worker loss, so external export actions remain separate from the analysis task.

### Important limitations to resolve before live use

- The upload API currently stages files on the web instance before copying them to shared storage. Large aggregate uploads should still be tested against request-size and timeout limits.
- The S3-compatible storage provider and Redis instance are not provisioned by this branch.
- The current job metadata file is a status snapshot; Celery remains the authoritative source for task state.
- Google Sheets export is not part of the worker task. It remains an explicit, separate approval action.
- The distributed queue has not yet been exercised end-to-end with real PDFs or a live Render worker.
