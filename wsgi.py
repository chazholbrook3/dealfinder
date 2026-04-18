"""
wsgi.py — production entry point for gunicorn.
Starts the APScheduler background thread alongside the web server.
"""

from app import app, start_scheduler

start_scheduler()

if __name__ == "__main__":
    app.run()
