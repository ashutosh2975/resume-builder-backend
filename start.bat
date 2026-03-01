@echo off
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║    ResumeForge Backend — Flask Server    ║
echo  ╚══════════════════════════════════════════╝
echo.

REM ── Ensure .env exists ──────────────────────────────────────────────────────
if not exist ".env" (
    echo  [SETUP] .env not found — copying from .env.example...
    copy ".env.example" ".env" >nul
    echo  [INFO]  .env created. Using SQLite by default (no PostgreSQL needed).
    echo  [INFO]  Edit .env to switch to PostgreSQL when ready.
    echo.
)

REM ── Activate virtual environment if it exists ────────────────────────────────
if exist "venv\Scripts\activate.bat" (
    echo  [*] Activating virtual environment...
    call venv\Scripts\activate.bat
) else (
    echo  [INFO] No venv found — using system Python.
    echo  [TIP]  Create one with: python -m venv venv
    echo.
)

REM ── Install / verify dependencies ────────────────────────────────────────────
echo  [*] Checking Python dependencies...
pip install -r requirements.txt -q --disable-pip-version-check
echo  [OK] Dependencies ready.
echo.

REM ── Start Flask ──────────────────────────────────────────────────────────────
echo  [*] Starting Flask API on http://localhost:5000
echo  [*] Database: SQLite (local) or PostgreSQL (if DATABASE_URL is set in .env)
echo  [*] Press Ctrl+C to stop.
echo.

python app.py
