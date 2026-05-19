import os
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, render_template, request, jsonify, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import inspect, text

from models import db, SearchFilter, Lead, AppSettings
from messaging import generate_messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"]                  = os.environ.get("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"]     = os.environ.get("DATABASE_URL", "sqlite:///dealfinder.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

_MT  = ZoneInfo("America/Denver")
_UTC = ZoneInfo("UTC")

@app.template_filter("to_mt")
def to_mt(dt):
    """Convert a naive UTC datetime to Mountain Time for display."""
    if not dt:
        return None
    return dt.replace(tzinfo=_UTC).astimezone(_MT)

@app.template_filter("age_days")
def age_days(dt):
    """Return number of whole days since a naive UTC datetime."""
    if not dt:
        return None
    return (datetime.utcnow() - dt).days

# ── Scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(daemon=True)

def _now_mt():
    """Current time as a naive Mountain Time datetime."""
    return datetime.now(_MT).replace(tzinfo=None)

def _default_next_scan_time():
    """Next occurrence of 8:00 AM MT as a 'YYYY-MM-DDTHH:MM' string."""
    now = _now_mt()
    candidate = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%dT%H:%M")

def run_and_reschedule():
    """Run a scan then save the next scheduled time and re-arm the job."""
    from scanner import run_scan
    run_scan(app)
    with app.app_context():
        mode          = AppSettings.get("scan_repeat_mode", "daily")
        interval_hrs  = int(AppSettings.get("scan_interval_hours", "12"))
        prev_str      = AppSettings.get("next_scan_time", "")
        try:
            prev = datetime.fromisoformat(prev_str)
        except (ValueError, TypeError):
            prev = _now_mt()
        if mode == "daily":
            next_mt = prev + timedelta(days=1)
        else:
            next_mt = _now_mt() + timedelta(hours=max(1, interval_hrs))
        AppSettings.set("next_scan_time", next_mt.strftime("%Y-%m-%dT%H:%M"))
    apply_schedule()

def apply_schedule():
    """Read schedule settings from DB and arm (or disarm) the APScheduler job."""
    with app.app_context():
        enabled = AppSettings.get("scan_enabled", "true") == "true"
        if not enabled:
            try:
                scheduler.remove_job("ksl_scan")
            except Exception:
                pass
            log.info("Scheduled scan disabled")
            return

        next_str = AppSettings.get("next_scan_time")
        if not next_str:
            log.info("No next_scan_time set — schedule not armed")
            return

        mode         = AppSettings.get("scan_repeat_mode", "daily")
        interval_hrs = int(AppSettings.get("scan_interval_hours", "12"))

        try:
            next_mt = datetime.fromisoformat(next_str)
        except ValueError:
            log.warning(f"Invalid next_scan_time value: {next_str!r}")
            return

        # If the stored time is in the past, advance to the next future occurrence
        step = timedelta(days=1) if mode == "daily" else timedelta(hours=max(1, interval_hrs))
        now  = _now_mt()
        while next_mt <= now:
            next_mt += step
        AppSettings.set("next_scan_time", next_mt.strftime("%Y-%m-%dT%H:%M"))

        next_utc = next_mt.replace(tzinfo=_MT).astimezone(timezone.utc)
        scheduler.add_job(
            func=run_and_reschedule,
            trigger=DateTrigger(run_date=next_utc),
            id="ksl_scan",
            replace_existing=True,
        )
        log.info(f"Scan scheduled for {next_mt.strftime('%Y-%m-%d %H:%M')} MT ({mode})")

def start_scheduler():
    if not scheduler.running:
        scheduler.start()
    apply_schedule()
    log.info("Scheduler started")

# ── Default settings ──────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "tier1_pct":           "0",
    "tier2_pct":           "10",
    "scan_enabled":        "true",
    "scan_repeat_mode":    "daily",
    "scan_interval_hours": "12",
}

def ensure_defaults():
    for key, val in DEFAULT_SETTINGS.items():
        if not AppSettings.query.filter_by(key=key).first():
            db.session.add(AppSettings(key=key, value=val))
    db.session.commit()
    if not AppSettings.get("next_scan_time"):
        AppSettings.set("next_scan_time", _default_next_scan_time())


