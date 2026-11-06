
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
- Each PDF is extracted and analyzed separately before grouping by the driver's extracted name. Unidentified or conflicting driver identities remain in review.

### Important operational limitation

This first background-job implementation is process-local, not a durable distributed queue. A restart, redeploy, or instance sleep can interrupt an active batch. Job status and uploaded PDFs are stored under `DATA_DIR`, which defaults to the application's local `data` directory. Local container storage is not durable across Render redeploys or instance replacement.

Before using this with real 100+ PDF batches, configure durable storage and move processing to a dedicated worker/queue with persistent job state. If using a mounted persistent disk, set `DATA_DIR` to its mount path. Do not assume the current free web-service filesystem is persistent.

### Batch sizing

The upload endpoint streams each file to disk and currently enforces a 100 MB per-PDF limit. It does not yet provide resumable/chunked uploads, so very large combined upload requests should be tested against the hosting provider's request limits before relying on them.

### Verification status

The GitHub Actions unit-test workflow is configured for the development branch. A successful workflow run and end-to-end test with representative trip tickets, toll receipts, and parking receipts are still required before production use.
