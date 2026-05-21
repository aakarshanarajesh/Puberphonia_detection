$ErrorActionPreference = "Stop"

$env:S3_ENABLED = "true"
$env:AWS_REGION = "eu-north-1"
$env:AWS_S3_BUCKET = "puberphonia-audio-storage"
$env:AWS_S3_PREFIX = "puberphonia"
$env:Path = "C:\Program Files\Amazon\AWSCLIV2;$env:Path"

Write-Host "Starting Puberphonia backend with S3 enabled..."
Write-Host "Bucket: $env:AWS_S3_BUCKET"
Write-Host "Region: $env:AWS_REGION"
Write-Host ""
Write-Host "If upload fails with credentials error, run: aws configure"
Write-Host ""

.\.venv\Scripts\python.exe voice_screener_api.py