def ensure_columns():
    """Add columns missing from databases created before migrations."""
    try:
        inspector = inspect(db.engine)
        lead_cols   = {c["name"] for c in inspector.get_columns("leads")}
        filter_cols = {c["name"] for c in inspector.get_columns("search_filters")}
        with db.engine.begin() as conn:
            if "title_unknown" not in lead_cols:
                conn.execute(text("ALTER TABLE leads ADD COLUMN title_unknown BOOLEAN DEFAULT 0"))
                log.info("Schema: added title_unknown column to leads")
            if "target_price" not in filter_cols:
                conn.execute(text("ALTER TABLE search_filters ADD COLUMN target_price INTEGER DEFAULT 0"))
                log.info("Schema: added target_price column to search_filters")
    except Exception as e:
        log.warning(f"Schema migration check failed: {e}")


def run_one_time_migrations():
    """Data migrations that run exactly once, tracked by AppSettings flags."""
    # Mark every lead that exists before title filtering as unverified.
    # The flag prevents this from wiping correctly-classified leads on later boots.
    if not AppSettings.get("migration_title_v1"):
        try:
            db.session.execute(text("UPDATE leads SET title_unknown = 1"))
            db.session.commit()
            AppSettings.set("migration_title_v1", "done")
            log.info("Migration title_v1: set title_unknown=1 on all existing leads")
        except Exception as e:
            log.warning(f"Migration title_v1 failed: {e}")

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    leads   = Lead.query.order_by(Lead.found_at.desc()).limit(50).all()
    filters = SearchFilter.query.order_by(SearchFilter.created_at.desc()).all()
    stats = {
        "total":     Lead.query.count(),
        "urgent":    Lead.query.filter_by(deal_tier=1).count(),
        "opportunity": Lead.query.filter_by(deal_tier=2).count(),
        "deal":      Lead.query.filter_by(status="deal").count(),
    }
    next_run = None
    if scheduler.running:
        job = scheduler.get_job("ksl_scan")
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime("%I:%M %p")
    settings = AppSettings.all_as_dict()
    schedule = {
        "enabled":        AppSettings.get("scan_enabled", "true") == "true",
        "repeat_mode":    AppSettings.get("scan_repeat_mode", "daily"),
        "interval_hours": AppSettings.get("scan_interval_hours", "12"),
        "next_scan_time": AppSettings.get("next_scan_time", ""),
    }
    last_scan = None
    _ls_at = AppSettings.get("last_scan_at")
    if _ls_at:
        _dt = datetime.fromisoformat(_ls_at).replace(tzinfo=timezone.utc)
        last_scan = {
            "at":     _dt,
            "total":  int(AppSettings.get("last_scan_total",  0)),
            "urgent": int(AppSettings.get("last_scan_urgent", 0)),
            "opp":    int(AppSettings.get("last_scan_opp",    0)),
        }
    title_unknown_leads = (
        Lead.query
        .filter_by(title_unknown=True)
        .filter(Lead.status != "hidden")
        .order_by(Lead.found_at.desc())
        .all()
    )
    return render_template("index.html", leads=leads, filters=filters,
                           stats=stats, next_run=next_run, settings=settings,
                           last_scan=last_scan, schedule=schedule,
                           title_unknown_leads=title_unknown_leads)


@app.route("/leads")
def leads_page():
    status = request.args.get("status", "")
    tier   = request.args.get("tier", "")
    q = Lead.query.order_by(Lead.found_at.desc())
    q = q.filter(Lead.title_unknown == False)  # only explicitly confirmed clean leads
    if status:
        q = q.filter_by(status=status)
    else:
        q = q.filter(Lead.status != 'hidden')
    if tier:
        q = q.filter_by(deal_tier=int(tier))
    leads = q.all()
    hidden_leads = Lead.query.filter_by(status='hidden').order_by(Lead.found_at.desc()).all()
    return render_template("leads.html", leads=leads, active_status=status, active_tier=tier,
                           hidden_leads=hidden_leads)


