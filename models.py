from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class AppSettings(db.Model):
    """Global app settings adjustable from the dashboard."""
    __tablename__ = "app_settings"

    id    = db.Column(db.Integer, primary_key=True)
    key   = db.Column(db.String(80), unique=True, nullable=False)
    value = db.Column(db.String(200), nullable=False)

    @staticmethod
    def get(key, default=None):
        row = AppSettings.query.filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = AppSettings.query.filter_by(key=key).first()
        if row:
            row.value = str(value)
        else:
            db.session.add(AppSettings(key=key, value=str(value)))
        db.session.commit()

    @staticmethod
    def all_as_dict():
        return {r.key: r.value for r in AppSettings.query.all()}


class SearchFilter(db.Model):
    __tablename__ = "search_filters"
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    make       = db.Column(db.String(60), default="")
    model      = db.Column(db.String(60), default="")
    year_min   = db.Column(db.Integer, default=0)
    year_max   = db.Column(db.Integer, default=9999)
    price_min  = db.Column(db.Integer, default=0)
    price_max  = db.Column(db.Integer, default=999999)
    miles_max  = db.Column(db.Integer, default=999999)
    zip_code   = db.Column(db.String(10), default="84101")
    radius_mi  = db.Column(db.Integer, default=100)
    active     = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    leads      = db.relationship("Lead", backref="filter", lazy=True)


class Lead(db.Model):
    __tablename__ = "leads"
    id             = db.Column(db.Integer, primary_key=True)
    filter_id      = db.Column(db.Integer, db.ForeignKey("search_filters.id"))
    ksl_id         = db.Column(db.String(40), unique=True, nullable=False)
    title          = db.Column(db.String(200))
    price          = db.Column(db.Integer, default=0)
    year           = db.Column(db.Integer, default=0)
    make           = db.Column(db.String(60))
    model          = db.Column(db.String(60))
    mileage        = db.Column(db.Integer, default=0)
    location       = db.Column(db.String(120))
    seller_name    = db.Column(db.String(100))
    seller_phone   = db.Column(db.String(30))
    listing_url    = db.Column(db.String(400))
    image_url      = db.Column(db.String(400))
    description    = db.Column(db.Text)
    mmr            = db.Column(db.Integer, default=0)
    mmr_source     = db.Column(db.String(30), default="")
    deal_tier      = db.Column(db.Integer, default=2)
    deal_label     = db.Column(db.String(20), default="")
    pct_vs_mmr     = db.Column(db.Float, default=0.0)
    price_diff     = db.Column(db.Integer, default=0)
    ai_message_fb  = db.Column(db.Text)
    ai_message_sms = db.Column(db.Text)
    status         = db.Column(db.String(30), default="new")
    notes          = db.Column(db.Text, default="")
    sms_sent       = db.Column(db.Boolean, default=False)
    found_at       = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":             self.id,
            "ksl_id":         self.ksl_id,
            "title":          self.title,
            "price":          self.price,
            "year":           self.year,
            "make":           self.make,
            "model":          self.model,
            "mileage":        self.mileage,
            "location":       self.location,
            "seller_name":    self.seller_name,
            "seller_phone":   self.seller_phone,
            "listing_url":    self.listing_url,
            "image_url":      self.image_url,
            "description":    self.description,
            "mmr":            self.mmr,
            "mmr_source":     self.mmr_source,
            "deal_tier":      self.deal_tier,
            "deal_label":     self.deal_label,
            "pct_vs_mmr":     self.pct_vs_mmr,
            "price_diff":     self.price_diff,
            "ai_message_fb":  self.ai_message_fb,
            "ai_message_sms": self.ai_message_sms,
            "status":         self.status,
            "notes":          self.notes,
            "sms_sent":       self.sms_sent,
            "found_at":       self.found_at.strftime("%b %d, %Y %I:%M %p") if self.found_at else "",
        }
