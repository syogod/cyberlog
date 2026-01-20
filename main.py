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
from models import ActivityType, LogEntry, ParentActivity
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
    # ensure parent activities exist (hardcoded)
    parent_names = [
        "Bug Bounty",
        "Reverse Engineering",
        "Malware Analysis",
        "Penetration testing",
    ]
    existing_parents = cast(dict, {p.name: p for p in db.query(ParentActivity).all()})
    for pn in parent_names:
        if pn not in existing_parents:
            p = ParentActivity(name=pn)
            db.add(p)
    db.commit()

    # Add some sensible default child activity types if none exist
    # Ensure the `parent_id` column exists on the activity_types table (add if missing).
    try:
        with engine.connect() as conn:
            col_check = conn.execute(text("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='activity_types' AND COLUMN_NAME='parent_id'"))
            has_col = col_check.scalar() if col_check is not None else 0
            if not has_col:
                # MySQL: add nullable integer parent_id column
                conn.execute(text("ALTER TABLE activity_types ADD COLUMN parent_id INT NULL"))
    except Exception:
        # best-effort: ignore if this fails (e.g., sqlite or insufficient perms)
        pass

    if db.query(ActivityType).count() == 0:
        parents = cast(dict, {p.name: p.id for p in db.query(ParentActivity).all()})
        # list child names with parent name; resolve parent ids below to avoid ambiguous typing
        children = [
            ("Read one vuln writeup", "Bug Bounty"),
            ("Analyze exploit PoC", "Bug Bounty"),
            ("Reverse small malware sample", "Reverse Engineering"),
            ("Read one CVE", "Bug Bounty"),
            ("Practice one lab step", "Penetration testing"),
        ]
        for name, parent_name in children:
            parent_id = parents.get(parent_name)
            db.add(ActivityType(name=name, parent_id=parent_id))
        db.commit()
    db.close()


# ---------- Routes ----------
@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    tz = get_tz_from_request(request)
    streak = get_current_streak(db, tz)
    # Provide parent activities and child activity types for the dropdown on the index page
    parents = db.query(ParentActivity).order_by(ParentActivity.name).all()
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
            "parents": parents,
            # lightweight mapping for JS: list of {id,name,parent_id}
            "activity_map": [
                {"id": a.id, "name": a.name, "parent_id": a.parent_id} for a in activities
            ],
            "recent_logs": recent_logs,
            "user_tz": str(tz),
        }
    )

@app.post("/log")
def log_activity(
    activity_type_id: Optional[int] = Form(None),
    parent_id: Optional[int] = Form(None),
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
                new_at = ActivityType(name=name, parent_id=parent_id)
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

    # Minutes aggregated by parent activity (parents are hardcoded)
    def minutes_by_parent(start_dt, end_dt=None):
        # join parent -> activity -> log, but only include LogEntry rows within the time window
        cond = (LogEntry.activity_type_id == ActivityType.id) & (LogEntry.created_at >= start_dt)
        if end_dt:
            cond = cond & (LogEntry.created_at < end_dt)

        q = (
            db.query(
                ParentActivity.name.label('name'),
                func.coalesce(func.sum(LogEntry.duration_minutes), 0).label('minutes'),
            )
            .join(ActivityType, ActivityType.parent_id == ParentActivity.id)
            .outerjoin(LogEntry, cond)
            .group_by(ParentActivity.id)
        )
        return q.all()

    daily_parent_minutes = minutes_by_parent(day_start, day_end)
    weekly_parent_minutes = minutes_by_parent(week_start, None)
    monthly_parent_minutes = minutes_by_parent(month_start, None)

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

    # Parent-level summaries: total minutes and counts per parent, and top child by minutes
    parent_rows = (
        db.query(
            ParentActivity.id.label('id'),
            ParentActivity.name.label('name'),
            func.coalesce(func.sum(LogEntry.duration_minutes), 0).label('minutes'),
            func.count(LogEntry.id).label('count'),
        )
        .join(ActivityType, ActivityType.parent_id == ParentActivity.id)
        .outerjoin(LogEntry, LogEntry.activity_type_id == ActivityType.id)
        .group_by(ParentActivity.id)
        .all()
    )

    parent_summaries = []
    for pid, pname, pminutes, pcount in parent_rows:
        # find top child activity for this parent by total minutes
        top_child = (
            db.query(ActivityType.name.label('child_name'), func.coalesce(func.sum(LogEntry.duration_minutes), 0).label('minutes'))
            .outerjoin(LogEntry, LogEntry.activity_type_id == ActivityType.id)
            .filter(ActivityType.parent_id == pid)
            .group_by(ActivityType.id)
            .order_by(func.coalesce(func.sum(LogEntry.duration_minutes), 0).desc())
            .limit(1)
            .first()
        )
        if top_child:
            child_name, child_minutes = top_child
        else:
            child_name, child_minutes = None, 0
        parent_summaries.append({
            'id': pid,
            'name': pname,
            'minutes': int(pminutes or 0),
            'count': int(pcount or 0),
            'top_child': child_name,
            'top_child_minutes': int(child_minutes or 0),
        })

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
                "daily_parent_minutes": daily_parent_minutes,
                "weekly_parent_minutes": weekly_parent_minutes,
                "monthly_parent_minutes": monthly_parent_minutes,
            "top_activities": top_activities,
            "today_total_minutes": int(today_total_minutes or 0),
            "today_entry_count": int(today_entry_count or 0),
            "today_notes": today_notes,
            "calendar_days": calendar_days,
            # pass local month_start (date) to template for weekday calculations
            "month_start": month_start_local.date(),
            "user_tz": str(tz),
                "parent_summaries": parent_summaries,
        },
    )


