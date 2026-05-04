# BarberPro — Salon Management System

## Default Login
- **Username:** `owner`  |  **Password:** `owner1234`
- ⚠️ Change password after first login!

---

## Deploy to Render (Free)

### Step 1 — Push to GitHub
```bash
cd barberpro
git init
git add .
git commit -m "Initial BarberPro deploy"
git remote add origin https://github.com/YOUR_USERNAME/barberpro.git
git branch -M main
git push -u origin main
```

### Step 2 — Create Web Service on Render
1. Go to render.com → New → Web Service
2. Connect GitHub → select `barberpro` repo
3. Settings:
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2`
   - Instance Type: **Free**
4. Advanced → Add Environment Variable:
   - `SECRET_KEY` = any random string
   - `DB_PATH` = `/data/barberpro.db`
5. Advanced → Add Disk:
   - Name: `barberpro-data`
   - Mount Path: `/data`
   - Size: 1 GB
6. Click Create Web Service ✅

### Updates
```bash
git add . && git commit -m "update" && git push
```
Render auto-deploys on every push.

---

## Local Dev
```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```