@app.route("/leads/export.csv")
def export_leads_csv():
    import csv, io
    from flask import Response
    leads = (Lead.query
             .filter(Lead.title_unknown == False)
             .filter(Lead.status != "hidden")
             .order_by(Lead.found_at.desc())
             .all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Vehicle", "Price", "Tier", "Status", "Location", "Mileage", "Found"])
    for l in leads:
        w.writerow([
            l.title or "",
            l.price or "",
            l.deal_label or "",
            l.status or "",
            l.location or "",
            l.mileage or "",
            l.found_at.strftime("%Y-%m-%d %H:%M") if l.found_at else "",
        ])
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@app.route("/lead/<int:lead_id>")
def lead_detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    return render_template("lead_detail.html", lead=lead)


@app.route("/api/lead/<int:lead_id>/status", methods=["POST"])
def update_lead_status(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    data = request.get_json()
    if "status" in data: lead.status = data["status"]
    if "notes"  in data: lead.notes  = data["notes"]
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/lead/<int:lead_id>/regenerate", methods=["POST"])
def regenerate_message(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    broker_name  = os.environ.get("BROKER_NAME", "the broker")
    broker_phone = os.environ.get("BROKER_PHONE_DISPLAY", "")
    try:
        messages = generate_messages(lead.to_dict(), broker_name, broker_phone)
        lead.ai_message_fb  = messages.get("fb", "")
        lead.ai_message_sms = messages.get("sms", "")
        db.session.commit()
        return jsonify({"ok": True, "fb": lead.ai_message_fb, "sms": lead.ai_message_sms})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scan/now", methods=["POST"])
def scan_now():
    from scanner import run_scan
    try:
        run_scan(app)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.get_json()
    for key in ["tier1_pct", "tier2_pct"]:
        if key in data:
            AppSettings.set(key, data[key])
    return jsonify({"ok": True})


@app.route("/api/schedule", methods=["POST"])
def save_schedule():
    data = request.get_json()
    AppSettings.set("scan_enabled", "true" if data.get("scan_enabled", True) else "false")
    mode = data.get("scan_repeat_mode", "daily")
    if mode in ("daily", "interval"):
        AppSettings.set("scan_repeat_mode", mode)
    interval = data.get("scan_interval_hours")
    if interval is not None:
        AppSettings.set("scan_interval_hours", str(max(1, int(interval))))
    next_time = data.get("next_scan_time")
    if next_time:
        AppSettings.set("next_scan_time", next_time)
    apply_schedule()
    return jsonify({"ok": True, "next_scan_time": AppSettings.get("next_scan_time", "")})


@app.route("/filters", methods=["GET"])
def filters_page():
    filters  = SearchFilter.query.order_by(SearchFilter.created_at.desc()).all()
    settings = AppSettings.all_as_dict()
    return render_template("filters.html", filters=filters, settings=settings)


@app.route("/filters/new", methods=["POST"])
def create_filter():
    f = SearchFilter(
        name      = request.form.get("name", "Untitled"),
        make      = request.form.get("make", ""),
        model     = request.form.get("model", ""),
        year_min  = int(request.form.get("year_min")  or 0),
        year_max  = int(request.form.get("year_max")  or 9999),
        price_min = int(request.form.get("price_min") or 0),
        price_max = int(request.form.get("price_max") or 999999),
        miles_max    = int(request.form.get("miles_max")    or 999999),
        target_price = int(request.form.get("target_price") or 0),
        zip_code     = request.form.get("zip_code", "84101"),
        radius_mi = int(request.form.get("radius_mi") or 100),
        active    = True,
    )
    db.session.add(f)
    db.session.commit()
    return redirect(url_for("filters_page"))


@app.route("/filters/<int:filter_id>/toggle", methods=["POST"])
def toggle_filter(filter_id):
    f = SearchFilter.query.get_or_404(filter_id)
    f.active = not f.active
    db.session.commit()
    return redirect(url_for("filters_page"))


@app.route("/filters/<int:filter_id>/edit", methods=["POST"])
def edit_filter(filter_id):
    f = SearchFilter.query.get_or_404(filter_id)
    f.name         = request.form.get("name", f.name)
    f.make         = request.form.get("make", "")
    f.model        = request.form.get("model", "")
    f.year_min     = int(request.form.get("year_min")     or 0)
    f.year_max     = int(request.form.get("year_max")     or 9999)
    f.price_min    = int(request.form.get("price_min")    or 0)
    f.price_max    = int(request.form.get("price_max")    or 999999)
    f.miles_max    = int(request.form.get("miles_max")    or 999999)
    f.target_price = int(request.form.get("target_price") or 0)
    f.zip_code     = request.form.get("zip_code", f.zip_code)
    f.radius_mi    = int(request.form.get("radius_mi")    or 100)
    db.session.commit()
    return redirect(url_for("filters_page"))


@app.route("/filters/<int:filter_id>/delete", methods=["POST"])
def delete_filter(filter_id):
    f = SearchFilter.query.get_or_404(filter_id)
    db.session.delete(f)
    db.session.commit()
    return redirect(url_for("filters_page"))


# ── Startup ───────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    ensure_defaults()
    ensure_columns()
    run_one_time_migrations()

if __name__ == "__main__":
    start_scheduler()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


