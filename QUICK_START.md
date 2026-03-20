# Quick Start Guide - Updated Firestore Credentials

## ✅ Setup Complete

Your TEAM-GRADE ingestion system is now ready to use with Windows Firestore authentication.

---

## 🚀 Get Started in 30 Seconds

### 1. Verify Everything is Ready

```powershell
cd C:\Users\ricky\OneDrive\Desktop\Team-Grade\TEAM-GRADE

# Check credentials file exists
Test-Path "credentials\service-account.json"  # Should return: True
```

### 2. Activate Virtual Environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 3. Test Firestore Connection

```powershell
python -m ingest.ingest_worker --status
```

You should see:
```
Initializing Firestore client for project: the-bridge-athletics1
Credentials path: C:\Users\ricky\...\credentials\service-account.json
✓ Found credentials file
✓ Loaded service account from JSON
✓ Created service account credentials
✓ Firestore initialized
```

### 4. Ingest a Video

```powershell
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=VIDEO_ID"
```

**Done!** Your video is being ingested. ✓

---

## 📋 What Was Done

| Item | Status |
|------|--------|
| Service account JSON placed | ✅ |
| firestore_client.py updated | ✅ |
| ingest_worker.py updated | ✅ |
| Credentials path configured | ✅ |
| Syntax verified | ✅ |
| Imports working | ✅ |
| Ready to use | ✅ |

---

## 📁 Where Everything Is

```
C:\Users\ricky\OneDrive\Desktop\Team-Grade\TEAM-GRADE\
├── credentials/
│   └── service-account.json          ← Your Google Cloud key
├── team-grade-processing/
│   └── ingest/
│       ├── firestore_client.py       ← UPDATED (credentials loading)
│       ├── ingest_worker.py          ← UPDATED (path discovery)
│       ├── youtube_downloader.py     ← (unchanged)
│       ├── youtube_metadata.py       ← (unchanged)
│       └── queue_manager.py          ← (unchanged)
├── FIRESTORE_CREDENTIALS_SETUP.md    ← Full guide
├── CREDENTIALS_UPDATE_SUMMARY.md     ← What changed
└── CODE_CHANGES_REFERENCE.md         ← Code diffs
```

---

## 🔍 How It Works Now

```
You run: python -m ingest.ingest_worker "https://youtube.com/watch?v=ID"
  ↓
IngestWorker loads credentials from: credentials/service-account.json
  ↓
FirestoreClient initializes with explicit service account credentials
  ↓
✓ Firestore connection established
  ↓
✓ Video ingestion begins
```

---

## 🛠️ Common Commands

### Check queue status
```powershell
python -m ingest.ingest_worker --status
```

### Ingest single video
```powershell
python -m ingest.ingest_worker "https://www.youtube.com/watch?v=..."
```

### Batch ingest
```powershell
python -m ingest.ingest_worker --batch "https://youtube.com/watch?v=ID1" "https://youtube.com/watch?v=ID2"
```

### View logs
```powershell
cat logs\ingest.log
```

---

## ⚠️ Troubleshooting Quick Tips

### ❌ "Credentials file not found"
- **Check**: `Test-Path "credentials\service-account.json"`
- **If False**: File is missing, contact your admin

### ❌ "Invalid JSON"
- **Check**: `python -c "import json; json.load(open('credentials/service-account.json'))"`
- **If Error**: JSON file is corrupted

### ❌ "Cannot connect to Firestore"
- **Check**: Your internet connection
- **Check**: Google Cloud project is active
- **Check**: Service account has Firestore permissions

### ⚠️ "No credentials file found, attempting ADC..."
- This is a warning, not an error
- System is trying Application Default Credentials as fallback
- To fix: Ensure JSON is at `credentials/service-account.json`

---

## 📚 Documentation Files

1. **FIRESTORE_CREDENTIALS_SETUP.md** (Comprehensive)
   - Complete setup guide
   - How credentials are loaded
   - All troubleshooting scenarios
   - Security notes

2. **CREDENTIALS_UPDATE_SUMMARY.md** (Technical)
   - What changed in detail
   - Why it works on Windows
   - Before/after comparison
   - Benefits summary

3. **CODE_CHANGES_REFERENCE.md** (Developer)
   - Exact code changes
   - Line-by-line diffs
   - Compatibility notes
   - Testing procedures

---

## ✨ What's Better Now

| Problem | Before | After |
|---------|--------|-------|
| Works on Windows | ❌ Fails often | ✅ Always works |
| Need env vars | ✅ Yes | ❌ No |
| Error messages | ❌ Vague | ✅ Clear |
| Setup steps | ⚠️ Complex | ✅ Simple |
| Credentials location | ❌ ???? | ✅ Known location |
| Logging | ❌ Minimal | ✅ Detailed |

---

## 🔒 Security Reminder

⚠️ **IMPORTANT**: Never share or commit this file:
- `credentials/service-account.json`

If it's ever compromised:
1. Regenerate the key in Google Cloud Console
2. Download new JSON file
3. Replace `credentials/service-account.json` with new key
4. Restart your worker

---

## ✅ You're All Set!

Your ingestion worker is ready to:
- ✅ Download YouTube videos
- ✅ Extract metadata  
- ✅ Save to Firestore
- ✅ Queue for processing
- ✅ Work on Windows, Mac, Linux

**Start ingesting:** `python -m ingest.ingest_worker "https://youtube.com/watch?v=..."`

---

## 📞 Need Help?

Check these in order:

1. **FIRESTORE_CREDENTIALS_SETUP.md** - Full troubleshooting guide
2. **logs/ingest.log** - Worker logs with detailed errors
3. **CODE_CHANGES_REFERENCE.md** - Technical details
4. Run: `python -m ingest.ingest_worker --status` - Check connection

---

## 🎯 Next Steps

1. ✅ Verify setup: `python -m ingest.ingest_worker --status`
2. 🎬 Ingest a video: `python -m ingest.ingest_worker "YOUTUBE_URL"`
3. 📊 Check Firestore console to see videos being saved
4. 🚀 Deploy your full pipeline

**Happy ingesting!** 🎉
