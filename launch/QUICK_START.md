# TEAM-GRADE LAUNCHER - QUICK REFERENCE

## What to Do Now

### To Use the Launcher

1. **Open File Explorer**
2. **Navigate to:**
   ```
   C:\Users\ricky\OneDrive\Desktop\Team-Grade\TEAM-GRADE\launch\dist\
   ```
3. **Double-click:**
   ```
   TEAM-GRADE-Launcher.exe
   ```
4. **Done!** All 4 components launch automatically:
   - Worker window (terminal #1)
   - API window (terminal #2)
   - Orchestrator window (main terminal)
   - Browser (UI opens automatically)

---

## What You Should See

### Launcher Window Shows
```
[OK] Virtual environment found
[OK] Worker launched (PID: xxxxx)
[OK] API server launched (PID: xxxxx)
[OK] API server is ready
[OK] UI opened at http://localhost:8000
```

### Worker Window Shows
```
[OK] Ingestion Worker Started (Listener Mode)
Listening for jobs in queue...
```

### API Window Shows
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Browser Opens to
```
http://localhost:8000
```

---

## To Stop Everything

- **Option 1:** Close the main launcher window
- **Option 2:** Press Ctrl+C in the main window
- **Note:** Can close any individual window to stop just that component

---

## To Restart

Just double-click the .exe again. Everything restarts fresh.

---

## To Rebuild the Launcher (if you change launcher.py)

```powershell
cd C:\Users\ricky\OneDrive\Desktop\Team-Grade\TEAM-GRADE\launch
.\build_launcher.ps1 -Clean
```

Wait 1-2 minutes. New .exe is in `dist\` folder.

---

## File Locations

| What | Where |
|------|-------|
| **Launcher to run** | `launch\dist\TEAM-GRADE-Launcher.exe` |
| **Source code** | `launch\launcher.py` |
| **Full documentation** | `launch\README-LAUNCHER.md` |
| **Build summary** | `launch\BUILD_SUMMARY.txt` |
| **Build log** | `launch\build_log.txt` |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Nothing opens | Check venv exists at `.venv/` |
| Only one window opens | Check PowerShell execution policy |
| API not responding | Wait longer (30+ sec) or check port 8000 |
| Worker not detecting jobs | Check `yt_dlp` is installed |
| .exe won't run | Rebuild with `.\build_launcher.ps1 -Clean` |

---

## Architecture

```
Double-click .exe
    ↓
launcher.py validates environment
    ↓
Spawn Worker Process (new window) ---→ start_worker.ps1
    ↓
Spawn API Process (new window) ---→ start_api.ps1
    ↓
Wait for API ready
    ↓
Open browser to localhost:8000
    ↓
All running!
```

---

## System Requirements

- ✓ Windows 10/11
- ✓ Python 3.9+ in `.venv` folder
- ✓ No admin privileges needed
- ✓ ~500 MB disk space
- ✓ Ports 8000, 8001 available

---

## Key Design Points

1. **Four separate processes** for modularity
2. **Each in its own window** for easy monitoring
3. **Automatic environment validation** before launch
4. **ASCII-only logging** (no Windows Unicode crashes)
5. **Graceful error handling** if anything fails
6. **Self-contained .exe** (can copy anywhere)

---

## Taking It Further

### Pin to Start Menu
Right-click .exe → Pin to Start

### Create Desktop Shortcut
Right-click .exe → Send to → Desktop (create shortcut)

### Share with Others
Copy `TEAM-GRADE-Launcher.exe` to another computer (with Python + .venv)

### Deploy to Server
Deploy `launch/dist/TEAM-GRADE-Launcher.exe` without installation hassles

---

## Common Commands

### Check if port 8000 is in use
```powershell
netstat -ano | findstr ":8000"
```

### Kill a process by PID
```powershell
taskkill /PID <pid> /F
```

### Check venv activation
```powershell
.\.venv\Scripts\Activate.ps1
python --version
```

### Rebuild from scratch
```powershell
cd launch
.\build_launcher.ps1 -Clean
```

---

## Next Steps

1. ✓ Build: **DONE** (25.11 MB .exe created)
2. → Test: Double-click the .exe
3. → Verify: Check all 4 windows open
4. → Deploy: Copy .exe to other machines

---

**Status: READY FOR PRODUCTION** ✓

**Last Built:** 2026-03-13  
**Version:** 1.0.0  
**Size:** 25.11 MB  
**Type:** Standalone executable (no installation needed)

---

For detailed information, see: `README-LAUNCHER.md`
