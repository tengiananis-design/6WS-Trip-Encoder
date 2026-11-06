
# 6WS Trip Encoder

AI-assisted PDF Trip Ticket and Reimbursement Encoder.

## Features

- Upload multiple PDF documents.
- Extract text using PDF processing and Tesseract OCR.
- Analyze trip tickets using an AI model.
- Identify and group documents by driver.
- Process trip ticket information.
- Separate toll, parking, and passway reimbursements.
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


## Security and source evidence

- Set `APP_USERNAME` and `APP_PASSWORD` in the Render environment before exposing the dashboard. All routes except `/health` require HTTP Basic authentication. If credentials are absent, the app returns HTTP 503 rather than serving private driver documents publicly.
- Each extracted record carries its source PDF filename and page numbers. The review screen renders those exact pages through an authenticated preview endpoint. Previews are returned with private/no-store caching.
- Do not use a public bucket. Configure a private S3-compatible bucket with least-privilege access.

## Durable background processing

The durable worker mode requires all of the following on both web and worker services:
- `REDIS_URL`
- `S3_BUCKET`
- S3-compatible credentials and optional `S3_ENDPOINT_URL` / `AWS_REGION`
- `GEMINI_API_KEY`

Worker start command:

```bash
celery -A worker.celery_app worker --loglevel=INFO --concurrency=1
```

The current Render Blueprint intentionally does not provision Redis, object storage, or a paid background worker. Those services must be chosen and cost-reviewed before enabling durable mode. Without the queue and bucket, the web app uses local background processing, which can be interrupted by a restart and is not suitable for production-scale batches.


## Pre-test checklist

Run the following from the repository root with Python 3.11 and the requirements installed:

```bash
python -m compileall -q app.py worker.py services
node --check static/app.js
python -m unittest discover -s tests -v
```

For the first end-to-end test, configure `APP_USERNAME`, `APP_PASSWORD`, and `GEMINI_API_KEY`. Upload a small representative set first: one trip ticket, one toll PDF, and one parking PDF, including at least one driver whose name is not readable so manual assignment can be exercised. Verify every extracted row against the displayed source page. Do not enable production export; it remains disabled.

For 100+ PDFs, do not use the local background fallback. Configure and verify the private object bucket and Redis queue on both web and worker services first. The current pipeline still processes PDFs sequentially and makes one Gemini request per PDF, so the large-batch load/quota test remains a separate gate.
