# Puberphonia Flask + S3 Deployment on EC2

This setup keeps the project simple: Flask runs on an Ubuntu EC2 instance, FFmpeg handles audio preprocessing, librosa/pYIN performs pitch extraction, and S3 stores uploaded audio plus optional result files.

## 1. AWS S3 Setup

1. Create a private S3 bucket, for example:

   ```bash
   puberphonia-audio-storage
   ```

2. Keep **Block all public access** enabled.

3. Preferred for EC2: attach an IAM role to the EC2 instance with this minimum policy:

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

For local development only, use `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` environment variables. Do not hardcode keys in Python.

## 2. Launch EC2

1. Launch an Ubuntu EC2 instance.
2. Security group inbound rules:
   - SSH: port `22`, your IP only
   - Flask test access: port `5000`, your IP or `0.0.0.0/0` for demo
3. SSH into the instance:

   ```bash
   ssh -i your-key.pem ubuntu@EC2_PUBLIC_IP
   ```

## 3. Install System Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git
ffmpeg -version
```

## 4. Copy or Clone Project

If using Git:

```bash
git clone YOUR_REPO_URL puberphonia
cd puberphonia
```

Or copy files with `scp`, then:

```bash
cd puberphonia
```

## 5. Install Python Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 6. Configure Environment Variables

On EC2 with an IAM role:

```bash
export S3_ENABLED=true
export AWS_REGION=eu-north-1
export AWS_S3_BUCKET=puberphonia-audio-storage
export AWS_S3_PREFIX=puberphonia
```

Local development with access keys:

```bash
export S3_ENABLED=true
export AWS_REGION=eu-north-1
export AWS_S3_BUCKET=puberphonia-audio-storage
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
```

For a persistent EC2 setup, put the exports in `~/.bashrc` or a systemd service file.

## 7. Run Flask on EC2

Simple test run:

```bash
source .venv/bin/activate
python voice_screener_api.py
```

Open:

```text
http://EC2_PUBLIC_IP:5000/voice_screener.html?patientId=SIVA001&age=24
```

Health check:

```text
http://EC2_PUBLIC_IP:5000/health
```

## 8. Production-ish Run With Gunicorn

```bash
source .venv/bin/activate
gunicorn -w 2 -b 0.0.0.0:5000 voice_screener_api:app
```

Optional systemd service:

```ini
[Unit]
Description=Puberphonia Voice Screener
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/puberphonia
Environment="S3_ENABLED=true"
Environment="AWS_REGION=eu-north-1"
Environment="AWS_S3_BUCKET=puberphonia-audio-storage"
Environment="AWS_S3_PREFIX=puberphonia"
ExecStart=/home/ubuntu/puberphonia/.venv/bin/gunicorn -w 2 -b 0.0.0.0:5000 voice_screener_api:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Save it as `/etc/systemd/system/puberphonia.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable puberphonia
sudo systemctl start puberphonia
sudo systemctl status puberphonia
```

## Interview-Ready Workflow

1. Browser records audio.
2. Flask receives `/analyze`.
3. Original recording is uploaded to private S3 under `raw/`.
4. FFmpeg converts the audio to mono 16 kHz WAV.
5. Processed WAV is uploaded to S3 under `processed-wav/`.
6. librosa pYIN extracts F0.
7. Metrics and classification are returned to the frontend.
8. Analysis JSON is uploaded to S3 under `results/`.
9. `/save-result` updates local CSV/XLSX and uploads snapshots to S3.
