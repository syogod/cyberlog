# Cyberlog

Cyberlog is a lightweight FastAPI web application to record small security learning "reps" (reading CVEs, practicing lab steps, etc.), track minutes spent per activity, view streaks, and visualize monthly consistency with a calendar heatmap.

---

**Features**
- Log an activity (select from seeded activity types or create a custom activity)
- Optional duration in minutes and notes per entry
- Dashboard with:
  - Current streak (consecutive days with activity)
  - Minutes-per-activity summaries (daily, weekly, monthly)
  - Top activity types
  - Monthly calendar showing days with/without logs
  - Small charts powered by Chart.js (client-side)
- Simple CRUD for recent log entries (update duration / delete)
- Per-user timezone rendering via a browser-set `USER_TZ` cookie

---

**Tech stack**
- Python 3.10+ (any 3.10+/3.11 recommended)
- FastAPI for the web framework
- Jinja2 for server-rendered templates
- SQLAlchemy ORM for persistence
- MySQL-compatible database (uses `pymysql` driver)
- Chart.js (served from CDN in templates)

---

Quick start (local development)

1. Clone the repo and change into the project directory:

```powershell
cd S:\Development\cyberlog
```

2. Create and activate a Python virtual environment and install requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Set required environment variables (PowerShell example):

```powershell
$env:DB_USER = 'cyberlog'
$env:DB_PASSWORD = ''
$env:DB_HOST = '127.0.0.1'
$env:DB_PORT = '3306'
$env:DB_NAME = 'cyberlog'
# Optional: define LOCAL_TIMEZONE e.g. 'America/Chicago'
$env:LOCAL_TIMEZONE = 'UTC'
```

4. Ensure the database exists (see SQL below), then run the app:

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` in your browser to use the log page and `http://127.0.0.1:8000/dashboard` for the dashboard.

Notes:
- On first run the app will call `Base.metadata.create_all(bind=engine)` and also run `seed_activity_types()` to create default `ActivityType` rows if empty.
- The app expects timestamps stored in the DB to be UTC or timezone-aware. The server converts to the user's timezone (from `USER_TZ` cookie) for display and aggregation.

---

Database setup (MySQL)

Run these SQL statements on your MySQL server to create the database and tables expected by the app. Adjust character set / collations as needed for your environment.

```sql
-- Create database
CREATE DATABASE IF NOT EXISTS cyberlog
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE cyberlog;

-- Activity types table
CREATE TABLE IF NOT EXISTS activity_types (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE,
  created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET = utf8mb4;

-- Log entries table
CREATE TABLE IF NOT EXISTS log_entry (
  id INT AUTO_INCREMENT PRIMARY KEY,
  activity_type_id INT NULL,
  custom_activity VARCHAR(255) NULL,
  notes TEXT NULL,
  duration_minutes INT NULL,
  created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_activity_type FOREIGN KEY (activity_type_id) REFERENCES activity_types(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET = utf8mb4;
```

If you prefer the app to create tables automatically, ensure the DB user has `CREATE` privileges and start the app — it will call `Base.metadata.create_all(bind=engine)` at startup.

---

Docker

A `Dockerfile` is included for container builds and `deploy.ps1` automates tagging/pushing. Typical commands (PowerShell):

```powershell
# build image
docker build -t cyberlog:latest .
# run container (example)
docker run -e DB_HOST=mydbhost -p 8000:8000 cyberlog:latest
```

---

Configuration / Environment variables

- `DB_USER` (required)
- `DB_PASSWORD` (required -- may be empty locally)
- `DB_HOST` (required)
- `DB_PORT` (optional, default `3306`)
- `DB_NAME` (required)
- `LOCAL_TIMEZONE` (optional, IANA tz string used as fallback; defaults to `UTC`)

---

Notes, caveats, and extension ideas

- The repository uses `Base.metadata.create_all(...)` and does not include a migration tool (Alembic). For production schema changes, add migrations to avoid data loss.
- The calendar uses a browser `title` attribute for entry tooltips. You can replace this with richer popovers if desired.
- Activity type creation is basic and uses exact string matching; you may want to add normalization (case-insensitive) before creating new `ActivityType` rows.
- Timestamps/UTC: the app treats DB-stored timestamps as UTC (or timezone-aware). If your DB stores local times, consider converting to UTC at insert time.

---

Inspiration

This project is inspired by the idea of "micro-practice" and habit-forming through short, daily intentional learning activities. It focuses on small, measurable reps (minutes per task) and visual feedback (streaks, calendar heatmap) to encourage regular security learning.

---

Questions or contributions

If you'd like help adding features (search, richer tooltips, CSV export, user accounts, migrations), tell me what you'd like next and I can implement it.
