# 6WS Trip Encoder

AI-assisted PDF Trip Ticket and Reimbursement Encoder.

## Current processing modes

### Local fallback
When `REDIS_URL` or `S3_BUCKET` is missing, the web app runs analysis as an in-process background task and stores files on its local filesystem. This mode is for development only: a service restart can interrupt a job, and ephemeral hosting filesystems can lose uploaded PDFs and results.

### Durable Render worker mode
For restart-safe processing across the web and worker services, configure both:
- `REDIS_URL`: Redis-compatible broker URL (Render Key Value or another Redis-compatible service).
- `S3_BUCKET`: bucket name for S3-compatible object storage shared by the web service and worker.
- `S3_ENDPOINT_URL`: optional endpoint for S3-compatible providers.
- `AWS_DEFAULT_REGION`: region if required by the storage provider.
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`: credentials when the provider does not supply workload credentials.
- `GEMINI_API_KEY`: Gemini API key.

The web service uploads source PDFs and manifests to object storage, then enqueues a Celery job. The worker downloads the PDFs to temporary storage, analyzes them, and writes results/status back to the same bucket. Redis stores queue state; the object bucket stores the actual files and results.

Worker start command:
```bash
celery -A worker.celery_app worker --loglevel=INFO --concurrency=1
```

The worker must use the same GitHub branch, Docker image, `GEMINI_API_KEY`, `REDIS_URL`, and S3 storage configuration as the web service. The Docker image installs Tesseract OCR, which is required by both processes.

**Do not enable the durable worker until the Redis broker and object-storage bucket are provisioned and their costs/access permissions are understood.** The app does not create paid infrastructure automatically. The worker and web service must both be configured to use the same bucket. Keep the bucket private.

## Limits
- Up to 200 PDFs per batch by default.
- 50 MB per PDF by default.
- 1 GB total per batch by default.
- Values are configurable using `MAX_FILES_PER_BATCH`, `MAX_FILE_SIZE_MB`, and `MAX_BATCH_SIZE_MB`.

## Safety
- Driver identity matching remains conservative; unidentified/conflicting files must be reviewed.
- AI output is a draft, not approved accounting data.
- Google Sheets export remains disabled until persistent human approval and duplicate-safe write handling are implemented.
- Never commit API keys, service-account credentials, or private driver documents to GitHub.

## Local installation
1. Install Python 3.11 and Tesseract OCR.
2. Install dependencies: `pip install -r requirements.txt`.
3. Configure `GEMINI_API_KEY`.
4. Run `uvicorn app:app --reload`.
5. Open `http://127.0.0.1:8000`.

## Validation
GitHub Actions compiles Python modules and runs processor tests on pushes and pull requests.
