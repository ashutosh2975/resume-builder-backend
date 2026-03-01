import os
import re
import json
import bcrypt
import jwt
import datetime
import urllib.request
import urllib.error
import io
import docx
from pdfminer.high_level import extract_text as extract_pdf_text
from groq import Groq
from flask import Flask, request, jsonify, make_response
from dotenv import load_dotenv
from functools import wraps

load_dotenv()

app = Flask(__name__)

# ─── CORS: allow any localhost / 127.0.0.1 origin on any port ────────────────
_LOCALHOST_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$")

@app.after_request
def _add_cors(response):
    origin = request.headers.get("Origin", "")
    if _LOCALHOST_RE.match(origin):
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return response

@app.route("/api/<path:path>", methods=["OPTIONS"])
@app.route("/api/", methods=["OPTIONS"])
def _handle_options(path=""):
    return make_response("", 204)

SECRET_KEY   = os.getenv("JWT_SECRET_KEY", "your-super-secret-jwt-key-change-in-production")
DATABASE_URL = os.getenv("DATABASE_URL", "")

# ─── Database abstraction ─────────────────────────────────────────────────────

USE_POSTGRES = DATABASE_URL.startswith("postgresql")

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras

    def get_db():
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

    def init_db():
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          SERIAL PRIMARY KEY,
                full_name   VARCHAR(120) NOT NULL,
                email       VARCHAR(255) UNIQUE NOT NULL,
                password    VARCHAR(255) NOT NULL,
                created_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS resumes (
                id          SERIAL PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        VARCHAR(255) NOT NULL DEFAULT 'Untitled Resume',
                template_id VARCHAR(100) NOT NULL DEFAULT 'modern-01',
                data        TEXT NOT NULL DEFAULT '{}',
                updated_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
        """)
        conn.commit(); cur.close(); conn.close()
        print("✅  PostgreSQL database initialized.")

else:
    import sqlite3
    DB_PATH = os.path.join(os.path.dirname(__file__), "resume_builder.db")

    def _dict_factory(cursor, row):
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    def get_db():
        try:
            # ensure the directory exists (not usually needed for file in same folder)
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = _dict_factory
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        except Exception as e:
            print(f"[DB] failed to open sqlite database at {DB_PATH}: {e}")
            raise

    def init_db():
        conn = get_db()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name   TEXT    NOT NULL,
                email       TEXT    UNIQUE NOT NULL,
                password    TEXT    NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS resumes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        TEXT    NOT NULL DEFAULT 'Untitled Resume',
                template_id TEXT    NOT NULL DEFAULT 'modern-01',
                data        TEXT    NOT NULL DEFAULT '{}',
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit(); conn.close()
        print(f"✅  SQLite database initialized at: {DB_PATH}")


# ─── Helpers ──────────────────────────────────────────────────────────────────

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def make_token(user_id: int, email: str) -> str:
    payload = {
        "sub":   user_id,
        "email": email,
        "iat":   datetime.datetime.utcnow(),
        "exp":   datetime.datetime.utcnow() + datetime.timedelta(days=7),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid authorization header"}), 401
        token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token has expired. Please sign in again."}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token. Please sign in again."}), 401
        return f(payload, *args, **kwargs)
    return decorated


def is_unique_violation(e):
    msg = str(e).lower()
    if USE_POSTGRES:
        return hasattr(e, "pgcode") and e.pgcode == "23505"
    return "unique" in msg


def db_exec(conn, sql, params=()):
    """Unified execute for both PG (uses %s) and SQLite (uses ?)."""
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur
    else:
        return conn.execute(sql, params)


def q(sql):
    """Convert ? placeholders to %s for PostgreSQL."""
    if USE_POSTGRES:
        return sql.replace("?", "%s")
    return sql


# ─── Resume Extraction Functions ──────────────────────────────────────────────

def manual_extract_resume(text: str) -> dict:
    """
    Fallback manual extraction using regex patterns.
    Returns structured resume data when AI extraction fails.
    """
    lines = text.split('\n')
    
    # Extract contact info
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', text)
    phone_match = re.search(r'(\+?[\d\s\-\(\).]{7,20})', text)
    linkedin_match = re.search(r'linkedin\.com/in/[\w-]+', text)
    
    # Basic name extraction - usually first line
    name = ""
    for line in lines[:5]:
        line_clean = line.strip()
        if line_clean and len(line_clean) < 60 and not re.search(r'[@|•]', line):
            name = line_clean
            break
    
    # Extract sections using regex
    def extract_section(pattern_keywords: list) -> str:
        """Extract section content between headers"""
        for i, line in enumerate(lines):
            if any(kw.lower() in line.lower() for kw in pattern_keywords):
                start = i + 1
                end = len(lines)
                for j in range(i + 1, len(lines)):
                    if any(kw.lower() in lines[j].lower() for kw in ['experience', 'education', 'skills', 'projects', 'summary']):
                        end = j
                        break
                return '\n'.join(lines[start:end])
        return ""
    
    # Extract experience entries
    experience = []
    exp_section = extract_section(['experience', 'work', 'professional'])
    if exp_section:
        # Split by company names and positions
        entries = re.split(r'\n(?=[A-Z][a-z\s]+(?:Engineer|Developer|Manager|Designer|Analyst|Architect|Manager))', exp_section)
        for entry in entries[:5]:  # Limit to 5 jobs
            if entry.strip():
                entry_lines = entry.strip().split('\n')
                if entry_lines:
                    experience.append({
                        "id": str(len(experience)),
                        "company": entry_lines[0].split('|')[0].strip() if '|' in entry_lines[0] else entry_lines[0][:30],
                        "position": entry_lines[0][:40],
                        "startDate": "",
                        "endDate": "",
                        "description": '\n'.join(entry_lines[1:])[:500]
                    })
    
    # Extract education
    education = []
    edu_section = extract_section(['education', 'academic', 'university', 'college'])
    if edu_section:
        entries = re.split(r'\n(?=[A-Z])', edu_section)
        for entry in entries[:3]:  # Limit to 3 education entries
            if entry.strip() and any(kw in entry.lower() for kw in ['degree', 'bachelor', 'master', 'phd', 'university', 'college']):
                education.append({
                    "id": str(len(education)),
                    "school": entry.split('|')[0].strip() if '|' in entry else entry[:50],
                    "degree": "Degree" if not re.search(r'bachelor|master|phd|associate', entry, re.I) else re.search(r'bachelor|master|phd|associate', entry, re.I).group(0).title(),
                    "field": "",
                    "startDate": "",
                    "endDate": ""
                })
    
    # Extract skills
    skills = []
    skills_section = extract_section(['skills', 'technical', 'competencies'])
    if skills_section:
        skill_list = re.split(r'[,•\n]', skills_section)
        skills = [s.strip() for s in skill_list if s.strip() and len(s.strip()) < 50][:20]
    
    # Extract projects
    projects = []
    proj_section = extract_section(['projects', 'portfolio', 'notable'])
    if proj_section:
        entries = re.split(r'\n(?=[A-Z])', proj_section)
        for entry in entries[:3]:  # Limit to 3 projects
            if entry.strip():
                entry_lines = entry.strip().split('\n')
                if entry_lines:
                    projects.append({
                        "id": str(len(projects)),
                        "name": entry_lines[0][:60],
                        "role": "Developer",
                        "url": "",
                        "startDate": "",
                        "endDate": "",
                        "description": '\n'.join(entry_lines[1:])[:300]
                    })
    
    return {
        "personalInfo": {
            "fullName": name,
            "email": email_match.group(0) if email_match else "",
            "phone": phone_match.group(0).strip() if phone_match else "",
            "location": "",
            "title": "",
            "website": "",
            "linkedin": linkedin_match.group(0) if linkedin_match else "",
            "photo": ""
        },
        "summary": extract_section(['summary', 'objective', 'profile'])[:500],
        "experience": experience,
        "education": education,
        "projects": projects,
        "skills": skills,
        "languages": [],
        "certifications": []
    }


def extract_with_groq(text: str) -> dict | None:
    """
    Try to extract resume using GROQ API (free, unlimited).
    Returns None if extraction fails, falls back to manual extraction.
    """
    if not GROQ_API_KEY:
        return None
    
    try:
        prompt = f"""Extract resume information from the following text and return as JSON.
Return ONLY valid JSON with this exact structure:
{{
  "personalInfo": {{
    "fullName": "string",
    "email": "string", 
    "phone": "string",
    "location": "string",
    "title": "string",
    "website": "string",
    "linkedin": "string"
  }},
  "summary": "string (max 300 chars)",
  "experience": [
    {{"company": "string", "position": "string", "startDate": "string", "endDate": "string", "description": "string"}},
  ],
  "education": [
    {{"school": "string", "degree": "string", "field": "string", "startDate": "string", "endDate": "string"}},
  ],
  "projects": [
    {{"name": "string", "role": "string", "description": "string", "startDate": "string", "endDate": "string"}},
  ],
  "skills": ["skill1", "skill2"]
}}

Resume text:
{text[:8000]}

Return ONLY the JSON object, no markdown or explanations."""

        client = Groq(api_key=GROQ_API_KEY)
        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=2048
        )
        
        response_text = message.choices[0].message.content
        
        # Clean markdown if present
        response_text = re.sub(r"```json", "", response_text)
        response_text = re.sub(r"```", "", response_text).strip()
        
        extracted = json.loads(response_text)
        
        # Ensure required structure
        if "personalInfo" in extracted and "skills" in extracted:
            # Add IDs and ensure all fields exist
            for i, exp in enumerate(extracted.get("experience", [])):
                exp["id"] = str(i)
            for i, edu in enumerate(extracted.get("education", [])):
                edu["id"] = str(i)
            for i, proj in enumerate(extracted.get("projects", [])):
                proj["id"] = str(i)
                proj["startDate"] = proj.get("startDate", "")
                proj["endDate"] = proj.get("endDate", "")
                proj["url"] = ""  # Add url field
                
            return extracted
    except Exception as e:
        print(f"[Extract] GROQ extraction failed: {e}")
    
    return None


def extract_with_gemini(text: str) -> dict | None:
    """
    Try to extract resume using Gemini API.
    Returns None if extraction fails, falls back to manual extraction.
    """
    if not GEMINI_API_KEY:
        return None
    
    try:
        prompt = f"""Extract resume information from the following text and return as JSON.
Return ONLY valid JSON with this exact structure:
{{
  "personalInfo": {{
    "fullName": "string",
    "email": "string", 
    "phone": "string",
    "location": "string",
    "title": "string",
    "website": "string",
    "linkedin": "string"
  }},
  "summary": "string (max 300 chars)",
  "experience": [
    {{"company": "string", "position": "string", "startDate": "string", "endDate": "string", "description": "string"}},
  ],
  "education": [
    {{"school": "string", "degree": "string", "field": "string", "startDate": "string", "endDate": "string"}},
  ],
  "projects": [
    {{"name": "string", "role": "string", "description": "string", "startDate": "string", "endDate": "string"}},
  ],
  "skills": ["skill1", "skill2"]
}}

Resume text:
{text[:8000]}

Return ONLY the JSON object, no markdown or explanations."""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 2048}
        }
        
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            response_text = result["candidates"][0]["content"]["parts"][0]["text"]
            
            # Clean markdown if present
            response_text = re.sub(r"```json", "", response_text)
            response_text = re.sub(r"```", "", response_text).strip()
            
            extracted = json.loads(response_text)
            
            # Ensure required structure
            if "personalInfo" in extracted and "skills" in extracted:
                # Add IDs and ensure all fields exist
                for i, exp in enumerate(extracted.get("experience", [])):
                    exp["id"] = str(i)
                for i, edu in enumerate(extracted.get("education", [])):
                    edu["id"] = str(i)
                for i, proj in enumerate(extracted.get("projects", [])):
                    proj["id"] = str(i)
                    proj["startDate"] = proj.get("startDate", "")
                    proj["endDate"] = proj.get("endDate", "")
                    proj["url"] = ""  # Add url field
                    
                return extracted
    except Exception as e:
        print(f"[Extract] Gemini extraction failed: {e}")
    
    return None


# ─── Health ───────────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    # simple DB ping to detect connectivity issues
    db_status = "unknown"
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
        else:
            conn.execute("SELECT 1")
        conn.close()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {e}"
        print(f"[Health] DB ping failed: {e}")

    return jsonify({
        "status": "ok",
        "message": "ResumeForge API 🚀",
        "database": "PostgreSQL" if USE_POSTGRES else "SQLite",
        "dbStatus": db_status,
    })


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    data      = request.get_json(silent=True) or {}
    full_name = (data.get("full_name") or "").strip()
    email     = (data.get("email")     or "").strip().lower()
    password  = (data.get("password")  or "").strip()

    errors = {}
    if not full_name or len(full_name) < 2:
        errors["full_name"] = "Full name must be at least 2 characters."
    if not EMAIL_RE.match(email):
        errors["email"] = "Please enter a valid email address."
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    if errors:
        return jsonify({"errors": errors}), 422

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("INSERT INTO users (full_name, email, password) VALUES (%s,%s,%s) RETURNING id,full_name,email",
                        (full_name, email, hashed))
            user = cur.fetchone(); conn.commit(); cur.close()
        else:
            cur = conn.execute("INSERT INTO users (full_name, email, password) VALUES (?,?,?)", (full_name, email, hashed))
            conn.commit()
            user = conn.execute("SELECT id,full_name,email FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
    except Exception as e:
        if is_unique_violation(e):
            return jsonify({"errors": {"email": "An account with this email already exists."}}), 409
        return jsonify({"error": f"Database error: {e}"}), 500

    return jsonify({"message": "Account created!", "token": make_token(user["id"], user["email"]),
                    "user": {"id": user["id"], "full_name": user["full_name"], "email": user["email"]}}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email")    or "").strip().lower()
    password = (data.get("password") or "").strip()
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT id,full_name,email,password FROM users WHERE email=%s", (email,))
            user = cur.fetchone(); cur.close()
        else:
            user = conn.execute("SELECT id,full_name,email,password FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500

    if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
        return jsonify({"error": "Invalid email or password."}), 401

    return jsonify({"message": "Signed in!", "token": make_token(user["id"], user["email"]),
                    "user": {"id": user["id"], "full_name": user["full_name"], "email": user["email"]}})


@app.route("/api/auth/me", methods=["GET"])
@token_required
def me(payload):
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT id,full_name,email,created_at FROM users WHERE id=%s", (payload["sub"],))
            user = cur.fetchone(); cur.close()
        else:
            user = conn.execute("SELECT id,full_name,email,created_at FROM users WHERE id=?", (payload["sub"],)).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({"error": f"Database error: {e}"}), 500
    if not user:
        return jsonify({"error": "User not found."}), 404
    return jsonify({"user": {"id": user["id"], "full_name": user["full_name"],
                             "email": user["email"], "created_at": str(user["created_at"])}})


# ─── Resumes CRUD ─────────────────────────────────────────────────────────────

@app.route("/api/resumes", methods=["GET"])
@token_required
def list_resumes(payload):
    user_id = payload["sub"]
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT id,name,template_id,updated_at FROM resumes WHERE user_id=%s ORDER BY updated_at DESC", (user_id,))
            rows = cur.fetchall(); cur.close()
        else:
            rows = conn.execute("SELECT id,name,template_id,updated_at FROM resumes WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    resumes = [{"id": str(r["id"]), "name": r["name"], "templateId": r["template_id"], "updatedAt": str(r["updated_at"])} for r in rows]
    return jsonify({"resumes": resumes})


@app.route("/api/resumes", methods=["POST"])
@token_required
def create_resume(payload):
    user_id = payload["sub"]
    body    = request.get_json(silent=True) or {}
    name    = (body.get("name") or "Untitled Resume").strip()
    tpl_id  = (body.get("template_id") or "modern-01").strip()
    data    = json.dumps(body.get("data") or {})
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("INSERT INTO resumes (user_id,name,template_id,data) VALUES (%s,%s,%s,%s) RETURNING id,name,template_id,updated_at",
                        (user_id, name, tpl_id, data))
            row = cur.fetchone(); conn.commit(); cur.close()
        else:
            cur = conn.execute("INSERT INTO resumes (user_id,name,template_id,data) VALUES (?,?,?,?)", (user_id, name, tpl_id, data))
            conn.commit()
            row = conn.execute("SELECT id,name,template_id,updated_at FROM resumes WHERE id=?", (cur.lastrowid,)).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"message": "Resume saved!", "resume": {"id": str(row["id"]), "name": row["name"], "templateId": row["template_id"], "updatedAt": str(row["updated_at"])}}), 201


@app.route("/api/resumes/<int:resume_id>", methods=["GET"])
@token_required
def get_resume(payload, resume_id):
    user_id = payload["sub"]
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("SELECT * FROM resumes WHERE id=%s AND user_id=%s", (resume_id, user_id))
            row = cur.fetchone(); cur.close()
        else:
            row = conn.execute("SELECT * FROM resumes WHERE id=? AND user_id=?", (resume_id, user_id)).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not row:
        return jsonify({"error": "Resume not found."}), 404
    return jsonify({"resume": {"id": str(row["id"]), "name": row["name"], "templateId": row["template_id"],
                               "data": json.loads(row["data"]), "updatedAt": str(row["updated_at"])}})


@app.route("/api/resumes/<int:resume_id>", methods=["PUT"])
@token_required
def update_resume(payload, resume_id):
    user_id = payload["sub"]
    body    = request.get_json(silent=True) or {}
    name    = (body.get("name") or "Untitled Resume").strip()
    tpl_id  = (body.get("template_id") or "modern-01").strip()
    data    = json.dumps(body.get("data") or {})
    now     = datetime.datetime.utcnow().isoformat()
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("UPDATE resumes SET name=%s,template_id=%s,data=%s,updated_at=NOW() WHERE id=%s AND user_id=%s RETURNING id,name,template_id,updated_at",
                        (name, tpl_id, data, resume_id, user_id))
            row = cur.fetchone(); conn.commit(); cur.close()
        else:
            conn.execute("UPDATE resumes SET name=?,template_id=?,data=?,updated_at=? WHERE id=? AND user_id=?",
                         (name, tpl_id, data, now, resume_id, user_id))
            conn.commit()
            row = conn.execute("SELECT id,name,template_id,updated_at FROM resumes WHERE id=?", (resume_id,)).fetchone()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not row:
        return jsonify({"error": "Resume not found."}), 404
    return jsonify({"message": "Resume updated!", "resume": {"id": str(row["id"]), "name": row["name"], "templateId": row["template_id"], "updatedAt": str(row["updated_at"])}})


@app.route("/api/resumes/<int:resume_id>", methods=["DELETE"])
@token_required
def delete_resume(payload, resume_id):
    user_id = payload["sub"]
    try:
        conn = get_db()
        if USE_POSTGRES:
            cur = conn.cursor()
            cur.execute("DELETE FROM resumes WHERE id=%s AND user_id=%s", (resume_id, user_id))
            conn.commit(); cur.close()
        else:
            conn.execute("DELETE FROM resumes WHERE id=? AND user_id=?", (resume_id, user_id))
            conn.commit()
        conn.close()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"message": "Resume deleted."})


# ─── AI Enhance Proxy ─────────────────────────────────────────────────────────

GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

MODE_PROMPTS = {
    "improve":    "You are a professional resume writer. Improve the grammar, clarity, and professional tone of this resume text. Keep the same facts, just make it sound more polished and impactful. Return ONLY the improved text, no explanations.",
    "shorten":    "You are a professional resume writer. Shorten this resume text to be more concise and impactful. Remove unnecessary words while keeping the key achievements and metrics. Return ONLY the shortened text as bullet points starting with action verbs.",
    "expand":     "You are a professional resume writer. Expand this resume text with more detail, impact metrics, and professional language. Add relevant context and accomplishments. Return ONLY the expanded text as bullet points.",
    "ats":        "You are an ATS optimization expert. Rewrite this resume text to be highly ATS-friendly: use standard action verbs, quantifiable achievements, and industry-standard keywords. Return ONLY the optimized text as bullet points.",
    "regenerate": "You are a professional resume writer. Completely rewrite this resume content from a fresh angle, keeping the same job/role but using different language and structure. Return ONLY the rewritten text as bullet points starting with strong action verbs.",
}


def _post_json(url: str, headers: dict, body: dict, timeout: int = 12) -> dict:
    """Make a JSON POST request using stdlib urllib (no dependencies)."""
    data = json.dumps(body).encode("utf-8")
    req  = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _try_gemini(text: str, mode: str) -> str | None:
    if not GEMINI_API_KEY:
        return None
    try:
        prompt = f"{MODE_PROMPTS.get(mode, MODE_PROMPTS['improve'])}\n\nResume text:\n{text}"
        url    = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        body   = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024}}
        resp   = _post_json(url, {"Content-Type": "application/json"}, body)
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:
        if "429" in str(exc):
            print(f"[AI] Gemini rate limit exceeded (429). Try again in a minute or add a paid key.")
        else:
            print(f"[AI] Gemini failed: {exc}")
        return None


def _try_deepseek(text: str, mode: str) -> str | None:
    if not DEEPSEEK_API_KEY:
        return None
    try:
        prompt = f"{MODE_PROMPTS.get(mode, MODE_PROMPTS['improve'])}\n\nResume text:\n{text}"
        body   = {
            "model":    "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": 1024,
        }
        resp = _post_json(
            "https://api.deepseek.com/v1/chat/completions",
            {"Content-Type": "application/json", "Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            body,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"[AI] DeepSeek failed: {exc}")
        return None


def _try_openai(text: str, mode: str) -> str | None:
    if not OPENAI_API_KEY:
        return None
    try:
        prompt = f"{MODE_PROMPTS.get(mode, MODE_PROMPTS['improve'])}\n\nResume text:\n{text}"
        body   = {
            "model":    "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7, "max_tokens": 1024,
        }
        resp = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"},
            body,
        )
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"[AI] OpenAI failed: {exc}")
        return None


def _try_groq(text: str, mode: str) -> str | None:
    """Try GROQ API — fast and free alternative for AI enhancement."""
    if not GROQ_API_KEY:
        return None
    try:
        prompt = f"{MODE_PROMPTS.get(mode, MODE_PROMPTS['improve'])}\n\nText to enhance:\n{text}"
        
        client = Groq(api_key=GROQ_API_KEY)
        message = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1024
        )
        
        return message.choices[0].message.content.strip()
    except Exception as exc:
        print(f"[AI] GROQ failed: {exc}")
        return None

# ─── Resume Parsing/Extraction ────────────────────────────────────────────────

@app.route("/api/ai/parse-resume", methods=["POST"])
def parse_resume():
    """
    Extract resume data from uploaded file using GROQ AI first (free, unlimited),
    then Gemini if needed, fallback to manual extraction.
    Returns: {result: resume_data, method: "ai" | "manual"}
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400
    
    # Extract text from file
    text = ""
    try:
        content = file.read()
        ext = file.filename.split('.')[-1].lower()
        
        if ext == 'pdf':
            text = extract_pdf_text(io.BytesIO(content))
        elif ext == 'docx':
            doc = docx.Document(io.BytesIO(content))
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        elif ext == 'txt':
            text = content.decode('utf-8', errors='ignore')
        else:
            return jsonify({"error": "Unsupported file format. Please upload PDF, DOCX, or TXT."}), 400
    
    except Exception as e:
        print(f"[Parse] Error extracting text: {e}")
        return jsonify({"error": "Failed to read file"}), 500
    
    if not text.strip():
        return jsonify({"error": "File is empty or unreadable"}), 400
    
    # Try GROQ extraction first (free, unlimited)
    print(f"[Parse] Attempting GROQ extraction...")
    result = extract_with_groq(text)
    
    if result:
        print(f"[Parse] Success with GROQ AI")
        return jsonify({"result": result, "method": "ai", "success": True})
    
    # Fallback to Gemini if GROQ fails
    print(f"[Parse] GROQ failed, trying Gemini extraction...")
    result = extract_with_gemini(text)
    
    if result:
        print(f"[Parse] Success with Gemini AI")
        return jsonify({"result": result, "method": "ai", "success": True})
    
    # Final fallback to manual extraction
    print(f"[Parse] AI methods failed, falling back to manual extraction...")
    try:
        result = manual_extract_resume(text)
        print(f"[Parse] Manual extraction complete")
        return jsonify({"result": result, "method": "manual", "success": True})
    except Exception as e:
        print(f"[Parse] Manual extraction failed: {e}")
        return jsonify({"error": "Failed to extract resume data", "method": "manual", "success": False}), 500

@app.route("/api/ai/enhance", methods=["POST"])
def ai_enhance():
    """Public AI proxy — no auth required so guests can also use AI."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    mode = (data.get("mode") or "improve").strip()

    if not text:
        return jsonify({"error": "text is required"}), 400
    if mode not in MODE_PROMPTS:
        mode = "improve"
    if len(text) > 8000:
        return jsonify({"error": "text too long (max 8000 chars)"}), 400

    # Try AI providers in order: GROQ (fast/free) → Gemini (free) → DeepSeek → OpenAI
    result = _try_groq(text, mode) or _try_gemini(text, mode) or _try_deepseek(text, mode) or _try_openai(text, mode)

    if result:
        return jsonify({"result": result, "provider": "ai"})
    return jsonify({"result": None, "provider": "none"}), 503


# ─── AI Resume Suggestions ────────────────────────────────────────────────────

@app.route("/api/ai/suggest", methods=["POST"])
def ai_suggest():
    """
    Takes a parsed resume JSON and returns AI-powered improvement suggestions.
    No auth required — works for all users.
    """
    data   = request.get_json(silent=True) or {}
    resume = data.get("resume") or {}

    if not resume:
        return jsonify({"error": "resume data is required"}), 400

    # Build a compact text summary of the resume to send to AI
    lines = []
    pi = resume.get("personalInfo", {})
    if pi.get("fullName"):  lines.append(f"Name: {pi['fullName']}")
    if pi.get("title"):     lines.append(f"Title: {pi['title']}")
    if resume.get("summary"):
        lines.append(f"Summary: {resume['summary'][:300]}")
    exp_list = resume.get("experience", [])
    for e in exp_list[:3]:
        lines.append(f"Experience: {e.get('position','')} at {e.get('company','')} ({e.get('startDate','')}–{e.get('endDate','')})")
        if e.get("description"):
            lines.append(f"  Bullets: {e['description'][:200]}")
    edu_list = resume.get("education", [])
    for ed in edu_list[:2]:
        lines.append(f"Education: {ed.get('degree','')} {ed.get('field','')} at {ed.get('school','')}")
    skills = resume.get("skills", [])
    if skills:
        lines.append(f"Skills: {', '.join(skills[:20])}")
    proj_list = resume.get("projects", [])
    for p in proj_list[:2]:
        lines.append(f"Project: {p.get('name','')} — {p.get('description','')[:150]}")

    resume_text = "\n".join(lines)

    prompt = f"""You are an expert resume coach and career counselor. Analyze the following resume and provide exactly 5 concise, actionable improvement suggestions.

Resume:
{resume_text}

Instructions:
- Each suggestion must be specific and immediately actionable.
- Cover areas like: weak bullet points, missing quantification, ATS keywords, summary quality, gaps, or missing sections.
- Format your response as a valid JSON array of exactly 5 objects, each with these fields:
  - "category": one of "Summary", "Experience", "Skills", "ATS", "Format", "Projects", "Education", "Missing"
  - "title": short title (max 6 words)
  - "suggestion": actionable advice (1-2 sentences, specific)
  - "priority": "high", "medium", or "low"

Return ONLY the JSON array, no other text, no markdown fences.
"""

    def _suggest_groq():
        if not GROQ_API_KEY: return None
        body = {
            "model": "mixtral-8x7b-32768",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
            "max_tokens": 1024,
        }
        resp = _post_json(
            "https://api.groq.com/openai/v1/chat/completions",
            {"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_API_KEY}"},
            body,
        )
        return resp["choices"][0]["message"]["content"].strip()

    def _suggest_gemini():
        if not GEMINI_API_KEY: return None
        url  = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4, "maxOutputTokens": 1024}}
        resp = _post_json(url, {"Content-Type": "application/json"}, body)
        return resp["candidates"][0]["content"]["parts"][0]["text"].strip()

    def _suggest_openai():
        if not OPENAI_API_KEY: return None
        body = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4, "max_tokens": 1024,
        }
        resp = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Content-Type": "application/json", "Authorization": f"Bearer {OPENAI_API_KEY}"},
            body,
        )
        return resp["choices"][0]["message"]["content"].strip()

    try:
        raw = None
        # Try GROQ first (fastest + free)
        try:
            raw = _suggest_groq()
        except Exception as groq_err:
            print(f"[Suggest] GROQ failed: {groq_err}")
            # Fallback to Gemini
            try:
                raw = _suggest_gemini()
            except Exception as ge:
                print(f"[Suggest] Gemini failed: {ge}")
                # Final fallback to OpenAI
                try:
                    raw = _suggest_openai()
                except Exception as oe:
                    print(f"[Suggest] OpenAI also failed: {oe}")

        if not raw:
            return jsonify({"suggestions": [], "provider": "none"}), 503

        # Clean markdown fences
        raw = re.sub(r"```(json)?", "", raw).strip()
        start = raw.find("[")
        end   = raw.rfind("]")
        if start != -1 and end != -1:
            raw = raw[start:end+1]

        suggestions = json.loads(raw)
        return jsonify({"suggestions": suggestions, "provider": "ai"})

    except Exception as e:
        print(f"[Suggest] Failed: {e}")
        return jsonify({"suggestions": [], "provider": "error"}), 500


# ─── Gemini AI Enhancement (Direct) ───────────────────────────────────────────

# ─── Skill Suggestions via GROQ ──────────────────────────────────────────────

@app.route("/api/ai/skill-suggestions", methods=["POST"])
def ai_skill_suggestions():
    """
    Generate skill suggestions based on partial input.
    Uses GROQ to provide intelligent suggestions related to the input text.
    No auth required — works for all users.
    """
    data = request.get_json(silent=True) or {}
    input_text = (data.get("input") or "").strip()

    if not input_text or len(input_text) < 2:
        return jsonify({"suggestions": []})
    if len(input_text) > 500:
        return jsonify({"error": "input too long (max 500 chars)"}), 400

    prompt = f"""You are a resume expert. Given a partial skill or technology name, suggest up to 8 similar or related professional skills that would be valuable on a technical resume.

Input: {input_text}

Instructions:
- Return ONLY a JSON array of exactly 8 (or fewer if less applicable) skill suggestions as strings.
- Skills should be professional and resume-appropriate.
- No duplicates.
- Return ONLY the JSON array, no markdown, no explanation.

Example format:
["Python", "Java", "C++", "Go", "Rust", "TypeScript", "JavaScript", "Kotlin"]
"""

    try:
        # Use GROQ for fast skill suggestions
        result = _try_groq(input_text, "improve", prompt)
        if result:
            # Extract JSON array from result
            start = result.find("[")
            end = result.rfind("]")
            if start != -1 and end != -1:
                try:
                    raw = result[start:end+1]
                    suggestions = json.loads(raw)
                    if isinstance(suggestions, list):
                        return jsonify({"suggestions": suggestions[:8], "provider": "groq"})
                except:
                    pass
        
        # Fallback: return empty if GROQ fails
        return jsonify({"suggestions": [], "provider": "none"})
    
    except Exception as e:
        print(f"[Skills] Suggestions failed: {e}")
        return jsonify({"suggestions": [], "provider": "error"}), 500


# ─── University / College Autocomplete ────────────────────────────────────────

@app.route("/api/universities", methods=["GET"])
def universities():
    """Proxy for https://universities.hipolabs.com — no auth needed."""
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"universities": []})
    try:
        encoded = urllib.request.quote(query, safe="")
        url     = f"http://universities.hipolabs.com/search?name={encoded}&limit=8"
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        results = [{"name": u["name"], "country": u.get("country", "")} for u in raw[:8]]
        return jsonify({"universities": results})
    except Exception as exc:
        print(f"[Unis] fetch failed: {exc}")
        return jsonify({"universities": []})


# ─── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db_label = "PostgreSQL" if USE_POSTGRES else "SQLite (local dev fallback)"
    print(f"\n🗄️  Database mode : {db_label}")
    init_db()
    print(f"🚀  Starting Flask on http://localhost:5000\n")
    app.run(debug=True, host="0.0.0.0", port=5000)