@app.get("/migrate")
def migrate(request: Request, db: Session = Depends(get_db)):
        """
        Simple migration helper: assigns parent_ids for existing ActivityType rows
        and optionally creates ActivityType rows for LogEntry.custom_activity values.
        Uses keyword heuristics to map to one of the four hardcoded parents.
        """
        # ensure parents exist
        seed_activity_types()

        # simple keyword -> parent mapping
        mapping = {
            'Reverse Engineering': ['reverse', 'disassemble', 'deobfus', 'rip'],
            'Malware Analysis': ['malware', 'trojan', 'ransom', 'sample'],
            'Bug Bounty': ['vuln', 'cve', 'exploit', 'poc', 'writeup', 'vulnerability'],
            'Penetration testing': ['practice', 'lab', 'ctf', 'pentest', 'scan', 'exercise'],
        }

        parents = cast(dict, {p.name: p for p in db.query(ParentActivity).all()})

        changed = []
        # Assign parents for activity types without parent
        for at in db.query(ActivityType).filter(ActivityType.parent_id == None).all():
            name = (at.name or '').lower()
            assigned = None
            for pname, keywords in mapping.items():
                for kw in keywords:
                    if kw in name:
                        assigned = parents.get(pname)
                        break
                if assigned:
                    break
            if not assigned:
                assigned = parents.get('Bug Bounty')
            if assigned:
                at.parent_id = assigned.id
                db.add(at)
                changed.append((at.name, assigned.name))
        db.commit()

        # Create ActivityType rows for distinct custom_activity values in LogEntry where activity_type_id is null
        created = []
        custom_names = db.query(func.distinct(LogEntry.custom_activity)).filter(LogEntry.custom_activity != None).all()
        for (cname,) in custom_names:
            if not cname:
                continue
            exists = db.query(ActivityType).filter(ActivityType.name == cname).first()
            if exists:
                continue
            lname = cname.lower()
            assigned = None
            for pname, keywords in mapping.items():
                for kw in keywords:
                    if kw in lname:
                        assigned = parents.get(pname)
                        break
                if assigned:
                    break
            if not assigned:
                assigned = parents.get('Bug Bounty')
            new_at = ActivityType(name=cname, parent_id=assigned.id if assigned else None)
            db.add(new_at)
            db.commit()
            db.refresh(new_at)
            created.append((cname, assigned.name if assigned else None))

        out = "<h3>Migration Results</h3>\n"
        out += f"<p>Assigned parents for {len(changed)} existing activity types.</p>\n"
        if changed:
            out += "<ul>" + "".join(f"<li>{a} -> {p}</li>" for a, p in changed) + "</ul>"
        out += f"<p>Created {len(created)} ActivityType rows from custom activity values.</p>\n"
        if created:
            out += "<ul>" + "".join(f"<li>{a} -> {p}</li>" for a, p in created) + "</ul>"
        out += '<p><a href="/dashboard">Back to dashboard</a></p>'
        return HTMLResponse(out)
