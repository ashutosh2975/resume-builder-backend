# ResumeForge Backend — Setup & Run Guide

## Stack
| Layer | Technology |
|-------|-----------|
| Backend framework | **Flask 3** |
| Database | **PostgreSQL** |
| Auth | **JWT** (PyJWT) + **bcrypt** hashing |
| ORM | Raw **psycopg2** (no ORM bloat) |

---

## Prerequisites

| Tool | Min version | Download |
|------|-------------|---------|
| Python | 3.9 + | https://python.org |
| PostgreSQL | 13 + | https://www.postgresql.org/download/ |
| pip | any | bundled with Python |

---

## 1 — Create the PostgreSQL database

Open **psql** (or pgAdmin) and run:

```sql
CREATE DATABASE resume_builder;
```

The app will **automatically create all tables** when it starts for the first time.

---

## 2 — Configure environment variables

```bash
# Copy the example file
copy .env.example .env      # Windows
cp  .env.example .env       # Mac / Linux
```

Open `.env` and fill in your values:

```env
DATABASE_URL=postgresql://YOUR_USER:YOUR_PASSWORD@localhost:5432/resume_builder
JWT_SECRET_KEY=some-very-long-random-string-here
```

---

## 3 — Install Python dependencies

```bash
# (Optional but recommended) Create a virtual environment first
python -m venv venv
venv\Scripts\activate       # Windows
source venv/bin/activate    # Mac / Linux

# Install packages
pip install -r requirements.txt
```

---

## 4 — Run the backend

**Windows shortcut:**
```
start.bat
```

**Or manually:**
```bash
python app.py
```

The server starts on **http://localhost:5000**

---

## API Endpoints

### Health check
```
GET  /api/health
```

### Register a new account
```
POST /api/auth/register
Content-Type: application/json

{
  "full_name": "Jane Smith",
  "email":     "jane@example.com",
  "password":  "securePassword123"
}
```
**Returns:** `{ token, user }` on success · `{ errors }` on validation failure

### Sign in
```
POST /api/auth/login
Content-Type: application/json

{
  "email":    "jane@example.com",
  "password": "securePassword123"
}
```
**Returns:** `{ token, user }`

### Get current user (protected)
```
GET  /api/auth/me
Authorization: Bearer <token>
```
**Returns:** `{ user }`

---

## Resume Extraction / Parsing

The backend provides intelligent resume extraction using **Gemini API with automatic fallback to manual extraction**.

### Pipeline:
1. **User uploads file** (PDF, DOCX, or TXT)
2. **Gemini AI extraction** - Attempts to extract structured resume data (~90% accuracy with proper formatting)
3. **Manual extraction fallback** - If AI fails, regex-based manual extraction is used
4. **Return method indicator** - Response includes `"method": "ai"` or `"method": "manual"` so frontend can show extraction type

### Endpoint:
```
POST /api/ai/parse-resume
- Accepts: multipart/form-data with 'file' field
- Returns: { result: resume_data, method: "ai"|"manual", success: true }
```

### Extraction Accuracy:
- **AI (Gemini)**: ~90% correct with well-formatted resumes
- **Manual (Regex)**: ~70% correct, best-effort extraction as fallback

## AI Features

The backend provides AI-powered resume enhancement features through the `/api/ai/enhance` endpoint.
Users can improve grammar, shorten content, expand descriptions, optimize for ATS, or regenerate bullet points.

The system tries multiple AI providers in order (GROQ, Gemini, DeepSeek, OpenAI) with automatic fallback.

## Frontend ↔ Backend flow

```
Browser                        Flask API                   PostgreSQL
  │                               │                             │
  │  POST /api/auth/register      │                             │
  │──────────────────────────────▶│                             │
  │                               │  INSERT INTO users          │
  │                               │────────────────────────────▶│
  │                               │◀────────────────────────────│
  │  { token, user }              │                             │
  │◀──────────────────────────────│                             │
  │                               │                             │
  │  Token stored in localStorage │                             │
  │  Redirect → /dashboard        │                             │
```

---

## Security notes

- Passwords are hashed with **bcrypt** (cost factor 12) — never stored in plain text.
- JWTs expire after **7 days** and are verified on every protected request.
- CORS is restricted to `http://localhost:5173` in development. Update `app.py` for production domains.
- Change `JWT_SECRET_KEY` to a strong random value before deploying.
