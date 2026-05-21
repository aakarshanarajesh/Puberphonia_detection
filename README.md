# Puberphonia Voice Screener

A Flask-based clinical voice screening tool for recording patient audio, extracting pitch features, classifying likely puberphonia indicators, and saving analysis results. The application includes a browser frontend, a Python API, FFmpeg audio conversion, librosa pYIN pitch tracking, local CSV/XLSX result export, and optional AWS S3 storage.

## Features

- Browser-based voice recording and screening workflow.
- Flask API endpoint for audio analysis.
- FFmpeg conversion to mono 16 kHz WAV before analysis.
- Pitch extraction using librosa pYIN.
- Median F0, F0 standard deviation, jitter, confidence, and voiced-frame reporting.
- Pitch classification as Normal Male, Borderline, or Puberphonia.
- Voice quality label as Clear or Hoarse.
- Local result storage in `voice_results.csv` and `voice_results.xlsx`.
- Optional AWS S3 uploads for raw audio, processed WAV files, analysis JSON, and result snapshots.

## Project Structure

```text
.
|-- voice_screener_api.py      # Flask API and voice analysis routes
|-- voice_screener.html        # Browser voice screener frontend
|-- s3_storage.py              # AWS S3 storage helper
|-- requirements.txt           # Python dependencies
|-- EC2_DEPLOYMENT.md          # AWS EC2 and S3 deployment guide
|-- run_s3_backend.ps1         # Windows helper script for S3-enabled backend startup
|-- voice_results.csv          # Local saved results
|-- voice_results.xlsx         # Local Excel export
```

## Requirements

- Python 3.11 or compatible Python 3 version.
- FFmpeg installed and available on `PATH`.
- A modern browser with microphone access.
- AWS account and private S3 bucket if S3 storage is enabled.

## Local Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Confirm FFmpeg is available:

```bash
ffmpeg -version
```

Run the backend:

```bash
python voice_screener_api.py
```

Open the screener:

```text
http://localhost:5000/voice_screener.html?patientId=SIVA001&age=24
```

Check the API health endpoint:

```text
http://localhost:5000/health
```

## API Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/` | GET | Basic API status page |
| `/voice_screener.html` | GET | Serves the voice screener frontend |
| `/analyze` | POST | Accepts an audio file and returns pitch analysis |
| `/save-result` | POST | Saves selected clinical result data to CSV and Excel |
| `/results` | GET | Returns a preview of saved local results |
| `/health` | GET | Shows backend and S3 configuration status |
| `/s3-test` | GET | Writes a small test JSON object to S3 when S3 is enabled |

## AWS S3 Storage

S3 storage is optional and controlled by environment variables. When enabled, the backend stores:

- Original browser recording under `raw/`.
- Processed mono WAV output under `processed-wav/`.
- Analysis JSON under `results/`.
- Latest CSV and XLSX snapshots under `results/`.

The S3 helper uses server-side encryption with `AES256` for uploaded objects. Buckets should remain private, with public access blocked.

### Required Environment Variables

```bash
export S3_ENABLED=true
export AWS_REGION=eu-north-1
export AWS_S3_BUCKET=puberphonia-audio-storage
export AWS_S3_PREFIX=puberphonia
```

For local development with access keys:

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
```

On Windows PowerShell:

```powershell
$env:S3_ENABLED = "true"
$env:AWS_REGION = "eu-north-1"
$env:AWS_S3_BUCKET = "puberphonia-audio-storage"
$env:AWS_S3_PREFIX = "puberphonia"
```

Do not hardcode AWS credentials in source files. For EC2 deployments, prefer an IAM role attached to the instance.

### Minimum S3 IAM Policy

Replace the bucket name with your own bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::puberphonia-audio-storage",
        "arn:aws:s3:::puberphonia-audio-storage/*"
      ]
    }
  ]
}
```

### Testing S3

Start the backend with S3 enabled, then open:

```text
http://localhost:5000/s3-test
```

Successful output includes an S3 URI and object key. If this fails, check AWS credentials, bucket name, region, IAM permissions, and whether `S3_ENABLED` is set to `true`.

## Windows S3 Startup Helper

The included PowerShell helper sets the S3 environment variables and starts the API:

```powershell
.\run_s3_backend.ps1
```

Update the bucket name and region in that script before using it for a different AWS account or region.

## EC2 Deployment

See [EC2_DEPLOYMENT.md](EC2_DEPLOYMENT.md) for an AWS EC2 deployment walkthrough, including:

- Creating a private S3 bucket.
- Attaching an IAM role.
- Installing Python, FFmpeg, Git, and project dependencies.
- Running Flask directly for testing.
- Running the API with Gunicorn.
- Optional systemd service setup.

## Analysis Workflow

1. The browser records patient audio.
2. The frontend sends the recording to `/analyze`.
3. If S3 is enabled, the raw recording is uploaded to S3.
4. FFmpeg converts the recording to mono 16 kHz WAV.
5. If S3 is enabled, the processed WAV is uploaded to S3.
6. librosa pYIN extracts F0 values and voiced-frame confidence.
7. The API calculates median F0, F0 standard deviation, jitter, and classification labels.
8. The API returns metrics, classification, graph data, and optional S3 URIs.
9. `/save-result` writes the final result to CSV and Excel, then optionally uploads snapshots to S3.

## Notes

- `voice_results.csv` and `voice_results.xlsx` are local result files and may contain patient-related data.
- Keep S3 buckets private and restrict IAM permissions to the minimum required actions.
- Make sure FFmpeg is installed on every machine or server running the backend.
- The classifier thresholds are implemented in `voice_screener_api.py`.
