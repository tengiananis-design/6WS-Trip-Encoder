
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
