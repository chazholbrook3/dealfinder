# DealFinder — KSL Car Broker Tool

Automatically scans KSL Classifieds for matching car listings, generates AI-personalized outreach messages, sends SMS alerts to the broker, and tracks all leads in a web dashboard.

---

## Features

- **Auto-scanner** — scrapes KSL Classifieds on a configurable schedule (default every 15 min)
- **Smart deduplication** — never alerts you about the same listing twice
- **Search filters** — filter by make, model, year range, price range, mileage, ZIP code, radius
- **AI outreach** — generates a Facebook DM version and a short SMS version for every listing
- **SMS alerts** — texts the broker immediately when a new matching listing is found (via Twilio)
- **Lead tracker** — full dashboard to track status (New → Contacted → Replied → Deal / Dead)
- **Always-on** — deploys to Railway.app so it runs 24/7 without needing a computer on

---

## Prerequisites (get these before starting)

### 1. Anthropic API key
- Go to [console.anthropic.com](https://console.anthropic.com)
- Create an account → API Keys → Create Key
- Costs ~$0.01–0.03 per message generated

### 2. Twilio (for SMS alerts)
- Go to [twilio.com](https://twilio.com) → sign up free
- Get a free phone number (~$1/month after trial)
- Note your **Account SID**, **Auth Token**, and your Twilio phone number

### 3. Railway (for hosting)
- Go to [railway.app](https://railway.app) → sign up with GitHub
- Free tier gives 500 hours/month — enough for always-on hosting

---

## Local development (optional — test before deploying)

```bash
# 1. Clone/download this folder
cd dealfinder

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up your .env file
cp .env.example .env
# Edit .env with your real API keys and info

# 5. Run the app
python app.py
# Visit http://localhost:5000
```

---

## Deploy to Railway (hosted, always-on)

### Step 1 — Push to GitHub
```bash
git init
git add .
git commit -m "DealFinder initial commit"
# Create a new repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/dealfinder.git
git push -u origin main
```

### Step 2 — Deploy on Railway
1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub repo
2. Select your `dealfinder` repo
3. Railway will auto-detect Python and start building

### Step 3 — Add environment variables
In Railway → your project → Variables, add all of these:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` |
| `TWILIO_ACCOUNT_SID` | `ACxxx...` |
| `TWILIO_AUTH_TOKEN` | your token |
| `TWILIO_FROM_NUMBER` | `+1XXXXXXXXXX` (your Twilio number) |
| `BROKER_PHONE` | `+1XXXXXXXXXX` (broker's real phone) |
| `BROKER_NAME` | `Mike Carter` |
| `BROKER_PHONE_DISPLAY` | `(435) 555-0199` |
| `SECRET_KEY` | any random string |
| `SCAN_INTERVAL_MINUTES` | `15` |

### Step 4 — Get your public URL
Railway assigns a URL like `https://dealfinder-production.up.railway.app`. Visit it and you'll see the dashboard.

---

## Using the app

### Add a search filter
1. Go to **Searches** in the nav
2. Fill in the filter form: make, model, year range, price range, max miles, ZIP code
3. Click **Add filter** — the scanner will use it on the next run

### Trigger a scan manually
On the Dashboard, click **Scan now** to immediately scan all active filters.

### Review leads
When the scanner finds a new listing:
- You'll receive an SMS text with the car name and KSL link
- It appears in the Leads dashboard with a generated Facebook DM + SMS message ready to copy
- Click **View** on any lead to see the full listing, copy messages, and update status

### Track your outreach
On any lead's detail page:
- Change **Status** to Contacted, Replied, Deal, or Dead
- Add **Notes** (negotiation details, client notes, etc.)
- Click **Regenerate** if you want a fresh AI message

---

## File structure

```
dealfinder/
├── app.py              # Flask app, routes, scheduler setup
├── models.py           # SQLAlchemy models (SearchFilter, Lead)
├── scraper.py          # KSL Classifieds scraper
├── scanner.py          # Background scan job
├── messaging.py        # Anthropic AI + Twilio SMS
├── wsgi.py             # Gunicorn entry point
├── requirements.txt
├── Procfile
├── railway.toml
├── .env.example        # Copy to .env and fill in
├── .gitignore
└── templates/
    ├── base.html
    ├── index.html
    ├── leads.html
    ├── lead_detail.html
    └── filters.html
```

---

## Troubleshooting

**No listings found** — KSL occasionally changes its HTML structure. Check the Railway logs → if you see "Found 0 listings", the scraper selectors may need updating. Open an issue or inspect KSL's source and update `scraper.py`.

**SMS not sending** — Double-check TWILIO_FROM_NUMBER starts with `+1` and BROKER_PHONE starts with `+1`. Check Twilio console for error logs.

**App crashes on Railway** — Check the Railway deployment logs. Common causes: missing environment variables, or a dependency install failure.

---

## Upgrading

Future ideas:
- Export leads to CSV
- Multiple broker users / login
- Per-lead client assignment
- Auto-follow-up reminders
- Craigslist or Facebook Marketplace integration (harder due to auth)
