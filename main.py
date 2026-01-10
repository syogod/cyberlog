from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from fastapi import FastAPI, Request, Form, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
import os
from datetime import datetime, timedelta, date
from typing import Optional, cast
from zoneinfo import ZoneInfo
from urllib.parse import unquote

# Timezone configuration: set `LOCAL_TIMEZONE` env var to a valid IANA name (e.g. 'America/Chicago').
# Defaults to UTC if not provided.
LOCAL_TZ_NAME = os.getenv("LOCAL_TIMEZONE", "UTC")
try:
    LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
except Exception:
    LOCAL_TZ = ZoneInfo("UTC")
UTC = ZoneInfo("UTC")

def ensure_aware(dt: datetime) -> datetime:
    if dt is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
def to_local(dt: datetime, tz: ZoneInfo = LOCAL_TZ) -> datetime:
    dt = ensure_aware(dt)
    return dt.astimezone(tz)

def local_to_utc(dt: datetime, tz: ZoneInfo = LOCAL_TZ) -> datetime:
    # dt must be timezone-aware in tz or naive local assumed tz
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)

def get_tz_from_request(request: Request) -> ZoneInfo:
    # Prefer a per-user timezone set by the browser in a cookie (`USER_TZ`).
    try:
        tzname = None
        if request is not None:
            tzname = request.cookies.get('USER_TZ')
        if tzname:
            # Cookie may be percent-encoded (set with encodeURIComponent). Decode first.
            try:
                tzname_decoded = unquote(tzname)
            except Exception:
                tzname_decoded = tzname
            try:
                return ZoneInfo(tzname_decoded)
            except Exception:
                return LOCAL_TZ
    except Exception:
        pass
    return LOCAL_TZ
from database import Base, engine, SessionLocal
from models import ActivityType, LogEntry
from sqlalchemy import func

app = FastAPI()
templates = Jinja2Templates(directory="templates")

def get_current_streak(db: Session, tz: ZoneInfo = LOCAL_TZ):
    """
    Returns the current consecutive-day streak of log entries.
    """
    # Read all entry timestamps in descending order and compute streak in local timezone
    rows = db.query(LogEntry.created_at).order_by(LogEntry.created_at.desc()).all()
    if not rows:
        return 0

    streak = 0
    today_local = datetime.now(tz).date()

    seen_dates = []
    for (created_at,) in rows:
        if created_at is None:
            continue
        dt_local = to_local(created_at, tz)
        d = dt_local.date()
        if not seen_dates or seen_dates[-1] != d:
            seen_dates.append(d)

    for d in seen_dates:
        if d == today_local - timedelta(days=streak):
            streak += 1
        else:
            break

    return streak

# ---------- DB Dependency ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- Startup ----------
@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    seed_activity_types()


def seed_activity_types():
    db = SessionLocal()
    if db.query(ActivityType).count() == 0:
        db.add_all([
            ActivityType(name="Read one vuln writeup"),
            ActivityType(name="Analyze exploit PoC"),
            ActivityType(name="Reverse small malware sample"),
            ActivityType(name="Read one CVE"),
            ActivityType(name="Practice one lab step"),
        ])
        db.commit()
    db.close()


# ---------- Routes ----------
@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    tz = get_tz_from_request(request)
    streak = get_current_streak(db, tz)
    # Provide activity types for the dropdown on the index page
    activities = db.query(ActivityType).order_by(ActivityType.name).all()
    # Last 3 log entries with activity name (if available) and metadata
    recent_q = (
        db.query(LogEntry, ActivityType.name.label("activity_name"))
        .join(ActivityType, LogEntry.activity_type_id == ActivityType.id, isouter=True)
        .order_by(LogEntry.created_at.desc())
        .limit(3)
        .all()
    )
    recent_logs = []
    for entry, activity_name in recent_q:
        label = activity_name if activity_name else (entry.custom_activity or "(custom)")
        recent_logs.append({
            "id": int(entry.id),
            "activity": label,
            "notes": entry.notes,
            "duration_minutes": entry.duration_minutes,
            "created_at": to_local(entry.created_at, tz).strftime('%Y-%m-%d %H:%M'),
        })
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "streak": streak,
            "activities": activities,
            "recent_logs": recent_logs,
            "user_tz": str(tz),
        }
    )

