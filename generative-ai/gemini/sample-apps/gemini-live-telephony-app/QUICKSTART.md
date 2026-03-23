# Quick Start Guide: Asterisk + Gemini Live

## 5-Minute Setup (Local Testing)

### Prerequisites

- Docker and Docker Compose
- Python 3.8+
- Google Cloud SDK
- Basic understanding of Asterisk

### Step 1: Set Up Google Cloud

```bash
# Authenticate
gcloud auth application-default login

# Set your project
export PROJECT_ID=your-google-cloud-project
gcloud config set project $PROJECT_ID

# Verify Gemini API access
curl -X POST https://generativelanguage.googleapis.com/v1beta/projects/$PROJECT_ID/locations/us-central1/publishers/google/models/gemini-2.0-flash-exp:generateContent \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -d '{"contents": [{"parts": [{"text": "Hello"}]}]}'
```

### Step 2: Configure .env

```bash
cat > .env << EOF
GOOGLE_CLOUD_PROJECT=$PROJECT_ID
GOOGLE_CLOUD_LOCATION=us-central1
ARI_BASE_URL=http://asterisk:8088
ARI_USERNAME=asterisk
ARI_PASSWORD=asterisk
ARI_APP_NAME=convobridge
RTP_LOCAL_IP=0.0.0.0
RTP_LOCAL_PORT_RANGE_START=10000
RTP_LOCAL_PORT_RANGE_END=20000
SERVICE_LOG_LEVEL=DEBUG
EOF
```

### Step 3: Start Services

Using Docker Compose (fastest):

```bash
docker-compose up -d
```

Or manually start Asterisk + Python:

```bash
# Terminal 1: Start Asterisk
asterisk -fg

# Terminal 2: Start Python service
python -m app.main
```

### Step 4: Test with a Call

#### Option A: SIP Softphone

Use a SIP client (Linphone, X-Lite, etc.):

1. Configure account:
   - Server: `asterisk_host`
   - Username: `testuser`
   - Password: (from Asterisk config)

2. Make a call to the test extension (e.g., `1234`)

3. Watch logs:
   ```bash
   docker-compose logs -f python-service
   ```

#### Option B: SIPp Simulator

```bash
# One-way test call
sipp -sf uac.xml -s 1234 asterisk_host -l 1 -m 1
```

### Step 5: Verify Everything Works

```bash
# Check service health
curl http://localhost:8000/health

# Check active calls
curl http://localhost:8000/metrics

# Check Asterisk
asterisk -rx "core show channels"
```

Expected output:

```json
{
  "status": "healthy",
  "active_calls": 1,
  "ari_connected": true
}
```

## Common Issues & Fixes

### "ARI connection refused"

```bash
# Check Asterisk is running
asterisk -rx "core show version"

# Check ARI module
asterisk -rx "module show like ari"

# Restart ARI
asterisk -rx "ari reload"
```

### "RTP port already in use"

```bash
# Find what's using the port
lsof -i :10000-20000

# Change RTP port range in .env
RTP_LOCAL_PORT_RANGE_START=20000
RTP_LOCAL_PORT_RANGE_END=30000
```

### "Gemini connection timeout"

```bash
# Check Google Cloud credentials
gcloud auth list

# Verify project and region
gcloud config list

# Test API directly
python -c "from google import genai; print(genai.Client().models.list())"
```

## Next Steps

- **Read** `ASTERISK_README.md` for detailed architecture
- **Review** `design_doc.md` for conversion approach
- **Extend** `app/gemini_live.py` for custom system prompts
- **Integrate** `app/db.py` for transcript storage
- **Deploy** to production using `deploy.sh`

## Architecture at a Glance

```
Caller
  ↓ (BSNL DID)
Asterisk (answers, creates bridge)
  ↓ (RTP/UDP)
Python Service (transcoding, session management)
  ↓ (HTTP/gRPC)
Gemini Live API (conversational AI)
  ↓
Response
  ↓
Caller
```

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | Entry point, FastAPI server, event loop |
| `app/asterisk_ari.py` | ARI client + Stasis event handling |
| `app/rtp_io.py` | RTP socket management |
| `app/audio_transcoding.py` | Audio conversion (µ-law ↔ PCM) |
| `app/gemini_live.py` | Gemini session orchestration |
| `app/session_state.py` | Per-call state models |
| `.env.example` | Configuration template |

## Performance Tips

- **RTP Port Pool:** Adjust range based on expected concurrent calls
- **Log Level:** Set to `ERROR` in production to reduce I/O
- **Resampler Quality:** Use `sinc_fastest` for latency, `sinc_best` for quality
- **CPU Affinity:** Pin Python process to specific cores for stable performance

## Support

For issues, consult:
1. Asterisk logs: `asterisk -rx "core show version"`
2. Python logs: `tail -f logs/*.log`
3. Design doc: `design_doc.md`
