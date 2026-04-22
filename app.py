import os
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, render_template, request, jsonify, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from models import db, SearchFilter, Lead, AppSettings
from messaging import generate_messages

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"]                  = os.environ.get("SECRET_KEY", "dev-secret")
app.config["SQLALCHEMY_DATABASE_URI"]     = os.environ.get("DATABASE_URL", "sqlite:///dealfinder.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# ── Scheduler ─────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler(daemon=True)

def start_scheduler():
    from scanner import run_scan
    interval = int(os.environ.get("SCAN_INTERVAL_MINUTES", 720))
    scheduler.add_job(
        func=lambda: run_scan(app),
        trigger=IntervalTrigger(minutes=interval),
        id="ksl_scan",
        replace_existing=True,
    )
    scheduler.start()
    log.info(f"Scheduler started — every {interval} min")

# ── Default settings ──────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "tier1_pct":  "0",    # at or below MMR = urgent
    "tier2_pct":  "10",   # up to 10% above MMR = opportunity
}

def ensure_defaults():
    for key, val in DEFAULT_SETTINGS.items():
        if not AppSettings.query.filter_by(key=key).first():
            db.session.add(AppSettings(key=key, value=val))
    db.session.commit()

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
    return render_template("index.html", leads=leads, filters=filters,
                           stats=stats, next_run=next_run, settings=settings)


@app.route("/leads")
def leads_page():
    status = request.args.get("status", "")
    tier   = request.args.get("tier", "")
    q = Lead.query.order_by(Lead.found_at.desc())
    if status: q = q.filter_by(status=status)
    if tier:   q = q.filter_by(deal_tier=int(tier))
    leads = q.all()
    return render_template("leads.html", leads=leads, active_status=status, active_tier=tier)


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
        miles_max = int(request.form.get("miles_max") or 999999),
        zip_code  = request.form.get("zip_code", "84101"),
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

if __name__ == "__main__":
    start_scheduler()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

@app.route('/debug-ksl')
def debug_ksl():
    import requests, os, urllib3
    urllib3.disable_warnings()
    host = os.environ.get("BRIGHTDATA_HOST")
    port = os.environ.get("BRIGHTDATA_PORT")
    user = os.environ.get("BRIGHTDATA_USER")
    pwd  = os.environ.get("BRIGHTDATA_PASS")
    proxy_url = f"http://{user}:{pwd}@{host}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    url = "https://classifieds.ksl.com/search/?category=cars-trucks&make=Toyota&model=Camry&yearFrom=2015&yearTo=2023&mileageTo=150000&zip=84101&miles=100"
    resp = requests.get(url, headers=headers, proxies=proxies, timeout=20, verify=False)
    # Return the raw HTML so we can see exactly what the proxy gets
    return resp.text, 200, {"Content-Type": "text/plain"}

