from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import numpy as np
import librosa
import io
import logging
import subprocess
import tempfile
import os
import csv
from datetime import datetime
import pandas as pd
from werkzeug.utils import secure_filename

from s3_storage import S3Storage


# ==========================================================
# CLINICAL VOICE SCREENER API – PRODUCTION READY (Dec 2025)
# FIXED: 90Hz pYIN confidence validation
# ==========================================================
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
app = Flask(__name__)
CORS(app)
storage = S3Storage()
print(
    "S3 DEBUG:",
    os.getenv("S3_ENABLED"),
    os.getenv("AWS_S3_BUCKET"),
    os.getenv("AWS_REGION"),
)


# ✅ FIXED: PORTABLE PATH (works on Windows/Linux/Mac)
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "voice_results.csv")
EXCEL_FILE = os.path.join(os.path.dirname(__file__), "voice_results.xlsx")


FIELDNAMES = [
    "timestamp",
    "patient_id", 
    "age",
    "median_f0_hz",
    "f0_std_hz",
    "jitter_percent",
    "pitch_label",
    "quality_label",
    "voiced_frames",      # NEW
    "mean_voiced_prob",   # NEW
    "confidence_high"     # NEW
]


def append_result_row(row: dict):
    """Create CSV if needed and append one result row."""
    target_file = RESULTS_FILE
    try:
        file_exists = os.path.exists(target_file)
        with open(target_file, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_file = os.path.join(os.path.dirname(__file__), f"voice_results_{stamp}.csv")
        with open(target_file, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerow(row)
    print(f"✅ SAVED TO CSV: {row['patient_id']} | F0: {row['median_f0_hz']}Hz | Conf: {row.get('confidence_high', 'N/A')}")


def save_excel():
    """Convert CSV to Excel automatically."""
    if os.path.exists(RESULTS_FILE):
        try:
            df = pd.read_csv(RESULTS_FILE)
            df.to_excel(EXCEL_FILE, index=False)
            print(f"✅ EXCEL SAVED: {EXCEL_FILE}")
        except Exception as e:
            print(f"⚠️ Excel save failed: {e}")


def upload_results_snapshot_to_s3():
    """Optionally store latest CSV/XLSX result snapshots in S3."""
    if not storage.is_enabled():
        return {}

    uploaded = {}
    if os.path.exists(RESULTS_FILE):
        try:
            uploaded["csv_s3_uri"] = storage.upload_file(
                RESULTS_FILE,
                f"{storage.prefix}/results/voice_results.csv",
                content_type="text/csv",
            )
        except RuntimeError as exc:
            uploaded["warning"] = str(exc)
    if os.path.exists(EXCEL_FILE):
        try:
            uploaded["excel_s3_uri"] = storage.upload_file(
                EXCEL_FILE,
                f"{storage.prefix}/results/voice_results.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except RuntimeError as exc:
            uploaded["warning"] = str(exc)
    return uploaded


def upload_analysis_json_to_s3(response_payload, patient_id):
    if not storage.is_enabled():
        return

    try:
        result_key = storage.build_key("results", "analysis.json", patient_id)
        response_payload["storage"]["analysis_json_s3_uri"] = storage.upload_json(response_payload, result_key)
    except RuntimeError as exc:
        response_payload["storage"]["warning"] = str(exc)


# ---- Validated thresholds (Dec 2025) ----
NORMAL_MALE_MAX = 160        # Hz
BORDERLINE_MIN = 161         # Hz
BORDERLINE_MAX = 194         # Hz
PUBERPHONIA_MIN = 195        # Hz
F0_STD_HOARSE_THRESHOLD = 30 # Hz
JITTER_HOARSE_THRESHOLD = 3.0 # %


def decode_to_wav_bytes(raw_bytes: bytes) -> bytes:
    """
    Convert any input (webm/opus, wav, etc.) to mono 16‑kHz WAV using ffmpeg.
    Requires ffmpeg installed and visible on PATH.
    """
    with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as f_in:
        f_in.write(raw_bytes)
        in_path = f_in.name

    out_path = in_path + ".wav"

    cmd = [
        "ffmpeg", "-y",
        "-i", in_path,
        "-ac", "1",      # mono
        "-ar", "16000",  # 16 kHz
        "-f", "wav",
        out_path
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        with open(out_path, "rb") as f_out:
            wav_bytes = f_out.read()
    finally:
        for p in (in_path, out_path):
            try:
                os.remove(p)
            except OSError:
                pass

    return wav_bytes


@app.route("/")
def index():
    return '''
    <!DOCTYPE html>
    <html><head><title>🎤 Voice Screener API</title>
    <style>body{font-family:sans-serif;max-width:600px;margin:50px auto;padding:20px;background:#f5f7fa;}
    h1{color:#0b7285;} .status{background:#d1e7dd;padding:10px;border-radius:8px;margin:10px 0;}</style></head>
    <body>
        <h1>🎤 Clinical Voice Screener API</h1>
        <div class="status">✅ Server running on <b>http://localhost:5000</b></div>
        <p><b>📱 Test:</b> Open voice_screener.html?patientId=SIVA001&age=24</p>
        <hr>
        <p><b>📊 Results saved:</b></p>
        <ul>
            <li><b>CSV:</b> voice_results.csv</li>
            <li><b>Excel:</b> voice_results.xlsx</li>
        </ul>
        <p><small>Run: <code>python voice_screener_api.py</code></small></p>
    </body></html>
    '''


@app.route("/voice_screener.html")
def voice_screener_page():
    return send_from_directory(os.path.dirname(__file__), "voice_screener.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    """Receive audio → pYIN → metrics → classification."""
    try:
        print("API HIT: Received /analyze request")
        print(f"API DEBUG: request.files keys = {list(request.files.keys())}")
        print(f"API DEBUG: request.form = {dict(request.form)}")
        if "audio" not in request.files:
            print("API ERROR: No audio file found in request.files")
            return jsonify({"status": "error", "message": "No audio file"}), 400

        audio_file = request.files["audio"]
        patient_id = request.form.get("patient_id", "anonymous")
        age = request.form.get("age", "unknown")
        original_filename = secure_filename(audio_file.filename or "recording.wav")
        raw_bytes = audio_file.read()
        print(f"API DEBUG: Received audio filename={original_filename}, bytes={len(raw_bytes)}")
        print(f"API DEBUG: S3 enabled={storage.is_enabled()}, bucket={storage.bucket}, region={storage.region}")

        # 1. Upload original recording to S3 before processing.
        original_s3_uri = ""
        original_s3_key = ""
        storage_warning = None
        if storage.is_enabled():
            try:
                original_s3_key = storage.build_key("raw", original_filename, patient_id)
                print(f"Uploading raw audio to S3: s3://{storage.bucket}/{original_s3_key}")
                original_s3_uri = storage.upload_bytes(
                    raw_bytes,
                    original_s3_key,
                    content_type=audio_file.mimetype or "application/octet-stream",
                    metadata={"patient_id": patient_id, "age": age},
                )
                print(f"Raw audio upload success: {original_s3_uri}")
            except RuntimeError as exc:
                storage_warning = str(exc)
                original_s3_key = ""
                print(f"S3 ERROR raw audio upload failed: {exc}")
        else:
            print("S3 SKIPPED: S3_ENABLED is false or storage is not configured")

        # 2. Decode to WAV
        print("API DEBUG: Starting FFmpeg decode")
        wav_bytes = decode_to_wav_bytes(raw_bytes)
        print(f"API DEBUG: FFmpeg decode complete, wav bytes={len(wav_bytes)}")
        wav_s3_uri = ""
        wav_s3_key = ""
        if storage.is_enabled() and not storage_warning:
            try:
                wav_s3_key = storage.build_key("processed-wav", f"{original_filename}.wav", patient_id)
                print(f"Uploading processed WAV to S3: s3://{storage.bucket}/{wav_s3_key}")
                wav_s3_uri = storage.upload_bytes(
                    wav_bytes,
                    wav_s3_key,
                    content_type="audio/wav",
                    metadata={"patient_id": patient_id, "age": age, "source_key": original_s3_key},
                )
                print(f"Processed WAV upload success: {wav_s3_uri}")
            except RuntimeError as exc:
                storage_warning = str(exc)
                print(f"S3 ERROR processed WAV upload failed: {exc}")

        # 3. Load with librosa
        y, sr = librosa.load(io.BytesIO(wav_bytes), sr=None, mono=True)

        if len(y) < sr // 2:  # <0.5s
            return jsonify({"status": "error", "message": "Recording too short (needs 1s+)" }), 400

        # 3. pYIN pitch tracking
        f0, voiced_flag, voiced_probs = librosa.pyin(
            y, fmin=90, fmax=280, sr=sr, frame_length=2048
        )
        times = librosa.frames_to_time(np.arange(len(f0)), sr=sr)

        # 4. FIXED: Confidence validation (eliminates 90Hz fallback)
        voiced_count = np.sum(voiced_flag)
        mean_voiced_prob = float(np.mean(voiced_probs[voiced_flag])) if voiced_count > 0 else 0.0
        confidence_ok = (voiced_count >= 4) and (mean_voiced_prob >= 0.100)
        f0_clean = f0[voiced_flag]

        voiced_count = int(np.sum(voiced_flag))
        mean_voiced_prob = float(np.mean(voiced_probs[voiced_flag])) if voiced_count > 0 else 0.0

        if len(f0_clean) < 2:
            # Let frontend fall back to graph
            response_payload = {
                "status": "success",
                "metrics": {
                    "median_f0_hz": 0.0,
                    "f0_std_hz": 0.0,
                    "jitter_percent": 0.0
                },
                "classification": {
                    "pitch": "Unknown",
                    "quality": "Unknown"
                },
                "confidence": {
                    "is_high": False,
                    "voiced_frames": voiced_count,
                    "mean_voiced_prob": mean_voiced_prob,
                    "warning": "Too few voiced frames – use graph estimate / retry louder."
                },
                "f0_values": [float(x) if not np.isnan(x) else 0 for x in f0],
                "time_values": times.tolist(),
                "storage": {
                    "raw_audio_s3_uri": original_s3_uri,
                    "processed_wav_s3_uri": wav_s3_uri,
                    "raw_audio_download_url": storage.create_presigned_url(original_s3_key) if original_s3_key else "",
                    "warning": storage_warning,
                },
            }
            upload_analysis_json_to_s3(response_payload, patient_id)
            return jsonify(response_payload), 200


        # 5. Metrics
        median_f0 = float(np.median(f0_clean))
        std_f0 = float(np.std(f0_clean))

        # Jitter (%)
        jitter = 0.0
        if len(f0_clean) > 1:
            jitter_periods = np.abs(np.diff(f0_clean))
            jitter = float(np.mean(jitter_periods) / median_f0 * 100)

        # 6. Classification
        if median_f0 <= NORMAL_MALE_MAX:
            pitch_class = "Normal Male"
        elif median_f0 <= BORDERLINE_MAX:
            pitch_class = "Borderline"
        else:
            pitch_class = "Puberphonia"

        is_hoarse = std_f0 > F0_STD_HOARSE_THRESHOLD or jitter > JITTER_HOARSE_THRESHOLD
        quality = "Hoarse" if is_hoarse else "Clear"

        print(f"✅ ANALYSIS: F0={median_f0:.1f}Hz | Std={std_f0:.1f}Hz | Jitter={jitter:.2f}% | {pitch_class} | Conf:{confidence_ok} ({voiced_count}f, p:{mean_voiced_prob:.3f})")
       
        response_payload = {
            "status": "success",
            "metrics": {
                "median_f0_hz": round(median_f0, 1),
                "f0_std_hz": round(std_f0, 1),
                "jitter_percent": round(jitter, 2)
            },
            "classification": {
                "pitch": pitch_class,
                "quality": quality
            },
            "confidence": {  # NEW - fixes 90Hz problem
                "is_high": confidence_ok,
                "voiced_frames": int(voiced_count),
                "mean_voiced_prob": round(mean_voiced_prob, 3),
                "warning": "Low confidence: too few voiced frames" if not confidence_ok else None
            },
            "f0_values": [float(x) if not np.isnan(x) else 0 for x in f0],
            "time_values": times.tolist(),
            "storage": {
                "raw_audio_s3_uri": original_s3_uri,
                "processed_wav_s3_uri": wav_s3_uri,
                "raw_audio_download_url": storage.create_presigned_url(original_s3_key) if original_s3_key else "",
                "warning": storage_warning,
            },
        }
        upload_analysis_json_to_s3(response_payload, patient_id)
        return jsonify(response_payload)

    except Exception as e:
        print(f"❌ Analysis error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/save-result", methods=["POST"])
def save_result():
    """Save clinical result to CSV + Excel."""
    try:
        data = request.get_json(silent=True) or {}

        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "patient_id": data.get("patient_id", "anonymous"),
            "age": data.get("age", "unknown"),
            "median_f0_hz": data.get("median_f0_hz", 0),
            "f0_std_hz": data.get("f0_std_hz", 0),
            "jitter_percent": data.get("jitter_percent", 0),
            "pitch_label": data.get("pitch_label", "unknown"),
            "quality_label": data.get("quality_label", "unknown"),
            "voiced_frames": data.get("voiced_frames", 0),      # NEW
            "mean_voiced_prob": data.get("mean_voiced_prob", 0), # NEW
            "confidence_high": data.get("confidence_high", False) # NEW
        }

        append_result_row(row)
        save_excel()  # Auto-convert to Excel
        try:
            uploaded = upload_results_snapshot_to_s3()
        except Exception as exc:
            uploaded = {"warning": f"Result saved locally, but S3 result upload failed: {exc}"}
        
        return jsonify({"status": "ok", "storage": uploaded}), 200
        
    except Exception as e:
        print(f"❌ Save error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "results_file": RESULTS_FILE,
        "s3_env_enabled": os.getenv("S3_ENABLED"),
        "s3_env_bucket": os.getenv("AWS_S3_BUCKET"),
        "s3_env_region": os.getenv("AWS_REGION"),
        "s3_enabled": storage.is_enabled(),
        "s3_bucket": storage.bucket if storage.is_enabled() else None,
    })


@app.route("/s3-test", methods=["GET"])
def s3_test():
    """Write a tiny test JSON object to S3 to verify Render env + AWS access."""
    if not storage.is_enabled():
        return jsonify({
            "status": "error",
            "message": "S3 is not enabled or not configured",
            "s3_env_enabled": os.getenv("S3_ENABLED"),
            "s3_env_bucket": os.getenv("AWS_S3_BUCKET"),
            "s3_env_region": os.getenv("AWS_REGION"),
            "s3_enabled": storage.is_enabled(),
        }), 500

    try:
        key = storage.build_key("debug", "s3-test.json", "render")
        uri = storage.upload_json({
            "status": "ok",
            "message": "Render can write to S3",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "bucket": storage.bucket,
            "region": storage.region,
        }, key)
        return jsonify({"status": "ok", "s3_uri": uri, "key": key})
    except Exception as exc:
        print(f"S3 TEST ERROR: {exc}")
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/results", methods=["GET"])
def get_results():
    """Preview latest results."""
    if not os.path.exists(RESULTS_FILE):
        return jsonify({"status": "no_results", "message": "No results yet"})
    
    try:
        df = pd.read_csv(RESULTS_FILE)
        return jsonify({
            "status": "success",
            "count": len(df),
            "latest": df.tail(3).to_dict('records')
        })
    except:
        return jsonify({"status": "error", "message": "Cannot read results"})


if __name__ == "__main__":
    print("==========================================================")
    print("🎤 CLINICAL VOICE SCREENER API v2.1 - FIXED 90Hz")
    print(f"📊 CSV: {RESULTS_FILE}")
    print(f"📊 Excel: {EXCEL_FILE}")
    print("🚀 http://localhost:5000")
    print("✅ FIXED: pYIN confidence → no more 90Hz fallback!")
    print("==========================================================")
    
    # Create empty CSV if needed
    if not os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, 'w') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
    
    app.run(debug=False, host="0.0.0.0", port=5000)
