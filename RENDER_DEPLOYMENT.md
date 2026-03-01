# Resume Builder - Backend API Repository

## 📦 What's Included

This is the **Backend-Only** repository for the Resume Builder API built with Flask & Python.

```
📁 resume-builder-backend/
├── app.py              # Flask application & API routes
├── requirements.txt    # Python dependencies
├── .env.example        # Environment configuration template
├── start.bat           # Windows startup script
├── README.md           # Project documentation
└── [database files]    # SQLite database (auto-created)
```

---

## 🚀 Quick Start (Local Development)

### Prerequisites
- Python 3.8+
- pip or conda
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/ashutosh2975/resume-builder-backend.git
cd resume-builder-backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Mac/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Run Locally

```bash
# Using Python
python app.py

# The API will run on http://localhost:5000
```

---

## 🔐 Environment Configuration

Create `.env` file in the backend directory:

```env
# Flask Configuration
FLASK_ENV=development
FLASK_DEBUG=1

# Database (optional - defaults to SQLite)
# DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/resume_builder

# JWT Configuration
JWT_SECRET_KEY=your-secret-key-here

# AI Services API Keys (get from their respective services)
GROQ_API_KEY=your-groq-api-key-here
GEMINI_API_KEY=your-gemini-api-key-here
```

**To generate JWT_SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 📚 API Endpoints

### Authentication
- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - Login user
- `POST /api/auth/logout` - Logout user

### Resumes
- `GET /api/resumes` - Get user's resumes
- `POST /api/resumes` - Create new resume
- `GET /api/resumes/{id}` - Get specific resume
- `PUT /api/resumes/{id}` - Update resume
- `DELETE /api/resumes/{id}` - Delete resume

### AI Enhancements
- `POST /api/enhance` - Enhance resume text with AI
- `POST /api/extract` - Extract data from uploaded resume

### Export
- `POST /api/export/render-html` - Render resume as HTML
- `GET /api/export/{id}/pdf` - Generate PDF
- `GET /api/export/{id}/png` - Generate PNG

### Utilities
- `GET /api/universities?q={query}` - Search universities

---

## 🚀 Deployment on Render

### Step 1: Create Render Account
1. Go to [render.com](https://render.com)
2. Click "Sign Up"
3. Choose "Continue with GitHub"
4. Authorize Render to access your GitHub

---

### Step 2: Deploy Backend Service
1. Click **"New +"** → **"Web Service"**
2. Click **"Connect repository"**
3. Select your GitHub account
4. Find and select: `resume-builder-backend`
5. Click **"Connect"**

---

### Step 3: Configure Deployment

Fill in the following settings:

| Setting | Value |
|---------|-------|
| **Name** | `resume-builder-api` |
| **Environment** | `Python 3` |
| **Region** | Choose closest to you |
| **Branch** | `main` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn app:app` |

---

### Step 4: Add Environment Variables

Click **"Advanced"** → **"Add Environment Variable"**

Add these variables:

| Key | Value |
|-----|-------|
| `FLASK_ENV` | `production` |
| `JWT_SECRET_KEY` | (Generate using: `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `GROQ_API_KEY` | Your Groq API key |
| `GEMINI_API_KEY` | Your Gemini API key |
| `DATABASE_URL` | (Optional: PostgreSQL URL if using) |

**Important:** Never commit `.env` with real keys to GitHub! Use Render's environment variable management.

---

### Step 5: Deploy

1. Click **"Create Web Service"**
2. Render will automatically build and deploy
3. Wait 3-5 minutes for deployment to complete
4. ✅ Your backend will be live at: `https://resume-builder-api.onrender.com`

---

## 📋 Your Backend URL

Once deployed, your API will be available at:
```
https://resume-builder-api.onrender.com
```

Example API call:
```bash
curl https://resume-builder-api.onrender.com/api/universities?q=Stanford
```

---

## 🔧 Production Configuration

### Database
For production, use **PostgreSQL** instead of SQLite:

1. In Render dashboard, add PostgreSQL database
2. Copy the connection URL
3. Add to environment variables as `DATABASE_URL`
4. Restart service

### CORS Configuration
Update frontend URL in `app.py` for production:

```python
CORS(app, resources={
    r"/api/*": {
        "origins": ["https://your-frontend-url.vercel.app"],
        "methods": ["GET", "POST", "PUT", "DELETE"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
```

Then deploy the change to Render.

---

## 📊 Tech Stack

- **Flask** - Web framework
- **Python 3.8+** - Programming language
- **SQLAlchemy** - ORM
- **Flask-JWT-Extended** - JWT authentication
- **Flask-CORS** - CORS handling
- **Groq API** - AI enhancements
- **Google Gemini API** - Alternative AI option
- **Gunicorn** - Production server
- **PostgreSQL** - Production database (optional)

---

## 📝 Dependencies

See `requirements.txt`:
```
Flask==2.x.x
Flask-CORS==4.x.x
Flask-JWT-Extended==4.x.x
SQLAlchemy==2.x.x
python-dotenv==0.x.x
gunicorn==20.x.x
requests==2.x.x
PDF generation libraries
```

---

## 🔍 Monitoring & Logs

In Render dashboard:
1. Click on your service
2. Go to **"Logs"** tab
3. Monitor real-time logs
4. Check for errors and warnings

---

## ⚠️ Common Issues

### Build Fails
- Check `requirements.txt` for missing dependencies
- Verify Python version compatibility
- Check Render build logs

### Service Crashes
- Check environment variables are set
- Verify database connection string
- Check API keys are valid

### CORS Errors
- Update frontend URL in `app.py`
- Redeploy service
- Check browser console

### Database Errors
- For SQLite: Creates `resume_builder.db` automatically
- For PostgreSQL: Ensure `DATABASE_URL` is set
- Run migrations if needed

---

## 📱 Connect with Frontend

Once backend is deployed at Render, connect it with your frontend:

1. Copy backend URL: `https://resume-builder-api.onrender.com`
2. In frontend environment variables:
   ```
   VITE_API_BASE_URL=https://resume-builder-api.onrender.com/api
   ```
3. Redeploy frontend on Vercel
4. ✅ They're now connected!

---

## 🔗 Repository Links

- **Backend Only:** https://github.com/ashutosh2975/resume-builder-backend
- **Frontend Only:** https://github.com/ashutosh2975/resume-builder-frontend
- **Full Stack:** https://github.com/ashutosh2975/Resume_builder_with_ai

---

## 📄 License

This project is open source and available under the MIT License.

---

## 🤝 Contributing

Feel free to fork this repository and submit pull requests!

---

## 📧 Support

For issues and questions:
1. Check existing GitHub issues
2. Create a new issue with detailed description
3. Include error logs and screenshots

---

## ✅ Deployment Checklist

- [ ] GitHub repository created
- [ ] Environment variables generated
- [ ] API keys obtained (Groq, Gemini)
- [ ] Render account created
- [ ] Service deployed successfully
- [ ] Backend URL working (test with `/api/universities?q=test`)
- [ ] Environment variables set in Render
- [ ] Frontend updated with backend URL
- [ ] CORS configured for frontend domain
- [ ] Database configured (SQLite or PostgreSQL)

---

**Backend Ready for Production!** 🚀