@app.post("/log")
def log_activity(
    activity_type_id: Optional[int] = Form(None),
    custom_activity: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    duration_minutes: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    # If a custom activity string is provided, prefer it and ensure it exists in ActivityType
    if custom_activity:
        name = custom_activity.strip()
        if name:
            existing = db.query(ActivityType).filter(ActivityType.name == name).first()
            if existing:
                activity_type_id = cast(int, existing.id)
            else:
                # create new activity type
                new_at = ActivityType(name=name)
                db.add(new_at)
                db.commit()
                db.refresh(new_at)
                activity_type_id = cast(int, new_at.id)

    entry = LogEntry(
        activity_type_id=activity_type_id,
        custom_activity=custom_activity,   # must match models.py
        notes=notes,
        duration_minutes=duration_minutes,
    )

    db.add(entry)
    db.commit()
    db.refresh(entry)

    return RedirectResponse("/", status_code=303)


@app.post("/log/update")
def update_log_duration(
    entry_id: int = Form(...),
    duration_minutes: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    entry = db.query(LogEntry).filter(LogEntry.id == entry_id).first()
    if not entry:
        return RedirectResponse("/", status_code=303)

    # Update duration if provided (allow clearing by submitting empty -> keep as-is)
    if duration_minutes is not None:
        setattr(entry, "duration_minutes", duration_minutes)
        db.add(entry)
        db.commit()
        db.refresh(entry)

    return RedirectResponse("/", status_code=303)


@app.post("/log/delete")
def delete_log_entry(entry_id: int = Form(...), db: Session = Depends(get_db)):
    entry = db.query(LogEntry).filter(LogEntry.id == entry_id).first()
    if entry:
        db.delete(entry)
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    tz = get_tz_from_request(request)
    stats = (
        db.query(
            ActivityType.name,
            func.count(LogEntry.id).label("count"),
        )
        .join(LogEntry, LogEntry.activity_type_id == ActivityType.id)
        .group_by(ActivityType.id)
        .all()
    )

    # Time window boundaries in user's local timezone (tz), then converted to UTC for DB filtering
    now_local = datetime.now(tz)
    day_start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=tz)
    day_end_local = day_start_local + timedelta(days=1)
    week_start_local = day_start_local - timedelta(days=6)  # last 7 days inclusive
    month_start_local = datetime(now_local.year, now_local.month, 1, tzinfo=tz)
    # compute next month start in local tz
    if now_local.month == 12:
        next_month_start_local = datetime(now_local.year + 1, 1, 1, tzinfo=tz)
    else:
        next_month_start_local = datetime(now_local.year, now_local.month + 1, 1, tzinfo=tz)

    # Convert local boundaries to UTC for querying the DB
    day_start = local_to_utc(day_start_local, tz)
    day_end = local_to_utc(day_end_local, tz)
    week_start = local_to_utc(week_start_local, tz)
    month_start = local_to_utc(month_start_local, tz)
    next_month_start = local_to_utc(next_month_start_local, tz)
    # NOTE: month_start/next_month_start above are UTC datetimes used for DB filtering.

    # Minutes per activity for daily/weekly/monthly
    def minutes_by_activity(start_dt, end_dt=None):
        q = (
            db.query(
                ActivityType.name.label("name"),
                func.coalesce(func.sum(LogEntry.duration_minutes), 0).label("minutes"),
            )
            .join(LogEntry, LogEntry.activity_type_id == ActivityType.id)
            .filter(LogEntry.created_at >= start_dt)
        )
        if end_dt:
            q = q.filter(LogEntry.created_at < end_dt)
        q = q.group_by(ActivityType.id)
        return q.all()

    daily_minutes = minutes_by_activity(day_start, day_end)
    weekly_minutes = minutes_by_activity(week_start, None)
    monthly_minutes = minutes_by_activity(month_start, None)

    # Calendar data for the current month: per-day list of log entries and summary
    month_q = (
        db.query(LogEntry, ActivityType.name.label("activity_name"))
        .join(ActivityType, LogEntry.activity_type_id == ActivityType.id, isouter=True)
        .filter(LogEntry.created_at >= month_start, LogEntry.created_at < next_month_start)
        .all()
    )

    # Group entries by date
    from collections import defaultdict
    day_map = defaultdict(list)
    for entry, activity_name in month_q:
        # convert entry timestamp to user's local date for grouping/visibility
        entry_date = to_local(entry.created_at, tz).date()
        label = activity_name if activity_name else (entry.custom_activity or "(custom)")
        day_map[entry_date].append({
            "id": int(entry.id),
            "activity": label,
            "minutes": entry.duration_minutes or 0,
            "notes": entry.notes,
        })

    # Build calendar days for template
    import calendar as _cal
    # Use the local month start for calendar generation
    _, num_days = _cal.monthrange(month_start_local.year, month_start_local.month)
    calendar_days = []
    for d in range(1, num_days + 1):
        dt = date(month_start_local.year, month_start_local.month, d)
        entries = day_map.get(dt, [])
        calendar_days.append({
            "date": dt,
            "logged": len(entries) > 0,
            "entries": entries,
        })

    # Top activity types by count (overall)
    top_activities = (
        db.query(ActivityType.name, func.count(LogEntry.id).label("count"))
        .join(LogEntry, LogEntry.activity_type_id == ActivityType.id)
        .group_by(ActivityType.id)
        .order_by(func.count(LogEntry.id).desc())
        .limit(5)
        .all()
    )

    # Today's activity summary
    today_total_minutes = (
        db.query(func.coalesce(func.sum(LogEntry.duration_minutes), 0))
        .filter(LogEntry.created_at >= day_start, LogEntry.created_at < day_end)
        .scalar()
    )
    today_entry_count = (
        db.query(func.count(LogEntry.id))
        .filter(LogEntry.created_at >= day_start, LogEntry.created_at < day_end)
        .scalar()
    )
    today_notes = [r[0] for r in db.query(LogEntry.notes).filter(LogEntry.created_at >= day_start, LogEntry.created_at < day_end).all() if r[0]]

    # Include current streak so the template can render it (use user's tz)
    streak = get_current_streak(db, tz)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "stats": stats,
            "streak": streak,
            "daily_minutes": daily_minutes,
            "weekly_minutes": weekly_minutes,
            "monthly_minutes": monthly_minutes,
            "top_activities": top_activities,
            "today_total_minutes": int(today_total_minutes or 0),
            "today_entry_count": int(today_entry_count or 0),
            "today_notes": today_notes,
            "calendar_days": calendar_days,
            # pass local month_start (date) to template for weekday calculations
            "month_start": month_start_local.date(),
            "user_tz": str(tz),
        },
    )
