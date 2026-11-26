#!/usr/bin/env python3
"""
RemotIQ Partners – Expense Submission App  (PWA edition) – v2
==============================================================
v2: Purpose / Notes is a dropdown; the Account on every line is looked up
    from it (chart of accounts mapping), TIN required on every line,
    email notification to the filer on success and on failure.

EMAIL SETUP (Railway → Variables)
  RESEND_API_KEY  API key from resend.com (Railway Hobby blocks SMTP, so an
                  HTTPS email API is used)
  EMAIL_FROM      e.g. RemotIQ Expenses <expenses@remotiqpartners.com>
                  (domain must be verified in Resend)
  FINANCE_EMAIL   optional: only used to alert Finance when the app loses its Xero
                  connection. Claim emails go to the filer only.
  Optional SMTP fallback (Railway Pro plan only): SMTP_HOST, SMTP_PORT,
  SMTP_USER, SMTP_PASS. If neither is set, emails are skipped and logged.
Cross-platform: works as an installable app on iOS, Android, Mac, and Windows.

ACCOUNTS SUPPORTED
  420 – Meals & Entertainment      (Meetings: Clients & Partners)
  421 – Engagements & Trainings   (Meetings: Staff & Internal, Team purposes)
  489 – Telephone & Internet      (Official Business: Comms)
  493 – Travel & Transportation   (all Travel purposes)

PURPOSE → ACCOUNT MAPPING
  The Purpose / Notes dropdown (see PURPOSES below) sets the Account on every
  expense line. Purposes with an account code lock the Account and the server
  enforces it. Purposes with code None let the employee pick the Account.

QUICK START (local dev / testing)
  1. pip install flask requests
  2. Edit XERO_CLIENT_ID and XERO_CLIENT_SECRET below
  3. python expense_app.py
  4. Visit http://localhost:5000/auth once to authorise Xero

DEPLOY FOR MOBILE ACCESS (required for iOS/Android install)
  The app must run on HTTPS for PWA install to work on phones.
  Easiest free options:
    • Railway  → railway.app  (connect GitHub, click Deploy)
    • Render   → render.com   (new Web Service, free tier)
    • Fly.io   → fly.io       (fly launch, fly deploy)

  Set XERO_CLIENT_ID and XERO_CLIENT_SECRET as environment variables on
  the hosting platform, and update XERO_REDIRECT_URI to your HTTPS URL.

INSTALL ON DEVICES (once deployed to HTTPS)
  iOS/Safari    → Share → "Add to Home Screen"
  Android/Chrome→ Menu → "Add to Home Screen" (or install prompt appears)
  Mac/Windows   → Chrome/Edge address bar → install icon (⊕)
"""

import os, json, secrets, smtplib, logging
from html import escape
from email.message import EmailMessage
from datetime import datetime, timedelta

import requests
from flask import Flask, request, redirect, session, render_template_string, url_for, Response, jsonify

# ─── Configuration ─────────────────────────────────────────────────────────────
XERO_CLIENT_ID     = os.environ.get("XERO_CLIENT_ID",     "YOUR_CLIENT_ID_HERE")
XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "YOUR_CLIENT_SECRET_HERE")
XERO_REDIRECT_URI  = os.environ.get("XERO_REDIRECT_URI",  "http://localhost:5000/callback")
XERO_SCOPES        = "openid profile email accounting.contacts accounting.invoices accounting.attachments offline_access"
# Xero sign-in is saved on the Railway volume (if attached) so it survives deploys.
# Railway sets RAILWAY_VOLUME_MOUNT_PATH automatically when a volume is mounted.
TOKEN_FILE         = os.environ.get("TOKEN_FILE") or os.path.join(
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "."), "xero_tokens.json")

# Only this Xero organisation is used (matched by name, case-insensitive)
XERO_TENANT_NAME = os.environ.get("XERO_TENANT_NAME", "RemotIQ Partners Inc.")

# Email notifications
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "RemotIQ Expenses <onboarding@resend.dev>")
FINANCE_EMAIL  = [e.strip() for e in os.environ.get("FINANCE_EMAIL", "").split(",") if e.strip()]
SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASS      = os.environ.get("SMTP_PASS", "")
log = logging.getLogger("expense_app")
PORT               = int(os.environ.get("PORT", 5000))

# ─── Expense purposes → chart of accounts ─────────────────────────────────────
# (label, account code or None). None = employee chooses the account manually.
PURPOSES = [
    ("Travel: Airfare (Flights, baggage, airport taxes)",                                   "493"),
    ("Travel: Ground Transport (Rental cars, taxi, rideshare, fuel, parking, tolls, mileage)", "493"),
    ("Travel: Lodging (Accommodation)",                                                     "493"),
    ("Travel: Meals & Entertainment (Business travel meals)",                               "493"),
    ("Travel: Other Costs (Train, ferry, visas, travel insurance)",                         "493"),
    ("Meetings: Staff & Internal (Team meetings, internal alignment)",                      "421"),
    ("Meetings: Clients & Partners (Business lunches, coffee, prospective clients)",        "420"),
    ("Team: Bonding & Wellness (Team building, fitness, mental health programs)",           "421"),
    ("Team: Employee Recognition (Birthdays, rewards, appreciation events)",                "421"),
    ("Remote Work: Comms & Utilities (Home WiFi, personal phone bills for work)",           None),
    ("Professional Development (Workshops, seminars, training, certifications)",            None),
    ("Ops: Professional Fees & Admin (Legal docs, printing, ID production)",                None),
    ("Ops: Courier & Shipping (LBC, DHL, FedEx, postage)",                                  None),
    ("Ops: Banking & Transaction Fees (Bank charges, payment processing)",                  None),
    ("Official Business (OB): Ops & Procurement (Transport, parking, and per diem/overtime during procurement)", None),
    ("Official Business (OB): Comms (Calls or mobile data used during official business)", "489"),
    ("Liquidation",                                                                         None),
    ("Reimbursement",                                                                       None),
    ("Cash Advance",                                                                        None),
    ("Others",                                                                              None),
]
PURPOSE_ACCOUNT = dict(PURPOSES)

# ─── Logo ──────────────────────────────────────────────────────────────────────
# The RemotIQ Partners logo is embedded below (LOGO_PNG_B64), so no extra file
# is needed. To change it later without editing code, upload a file named
# logo.png (or logo.svg / logo.jpg / logo.webp) next to expense_app.py on
# GitHub; that file takes priority over the embedded logo.
_HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_TYPES = {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg",
              "jpeg": "image/jpeg", "webp": "image/webp"}

LOGO_PNG_B64 = (
    "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"
    "XFA1pVSSpF9++umaK69SZDk1LS0e14oKCzMyM3v3OSE9I8NsMgXKyrZv3bZ1yxZCiMPptHPbyy+94HQ6Hnrs0eTXlBLbe47rHX/N"
    "Oad6Fal30Fi1XCjl9WIRVO7eFBObH1MKFm633D17gsGQy+3SYlGL1er2uBPjTPhtMMJTrpv85vz5GRlNOGMF+QXdevS8/qYbzho+"
    "PC0jPXmIb9qw8a358997661YlHpTfJFQ5MrLx7778Ydnnn1W9WxkNpvXrV0rSZLT5SouLD5rxDmTb7qx74kDbDabuGZvbu47C958"
    "ftYsziiRZJ87ZeYTT778/Av+0oDH6w0FQ4piuv2uaZeNHdO2fTtJkgFAVdW1q1fPeOzxHxcvcThddodzT/aeRx6cPmf+64du68Ta"
    "umXzlssuvKi0tNTr8+lUD5SW9ujVc8I11wwddmaz5s0Tx0J7c/f+uGTJ/Lmvr/nnb5fb6/X6SkpLL79o1KLvvunRq6cgBtGAp59x"
    "htfjM1vMhOAtm7es+We1yWyKx2MtW7YcePLJuq4nz2eEgOpUCAKh2owD8Y1Lv19cFgimpKYkEipF3PZNt95iNpuprteEPIShzDm/"
    "7Y47vv/mu4NNNMQ5+u6bb8+/+KJjFsfv9/tj0bjZatE0zWY3K2aTeF9JktauWXP5RaPC4bDX56NULykuad223djx44YNP6tNu3Z2"
    "uw04CkfCO3fsXPLtd+++9WbWjp0pqSllZaGrLr/C6/NZbVZaYc0cPoiq3D95/cRr/vnrrybpTXVdL8gv8KX4+g0Y0LZdW6fLJUtS"
    "KBgeMPBEABh29vDMzKYWixkTafOmTWtXrTJbLLFYvFXrVieeNFDT9Er7PMaZx+upclnnjFltjs0bN636+5+U1FRd08rK/KMvv+yq"
    "a6/u2auXOPADgHAotGbV6rlz5nzx2ed2hx0ApaakzXz8iaHDzuzdp8+hWy4xzr9Y+PnV468ymUxeny/gL7Ha7FNuv23MuLGdunRJ"
    "vn5/3r6vv/py9gsv7ti23eNNMZlNLz3/rK7rz7z0wuECGTjnVNetNuuyn37+9quvS4qL0zPS/aXFlPI2bdt07tqlSWam2WyhlFqs"
    "VovVIg6N1Hh8+n33AQezxRqNxiSJTH/80dGXX9Y0KW4gGo3+/MOPjzw4ffvWrTa7PT0l/eUXXjx/1MW9evc+8JpCeZ4zVjLsnMLM"
    "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"
    "ozGny9m6Teujx0biTEtRlEsuvzQSDiMEDrv9z2W/X3bhxX/+/kc0Ek1OpDr2e9BuPbq/MPuVWDQSj0Vdbm9Ozp5Lzj3/1hun/L1i"
    "paZqCS15QGhvbu7cV+YMGzzkkw8/8nh9AHjfvryrrp4k0tTqv7lEFQIWU267LR6NUqrb7I6Vf64Yccawzz7+OBAIJB5GDODs3buf"
    "n/XMZRdcHAlHFMWkxmMej2fcxAlCavIg4Z/ySDBCTOedqy7+od4NzQEAWS2hO+4mrVvJPXoApY0ozYgQ4Fw+/TTSu7e+fgOyWmsR"
    "BYcwj0SV4Wcji6WhXio5WK6Gg7JLt65XT75h1owZqalpHp/vkw8/HD3m8qFnnpkYZ2JTyTm8PHeuLJvemj/P5fakpKbu25t31+1T"
    "Z82c0alT55atWpnMprKysp3bd2zdsiUcCnk8HmKRS0tLbDbrvLffPufckQ0ycI+Bm87r8374+cIJY6745aeffT6f1+uLxWNvzH3t"
    "nQXzfSmpDqcTAYRCwaLCQjWu2R1OX0qqpqpFhUUDB5204L13qhE1EOeuV1x11ftvv7NuzVqPz+dwun5btmzFiHNbtGqZmpoqyRIA"
    "olR3OBwvvz7X6/XW2UISTsVTBp/63c8/3HHzLd9/853ZYrZabcKDKgr5VHloXx5niVAkHEEYmczmROAG1XWny7Vrx45RI8/7+Msv"
    "2rRtcxzdrWKH1O/EAe988vHEsVeUFBe7PT5fampOds79d9399JMzmzVv5vP5EELFxcV7c3NLikusNmtaenpJcbEYk18sXLhh3fOW"
    "2ggZ1MEbduXESe+//d7mDRs9Xo/D5fz1p1/+/P33lq1a+VJSZEkChKiuu1yul+fNFXmax4yNLrxkFKX0lsk3+ktL3B6vrtM3Xpv7"
    "0bvvtOvQoV379i63W9O0nOycLZs35+/bb7VaUlNTw5FIWVngmuuvn/Xi8w3Y9aKhRp5/7s233z7rqRlpqeluj2fXzp2Txl3Ztk3b"
    "Lt26+lJTOOfBsuCePXt27thRVFDocDhtdlssFgsEAi+9Oqdd+/aJ5UU6VK9aOetM0rIFKywEWamHEYqAcSAyxCJlE69zf/IBadWS"
    "6zqSGoswHWcMyYrttpsD4ybUzsdGKXK7TBed32CZrZzrmlb+UzPzUeht3Hrn7Ys+W5iTnS2E3e6fdteAXwfa7LZEogZCCIArivLK"
    "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"
)

def find_logo():
    for ext, mime in LOGO_TYPES.items():
        path = os.path.join(_HERE, f"logo.{ext}")
        if os.path.exists(path):
            return path, mime
    return None, None

# ─── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ─── PWA Manifest ──────────────────────────────────────────────────────────────
MANIFEST = {
    "name": "RemotIQ Expense Submission",
    "short_name": "Expenses",
    "description": "Submit employee expenses — forwarded to Xero as Draft Bills.",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#f0f4f8",
    "theme_color": "#c8102e",
    "orientation": "portrait-primary",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
    ]
}

# ─── SVG App Icon ──────────────────────────────────────────────────────────────
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="36" fill="#c8102e"/>
  <rect x="52" y="36" width="88" height="120" rx="10" fill="none" stroke="#fff" stroke-width="8"/>
  <rect x="72" y="26" width="48" height="24" rx="6" fill="#c8102e" stroke="#fff" stroke-width="8"/>
  <line x1="68" y1="90"  x2="124" y2="90"  stroke="#fff" stroke-width="7" stroke-linecap="round"/>
  <line x1="68" y1="112" x2="104" y2="112" stroke="#fff" stroke-width="7" stroke-linecap="round"/>
</svg>"""

# ─── Service Worker ─────────────────────────────────────────────────────────────
SERVICE_WORKER = """
// RemotIQ Expense App – Service Worker
const CACHE = 'remotiq-expense-v1';

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(['/']))
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  return self.clients.claim();
});

// Network-first: always try live, fall back to cache for the shell
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(r => {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
"""

# ─── HTML Template ─────────────────────────────────────────────────────────────
TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Expenses">
<meta name="theme-color" content="#c8102e">
<meta name="description" content="RemotIQ Partners expense submission">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon.svg">
<title>Expense Submission – RemotIQ Partners</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --brand:   #c8102e;
    --brand-d: #8e0b20;
    --go:      #1e8e3e;
    --go-d:    #146c2e;
    --blue:    #2b6cb0;
    --blue-d:  #1f4f86;
    --bg:      #f0f4f8;
    --card:    #ffffff;
    --border:  #e2e8f0;
    --muted:   #718096;
    --text:    #2d3748;
    --safe-top: env(safe-area-inset-top);
    --safe-bot: env(safe-area-inset-bottom);
  }

  html { -webkit-text-size-adjust: 100%; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif;
         background: var(--bg); color: var(--text); min-height: 100dvh; }

  /* ── Top bar ── */
  .topbar {
    background: linear-gradient(135deg, var(--brand) 0%, var(--brand-d) 100%);
    color: #fff;
    padding: calc(var(--safe-top) + 12px) 20px 14px;
    display: flex; align-items: center; gap: 10px;
    position: sticky; top: 0; z-index: 100;
    box-shadow: 0 2px 10px rgba(0,0,0,.3);
  }
  .topbar h1  { font-size: 16px; font-weight: 700; }
  .logo-chip { background: #fff; border-radius: 8px; padding: 5px 10px; display: inline-flex;
               align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,.2); }
  .logo-chip img { height: 30px; width: auto; display: block; }
  @media (min-width: 700px) { .logo-chip img { height: 40px; } .logo-chip { padding: 6px 14px; } }
  .topbar .sub { font-size: 12px; opacity: .65; margin-left: auto; }

  /* ── Install banner ── */
  #install-banner {
    display: none; background: #c8102e; color: #fff;
    padding: 10px 16px; text-align: center; font-size: 13px;
    align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap;
  }
  #install-banner button {
    background: #fff; color: #c8102e; border: none; border-radius: 20px;
    padding: 5px 14px; font-size: 12px; font-weight: 700; cursor: pointer; }
  #install-banner .dismiss { background: transparent; color: rgba(255,255,255,.6);
    font-size: 12px; text-decoration: underline; }

  /* ── Page ── */
  .page { max-width: 1100px; margin: 0 auto; padding: 16px 14px calc(var(--safe-bot) + 40px); }

  /* ── Card ── */
  .card { background: var(--card); border-radius: 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 4px 14px rgba(0,0,0,.04);
          padding: 20px 18px; margin-bottom: 14px; }
  .card-title { font-size: 10px; font-weight: 800; text-transform: uppercase;
                letter-spacing: .1em; color: var(--brand); margin-bottom: 16px;
                padding-bottom: 10px; border-bottom: 1px solid var(--border); }

  /* ── Fields ── */
  .field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 14px; }
  .field:last-child { margin-bottom: 0; }
  label { font-size: 11px; font-weight: 700; color: var(--muted);
          text-transform: uppercase; letter-spacing: .05em; }
  input[type=text], input[type=email], input[type=date], input[type=search],
  input[type=number], select {
    border: 1.5px solid var(--border); border-radius: 8px;
    padding: 12px 14px; font-size: 16px; /* 16px prevents iOS zoom */
    color: var(--text); background: #fafbfc; outline: none; width: 100%;
    -webkit-appearance: none; appearance: none;
    transition: border-color .15s, box-shadow .15s; }
  input:focus, select:focus {
    border-color: var(--brand); background: #fff;
    box-shadow: 0 0 0 3px rgba(200,16,46,.12); }
  input::placeholder { color: #b0bec5; }
  select { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23718096' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
           background-repeat: no-repeat; background-position: right 14px center;
           padding-right: 38px; }

  select.locked { pointer-events: none; background-color: #edf2f7 !important; color: var(--muted); }

  .combo { position: relative; }
  .combo-list { position: absolute; z-index: 50; left: 0; right: 0; top: calc(100% + 4px);
    background: #fff; border: 1.5px solid var(--border); border-radius: 8px;
    box-shadow: 0 8px 24px rgba(0,0,0,.12); max-height: 280px; overflow-y: auto; }
  .combo-item { padding: 10px 14px; cursor: pointer; font-size: 15px; display: flex;
    justify-content: space-between; gap: 10px; }
  .combo-item small { color: var(--muted); font-size: 12px; }
  .combo-item.active, .combo-item:hover { background: #ebf8ff; }
  .combo-item.new { color: #2b6cb0; font-weight: 600; border-top: 1px solid var(--border); }
  .combo-empty { padding: 10px 14px; color: var(--muted); font-size: 13px; }
  input[type=search]::-webkit-search-cancel-button { cursor: pointer; }

  .bank-box { border: 1.5px solid #bee3f8; background: #f5fbff; border-radius: 10px;
              padding: 14px 16px 4px; margin: 4px 0 14px; }
  .bank-title { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em;
                color: #2b6cb0; margin-bottom: 6px; }
  .bank-note { font-size: 13px; color: #4a5568; margin-bottom: 12px; line-height: 1.45; }

  /* ── Notice ── */
  .notice { background: #ebf8ff; border-left: 3px solid #3182ce; border-radius: 6px;
            padding: 11px 14px; font-size: 13px; color: #2b6cb0; margin-bottom: 18px;
            line-height: 1.5; }

  /* ── Expense cards (mobile-first: one card per line) ── */
  .exp-card { background: #fafbfc; border: 1.5px solid var(--border);
              border-radius: 10px; padding: 14px; margin-bottom: 10px; position: relative; }
  .exp-card .row-num { font-size: 10px; font-weight: 800; text-transform: uppercase;
                       letter-spacing: .08em; color: var(--muted); margin-bottom: 10px; }
  .exp-card .btn-del { position: absolute; top: 10px; right: 10px;
                       background: none; border: none; cursor: pointer;
                       color: #cbd5e0; font-size: 22px; line-height: 1; padding: 4px 8px;
                       border-radius: 6px; transition: all .15s; }
  .exp-card .btn-del:hover { color: #e53e3e; background: #fff5f5; }
  .exp-card .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }

  /* ── Total bar ── */
  .total-bar { background: linear-gradient(135deg, var(--blue) 0%, var(--blue-d) 100%);
               border-radius: 10px; padding: 16px 20px;
               display: flex; justify-content: space-between; align-items: center;
               margin-bottom: 16px; }
  .total-bar .lbl { color: rgba(255,255,255,.7); font-size: 12px; font-weight: 700;
                    text-transform: uppercase; letter-spacing: .08em; }
  .total-bar .amt { color: #fff; font-size: 24px; font-weight: 800; }

  /* ── Buttons ── */
  .btn-add { width: 100%; background: none; border: 1.5px dashed #b0bec5; color: var(--muted);
             padding: 13px; border-radius: 10px; cursor: pointer; font-size: 14px;
             font-weight: 600; display: flex; align-items: center; justify-content: center;
             gap: 8px; transition: all .15s; -webkit-tap-highlight-color: transparent; }
  .btn-add:hover, .btn-add:active { border-color: var(--brand); color: var(--brand); background: #f0f4f8; }
  .btn-submit { width: 100%; background: linear-gradient(135deg, var(--go), var(--go-d));
                color: #fff; border: none; padding: 16px;
                border-radius: 12px; font-size: 16px; font-weight: 700;
                cursor: pointer; letter-spacing: .01em;
                -webkit-tap-highlight-color: transparent;
                transition: opacity .15s, transform .1s; }
  .btn-submit:active { opacity: .85; transform: scale(.98); }
  .btn-submit:disabled { opacity: .45; cursor: not-allowed; transform: none; }

  /* ── Status screens ── */
  .status-page { text-align: center; padding: 50px 16px; }
  .status-icon { font-size: 60px; margin-bottom: 18px; line-height: 1; }
  .status-page h2 { font-size: 22px; font-weight: 800; margin-bottom: 10px; }
  .status-page p  { font-size: 15px; color: var(--muted); line-height: 1.65;
                    max-width: 340px; margin: 0 auto 28px; }
  .btn-back { display: inline-block; background: var(--brand); color: #fff;
              text-decoration: none; padding: 14px 32px; border-radius: 12px;
              font-weight: 700; font-size: 15px; }
  .success h2 { color: #276749; }
  .error   h2 { color: #c53030; }

  /* ── Desktop table (≥ 700px) ── */
  @media (min-width: 700px) {
    .page { padding: 24px 20px 60px; }
    .card { padding: 28px 32px; }
    .exp-card { display: none !important; }
    .tscroll { display: block; overflow-x: auto;
               border-radius: 8px; border: 1px solid var(--border); }
    table { width: 100%; min-width: 900px; border-collapse: collapse; table-layout: fixed; }
    thead { background: var(--blue); }
    th { font-size: 11px; font-weight: 700; text-transform: uppercase;
         letter-spacing: .06em; color: #fff; padding: 11px 12px;
         text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
    tbody tr { border-bottom: 1px solid #f0f4f8; }
    tbody tr:last-child { border-bottom: none; }
    td { padding: 6px 6px; vertical-align: middle; }
    td input, td input[type], td select { background: transparent; border: 1px solid transparent;
      border-radius: 5px; padding: 7px 9px; font-size: 13px; width: 100%; }
    td input:hover, td select:hover { border-color: var(--border); background: #fff; }
    td input:focus, td select:focus { border-color: var(--brand); background: #fff;
      box-shadow: 0 0 0 2px rgba(200,16,46,.08); outline: none; }
    .totals-bar { background: linear-gradient(135deg, var(--blue), var(--blue-d)); border-top: none;
                  padding: 14px 20px; display: flex; justify-content: flex-end;
                  align-items: center; gap: 14px; }
    .totals-label  { font-size: 12px; font-weight: 700; color: rgba(255,255,255,.8);
                     text-transform: uppercase; letter-spacing: .06em; }
    .totals-amount { font-size: 22px; font-weight: 800; color: #fff; }
    .total-bar { display: none; }
    .btn-submit { width: auto; padding: 13px 44px; font-size: 15px; border-radius: 8px; }
    .actions { display: flex; justify-content: flex-end; }
    #mobile-lines { display: none !important; }
    #desktop-table { display: block !important; }
    #mobile-total-section { display: none !important; }
    .btn-add { width: auto; padding: 9px 18px; border-radius: 6px; font-size: 13px; margin-top: 14px; }
  }

  /* Mobile: hide desktop table */
  @media (max-width: 699px) {
    .tscroll, #desktop-table { display: none !important; }
  }
</style>
</head>
<body>

<div id="install-banner">
  <span>📱 Install this app on your device for quick access</span>
  <button onclick="installApp()">Install</button>
  <button class="dismiss" onclick="dismissInstall()">Not now</button>
</div>

<div class="topbar">
  {% if has_logo %}
  <span class="logo-chip"><img src="/logo" alt="RemotIQ Partners"></span>
  {% else %}
  <h1>RemotIQ Partners</h1>
  {% endif %}
  <span class="sub">Expense Submission</span>
</div>

<div class="page">

{% if state == "needs_auth" %}
<div class="card">
  <div class="status-page">
    <div class="status-icon">🛠️</div>
    <h2 style="color:#c8102e">Expense submission is temporarily unavailable</h2>
    <p>The app is being reconnected to Xero. Finance has been notified. Please try again shortly.</p>
    <a href="/" class="btn-back">Try again</a>
    <p style="margin-top:28px;font-size:12px"><a href="/auth" style="color:#718096">Finance only: connect Xero</a></p>
  </div>
</div>

{% elif state == "success" %}
<div class="card">
  <div class="status-page success">
    <div class="status-icon">✅</div>
    <h2>Expenses Submitted!</h2>
    <p>{{ message|safe }}</p>
    <a href="/" class="btn-back">Submit Another Claim</a>
  </div>
</div>
<script>sessionStorage.removeItem('riq_expense_form');</script>

{% elif state == "error" %}
<div class="card">
  <div class="status-page error">
    <div class="status-icon">⚠️</div>
    <h2>Submission Error</h2>
    <p>{{ message|safe }}</p>
    <a href="/" class="btn-back">← Go Back</a>
  </div>
</div>

{% else %}
<form id="expForm" method="POST" action="/submit" enctype="multipart/form-data" novalidate>

  <!-- Employee Details -->
  <div class="card">
    <div class="card-title">Employee Details</div>
    <div class="notice">
      Submitted expenses are automatically forwarded to Xero as
      <strong>Draft Bills</strong> for review and approval.
    </div>
    <div class="field">
      <label for="name_search">Full Name *</label>
      <div class="combo">
        <input type="search" id="name_search" autocomplete="off" autocorrect="off"
               autocapitalize="words" spellcheck="false" data-1p-ignore data-lpignore="true"
               placeholder="Search your name in Xero contacts…" role="combobox"
               aria-autocomplete="list" aria-controls="name_list" aria-expanded="false">
        <div id="name_list" class="combo-list" role="listbox" hidden></div>
      </div>
      <input type="hidden" name="employee_name" id="employee_name">
      <input type="hidden" name="contact_id" id="contact_id">
      <small id="name_hint" style="color:#718096;font-size:12px">
        Pick your name from Xero contacts. Not listed? Type your full name and it will be added to Xero.</small>
    </div>
    <div class="field">
      <label>Email Address *</label>
      <input type="email" name="employee_email" required placeholder="you@remotiqpartners.com"
             autocomplete="email" inputmode="email">
    </div>
    <div class="field">
      <label>Filer Type *</label>
      <select name="filer_type" id="filer_type" required onchange="applyFilerType()">
        <option value="">Select…</option>
        <option value="Employee">Employee</option>
        <option value="Non-employee">Non-employee</option>
      </select>
    </div>
    <div id="bank_section" class="bank-box" hidden>
      <div class="bank-title">Bank account for reimbursement *</div>
      <p class="bank-note">Required for non-employees. Finance will pay the reimbursement to this
        account. It is saved as the Account number on your Xero contact.</p>
      <div class="field">
        <label>Bank Name *</label>
        <input type="text" name="bank_name" list="ph_banks" autocomplete="off" placeholder="e.g. BDO, BPI, GCash">
        <datalist id="ph_banks">
          {% for b in ["BDO Unibank","BPI","Metrobank","Land Bank of the Philippines","PNB","Security Bank",
                       "UnionBank","RCBC","China Bank","EastWest Bank","Maybank","GCash","Maya"] %}<option value="{{ b }}">{% endfor %}
        </datalist>
      </div>
      <div class="field">
        <label>Account Name *</label>
        <input type="text" name="bank_acct_name" autocomplete="off" autocapitalize="words" placeholder="Name on the account">
      </div>
      <div class="field">
        <label>Account Number *</label>
        <input type="text" name="bank_acct_no" autocomplete="off" inputmode="numeric" placeholder="e.g. 0012 3456 7890">
      </div>
    </div>
    <div class="field">
      <label>Submission Date *</label>
      <input type="date" name="submission_date" id="sub_date" required>
    </div>
    <div class="field">
      <label>Currency *</label>
      <select name="currency" id="currency_select" required onchange="updateCurrency()">
        <option value="PHP">PHP – Philippine Peso (₱)</option>
        <option value="USD">USD – US Dollar ($)</option>
        <option value="AUD">AUD – Australian Dollar (A$)</option>
        <option value="SGD">SGD – Singapore Dollar (S$)</option>
        <option value="EUR">EUR – Euro (€)</option>
        <option value="GBP">GBP – British Pound (£)</option>
        <option value="JPY">JPY – Japanese Yen (¥)</option>
        <option value="HKD">HKD – Hong Kong Dollar (HK$)</option>
        <option value="CNY">CNY – Chinese Yuan (¥)</option>
        <option value="NZD">NZD – New Zealand Dollar (NZ$)</option>
      </select>
    </div>
    <div class="field">
      <label>Purpose / Notes *</label>
      <select name="purpose" id="purpose_select" required onchange="applyPurpose()">
        <option value="">Select purpose…</option>
        {% for label, code in purposes %}<option value="{{ label }}">{{ label }}</option>
        {% endfor %}
      </select>
      <small id="purpose_hint" style="color:#718096;font-size:12px"></small>
    </div>
  </div>

  <!-- Expense Items – Mobile cards -->
  <div id="mobile-lines"></div>

  <!-- Expense Items – Desktop table -->
  <div class="card" id="desktop-table">
    <div class="card-title">Expense Items</div>
    <div class="tscroll">
      <table>
        <thead>
          <tr>
            <th style="width:130px">Date *</th>
            <th style="width:170px">Account</th>
            <th style="width:150px">Supplier / Vendor *</th>
            <th>Description *</th>
            <th style="width:110px">TIN *</th>
            <th style="width:105px;text-align:right">Amount *</th>
            <th style="width:110px">Receipt *</th>
            <th style="width:38px"></th>
          </tr>
        </thead>
        <tbody id="desktop-rows"></tbody>
      </table>
      <div class="totals-bar">
        <span class="totals-label">Total</span>
        <span class="totals-amount" id="desktop-total">$0.00</span>
      </div>
    </div>
    <button type="button" class="btn-add" onclick="addLine()">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="3" stroke-linecap="round">
        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
      </svg>
      Add Expense Line
    </button>
  </div>

  <!-- Mobile total + add -->
  <div id="mobile-total-section">
    <div class="total-bar">
      <span class="lbl">Total</span>
      <span class="amt" id="mobile-total">$0.00</span>
    </div>
    <button type="button" class="btn-add" onclick="addLine()" style="margin-bottom:14px">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="3" stroke-linecap="round">
        <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
      </svg>
      Add Expense Line
    </button>
  </div>

  <div class="actions">
    <button type="submit" class="btn-submit" id="submitBtn">Submit to Xero →</button>
  </div>

</form>
{% endif %}

</div><!-- /page -->

<script>
// ── PWA install prompt ──────────────────────────────────────────────────────
let deferredPrompt = null;
const banner = document.getElementById('install-banner');

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  deferredPrompt = e;
  if (!localStorage.getItem('pwa-dismissed')) {
    banner.style.display = 'flex';
  }
});

function installApp() {
  if (deferredPrompt) {
    deferredPrompt.prompt();
    deferredPrompt.userChoice.then(() => {
      deferredPrompt = null;
      banner.style.display = 'none';
    });
  }
}

function dismissInstall() {
  banner.style.display = 'none';
  localStorage.setItem('pwa-dismissed', '1');
}

// Register service worker
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

// ── Currency ────────────────────────────────────────────────────────────────
const CURRENCY_SYMBOLS = {
  PHP: '₱', USD: '$', AUD: 'A$', SGD: 'S$',
  EUR: '€', GBP: '£', JPY: '¥', HKD: 'HK$',
  CNY: '¥', NZD: 'NZ$'
};

function currencySymbol() {
  const sel = document.getElementById('currency_select');
  return CURRENCY_SYMBOLS[sel ? sel.value : 'PHP'] || '';
}

function updateCurrency() { calcTotal(); }

// ── Full Name: searchable Xero contact list ─────────────────────────────────
const CONTACTS = {{ contacts | tojson }};
const norm = t => (t || '').toLowerCase().replace(/\s+/g, ' ').trim();
let comboItems = [], comboActive = -1;

function setContact(c, typed) {
  const search = document.getElementById('name_search');
  const hint = document.getElementById('name_hint');
  if (c) {
    search.value = c.name;
    document.getElementById('employee_name').value = c.name;
    document.getElementById('contact_id').value = c.id;
    const em = document.querySelector('[name="employee_email"]');
    if (em && (!em.value || em.value === em.dataset.auto)) { em.value = c.email || ''; em.dataset.auto = c.email || ''; }
    hint.textContent = '✓ Linked to Xero contact: ' + c.name;
    hint.style.color = '#276749';
  } else {
    const name = (typed || '').replace(/\s+/g, ' ').trim();
    const em = document.querySelector('[name="employee_email"]');
    if (em && em.dataset.auto && em.value === em.dataset.auto) { em.value = ''; em.dataset.auto = ''; }
    document.getElementById('employee_name').value = name;
    document.getElementById('contact_id').value = '';
    hint.textContent = name ? '➕ New Xero contact will be created: ' + name
                            : 'Pick your name from Xero contacts. Not listed? Type your full name and it will be added to Xero.';
    hint.style.color = name ? '#2b6cb0' : '#718096';
    const an = document.querySelector('[name="bank_acct_name"]');
    if (an) an.placeholder = name || 'Name on the account';
  }
  autoSave();
}

function applyFilerType() {
  const ft = document.getElementById('filer_type');
  document.getElementById('bank_section').hidden = !(ft && ft.value === 'Non-employee');
}

function exactContact(q) { return CONTACTS.find(c => norm(c.name) === norm(q)); }

function renderCombo() {
  const search = document.getElementById('name_search');
  const list = document.getElementById('name_list');
  const q = norm(search.value), words = q.split(' ').filter(Boolean);
  const hits = CONTACTS.filter(c => words.every(w => norm(c.name).includes(w))).slice(0, 50);
  comboItems = hits.map(c => ({ c }));
  if (q && !exactContact(q)) comboItems.push({ newName: search.value.replace(/\s+/g, ' ').trim() });
  comboActive = comboItems.length ? 0 : -1;
  list.innerHTML = comboItems.map((it, n) => it.c
      ? `<div class="combo-item" role="option" data-n="${n}"><span>${escHtml(it.c.name)}</span><small>${escHtml(it.c.email || '')}</small></div>`
      : `<div class="combo-item new" role="option" data-n="${n}">➕ Not in the list — add “${escHtml(it.newName)}” as a new contact</div>`
    ).join('') || `<div class="combo-empty">${CONTACTS.length ? 'Start typing your name…' : 'Contacts unavailable. Type your full name.'}</div>`;
  highlight();
  list.hidden = false; search.setAttribute('aria-expanded', 'true');
}
function highlight() {
  document.querySelectorAll('#name_list .combo-item').forEach((el, n) => el.classList.toggle('active', n === comboActive));
  const el = document.querySelector('#name_list .combo-item.active'); if (el) el.scrollIntoView({ block: 'nearest' });
}
function pick(n) {
  const it = comboItems[n]; if (!it) return;
  it.c ? setContact(it.c) : setContact(null, it.newName);
  closeCombo();
}
function closeCombo() {
  document.getElementById('name_list').hidden = true;
  document.getElementById('name_search').setAttribute('aria-expanded', 'false');
}
function escHtml(t) { return String(t).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }

function initCombo() {
  const search = document.getElementById('name_search');
  if (!search) return;
  search.addEventListener('focus', renderCombo);
  search.addEventListener('input', () => { setContact(null, search.value); renderCombo(); });
  search.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { comboActive = Math.min(comboActive + 1, comboItems.length - 1); highlight(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { comboActive = Math.max(comboActive - 1, 0); highlight(); e.preventDefault(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (comboActive >= 0) pick(comboActive); }
    else if (e.key === 'Escape') closeCombo();
  });
  document.getElementById('name_list').addEventListener('mousedown', e => {
    const el = e.target.closest('.combo-item'); if (el) { e.preventDefault(); pick(+el.dataset.n); }
  });
  search.addEventListener('blur', () => setTimeout(() => {
    closeCombo();
    const c = exactContact(search.value);            // typed an existing name exactly → link it
    if (c && !document.getElementById('contact_id').value) setContact(c);
  }, 120));
}

function restoreName() {
  const name = document.getElementById('employee_name').value;
  if (!name) return;
  document.getElementById('name_search').value = name;
  const id = document.getElementById('contact_id').value;
  const c = id ? CONTACTS.find(x => x.id === id) : exactContact(name);
  c ? setContact(c) : setContact(null, name);
}

// ── Expense lines ───────────────────────────────────────────────────────────
let idx = 0;
const isMobile = () => window.innerWidth < 700;

const PURPOSES = {{ purposes | tojson }};

function purposeAccount(label) {
  const hit = PURPOSES.find(([l]) => l === label);
  return hit ? hit[1] : null;
}

// Look up the Account from the selected Purpose / Notes and apply it to every line
function applyPurpose() {
  const sel = document.getElementById('purpose_select');
  if (!sel) return;
  const code = purposeAccount(sel.value);
  document.querySelectorAll('select[name^="a"]').forEach(a => {
    if (!/^a\d+$/.test(a.name)) return;
    if (code) { a.value = code; a.classList.add('locked'); a.tabIndex = -1; }
    else      { if (a.classList.contains('locked')) a.value = ''; a.classList.remove('locked'); a.tabIndex = 0; }
  });
  const hint = document.getElementById('purpose_hint');
  if (hint) {
    const opt = code && document.querySelector('select[name^="a"] option[value="' + code + '"]');
    hint.textContent = !sel.value ? ''
      : code ? 'Account for all expense lines: ' + (opt ? opt.textContent : code)
      : 'Manual account entry: choose the Account on each line. You can add several lines with different accounts.';
  }
}

function accountOptions(sel) {
  return `<option value="">Select account…</option>
          <option value="420" ${sel==='420'?'selected':''}>420 – Meals &amp; Entertainment</option>
          <option value="421" ${sel==='421'?'selected':''}>421 – Engagements &amp; Trainings</option>
          <option value="489" ${sel==='489'?'selected':''}>489 – Telephone &amp; Internet</option>
          <option value="493" ${sel==='493'?'selected':''}>493 – Travel &amp; Transportation</option>
          <option value="other" ${sel==='other'?'selected':''}>Other (not listed above)</option>`;
}

function addLine(d, a, s, desc, tin, amt) {
  const today = d || new Date().toISOString().slice(0, 10);
  const i = idx++;

  // ── Mobile card ──
  const card = document.createElement('div');
  card.className = 'exp-card';
  card.id = 'mcard' + i;
  card.innerHTML = `
    <div class="row-num">Expense ${i + 1}</div>
    <button type="button" class="btn-del" onclick="removeLine(${i})" title="Remove">×</button>
    <div class="field">
      <label>Date *</label>
      <input type="date" name="d${i}" value="${today}" required>
    </div>
    <div class="field">
      <label>Account *</label>
      <select name="a${i}">${accountOptions(a||'')}</select>
    </div>
    <div class="field">
      <label>Supplier / Vendor *</label>
      <input type="text" name="s${i}" value="${s||''}" placeholder="Supplier name"
             autocapitalize="words" required>
    </div>
    <div class="field">
      <label>Description *</label>
      <input type="text" name="desc${i}" value="${desc||''}" placeholder="What was this for?"
             autocapitalize="sentences" required>
    </div>
    <div class="field">
      <label>TIN *</label>
      <input type="text" name="tin${i}" value="${tin||''}" placeholder="e.g. 123-456-789"
             autocapitalize="none" inputmode="numeric" required>
    </div>
    <div class="field">
      <label>Amount *</label>
      <input type="number" name="amt${i}" value="${amt||''}" placeholder="0.00"
             step="0.01" min="0.01" inputmode="decimal" required
             oninput="calcTotal()">
    </div>
    <div class="field">
      <label>Receipt *</label>
      <input type="file" name="receipt${i}" accept="image/*,.pdf" capture="environment"
             style="font-size:13px" required>
    </div>
  `;
  document.getElementById('mobile-lines').appendChild(card);

  // ── Desktop table row ──
  const tr = document.createElement('tr');
  tr.id = 'drow' + i;
  tr.innerHTML = `
    <td><input type="date"   name="d${i}"    value="${today}" required></td>
    <td><select name="a${i}" required>${accountOptions(a||'')}</select></td>
    <td><input type="text"   name="s${i}"    value="${s||''}"    placeholder="Supplier" required></td>
    <td><input type="text"   name="desc${i}" value="${desc||''}" placeholder="Description" required></td>
    <td><input type="text"   name="tin${i}"  value="${tin||''}"  placeholder="TIN" required></td>
    <td><input type="number" name="amt${i}"  value="${amt||''}"  placeholder="0.00"
               step="0.01" min="0.01" required style="text-align:right" oninput="calcTotal()"></td>
    <td><input type="file" name="receipt${i}" accept="image/*,.pdf" style="font-size:11px;width:100%" required></td>
    <td><button type="button" class="btn-del" onclick="removeLine(${i})" title="Remove">×</button></td>
  `;
  document.getElementById('desktop-rows').appendChild(tr);

  applyPurpose();
  renumberCards();
  calcTotal();
}

function removeLine(i) {
  ['mcard', 'drow'].forEach(prefix => {
    const el = document.getElementById(prefix + i);
    if (el) el.remove();
  });
  renumberCards();
  calcTotal();
}

function renumberCards() {
  document.querySelectorAll('.exp-card .row-num').forEach((el, n) => {
    el.textContent = 'Expense ' + (n + 1);
  });
}

function calcTotal() {
  // Only read from the visible section to avoid double-counting hidden fields
  const src = window.innerWidth >= 700 ? '#desktop-rows' : '#mobile-lines';
  let t = 0;
  document.querySelectorAll(src + ' [name^="amt"]').forEach(el => {
    t += parseFloat(el.value) || 0;
  });
  const fmt = currencySymbol() + t.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.getElementById('mobile-total').textContent  = fmt;
  document.getElementById('desktop-total').textContent = fmt;
}

// ── Session storage: auto-save & restore ────────────────────────────────────
const STORE_KEY = 'riq_expense_form';

function autoSave() {
  try {
    const data = { lines: [], files: {} };
    ['employee_name','contact_id','employee_email','submission_date','currency','purpose','filer_type','bank_name','bank_acct_name','bank_acct_no'].forEach(k => {
      const el = document.querySelector('[name="' + k + '"]');
      if (el) data[k] = el.value;
    });
    const seen = new Set();
    document.querySelectorAll('#desktop-rows [name^="amt"]').forEach(el => {
      const m = el.name.match(/^amt(\d+)$/);
      if (!m || seen.has(m[1])) return;
      seen.add(m[1]);
      const i = m[1];
      data.lines.push({
        i,
        d:    (document.querySelector('[name="d'+i+'"]') || {}).value || '',
        a:    (document.querySelector('#desktop-rows [name="a'+i+'"]') || {}).value || '',
        s:    (document.querySelector('[name="s'+i+'"]') || {}).value || '',
        desc: (document.querySelector('[name="desc'+i+'"]') || {}).value || '',
        tin:  (document.querySelector('[name="tin'+i+'"]') || {}).value || '',
        amt:  el.value || '',
      });
    });
    // preserve previously saved filenames
    const prev = JSON.parse(sessionStorage.getItem(STORE_KEY) || '{}');
    data.files = prev.files || {};
    sessionStorage.setItem(STORE_KEY, JSON.stringify(data));
  } catch(e) {}
}

function restoreFromStorage() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null');
    if (!saved) return;
    ['employee_name','contact_id','employee_email','submission_date','currency','purpose','filer_type','bank_name','bank_acct_name','bank_acct_no'].forEach(k => {
      const el = document.querySelector('[name="' + k + '"]');
      if (el && saved[k]) el.value = saved[k];
    });
    if (saved.currency) updateCurrency();
    applyPurpose();
    restoreName();
    applyFilerType();
    if (saved.lines && saved.lines.length) {
      document.getElementById('mobile-lines').innerHTML = '';
      document.getElementById('desktop-rows').innerHTML = '';
      idx = 0;
      saved.lines.forEach(line => addLine(line.d, line.a, line.s, line.desc, line.tin, line.amt));
    }
    // Show previously selected filenames
    if (saved.files) {
      Object.keys(saved.files).forEach(function(i) {
        const fname = saved.files[i];
        if (!fname) return;
        setTimeout(function() {
          document.querySelectorAll('[name="receipt' + i + '"]').forEach(function(fi) {
            if (!fi.parentNode.querySelector('.file-note')) {
              const note = document.createElement('small');
              note.className = 'file-note';
              note.style.cssText = 'display:block;color:#718096;font-size:11px;margin-top:3px';
              note.textContent = '📎 Previously: ' + fname + ' — re-select to attach';
              fi.after(note);
            }
          });
        }, 50);
      });
    }
  } catch(e) {}
}

// ── Init ────────────────────────────────────────────────────────────────────
document.getElementById('sub_date').value = new Date().toISOString().slice(0, 10);
addLine();
initCombo();
restoreFromStorage();

// Auto-save on any change
document.getElementById('expForm').addEventListener('input', autoSave);
document.getElementById('expForm').addEventListener('change', function(e) {
  // Mirror account/date/text changes to the twin field in the other layout
  const tw = e.target.name && e.target.name.match(/^(a|d|s|desc|tin|amt)(\d+)$/);
  if (tw) document.querySelectorAll('[name="' + e.target.name + '"]').forEach(x => { if (x !== e.target) x.value = e.target.value; });
  // Track selected file names
  if (e.target.type === 'file' && e.target.files[0]) {
    try {
      const m = e.target.name.match(/^receipt(\d+)$/);
      if (m) {
        const saved = JSON.parse(sessionStorage.getItem(STORE_KEY) || '{}');
        if (!saved.files) saved.files = {};
        saved.files[m[1]] = e.target.files[0].name;
        sessionStorage.setItem(STORE_KEY, JSON.stringify(saved));
      }
    } catch(err) {}
  }
  autoSave();
});

document.getElementById('expForm').addEventListener('submit', function(e) {
  const cards = document.querySelectorAll('.exp-card');
  const rows  = document.querySelectorAll('#desktop-rows tr');
  if (!cards.length && !rows.length) {
    e.preventDefault();
    alert('Please add at least one expense line.');
    return;
  }
  const pur = document.getElementById('purpose_select');
  const nm = document.getElementById('employee_name');
  if (!nm.value.trim()) setContact(exactContact(document.getElementById('name_search').value) || null,
                                   document.getElementById('name_search').value);
  if (!nm.value.trim()) {
    e.preventDefault();
    alert('Please select your name from the Xero contacts, or type your full name.');
    return;
  }
  const ftype = document.getElementById('filer_type').value;
  if (!ftype) {
    e.preventDefault();
    alert('Please select the Filer Type (Employee or Non-employee).');
    return;
  }
  if (ftype === 'Non-employee') {
    const missing = ['bank_name','bank_acct_name','bank_acct_no'].some(k => !document.querySelector('[name="' + k + '"]').value.trim());
    if (missing) {
      e.preventDefault();
      applyFilerType();
      alert('Non-employee filers must give their Bank Name, Account Name and Account Number.');
      return;
    }
  }
  const vis = window.innerWidth >= 700 ? '#desktop-rows' : '#mobile-lines';
  const missingT = [...document.querySelectorAll(vis + ' [name^="tin"]')].some(x => !x.value.trim());
  const missingA = [...document.querySelectorAll(vis + ' select[name^="a"]')].some(x => /^a\d+$/.test(x.name) && !x.value);
  const missingR = [...document.querySelectorAll(vis + ' input[type="file"]')].some(x => !x.files || !x.files.length);
  if (!pur.value || missingT || missingA || missingR) {
    e.preventDefault();
    alert(!pur.value ? 'Please select a Purpose / Notes.'
        : missingT ? 'Please enter the supplier TIN for every expense line.'
        : missingA ? 'Please choose the Account for every expense line.'
        : 'Please attach a receipt for every expense line.');
    return;
  }
  // Disable hidden section's inputs so only visible values are submitted
  if (window.innerWidth >= 700) {
    // Desktop: disable mobile card inputs (they are empty/hidden)
    document.querySelectorAll('#mobile-lines input, #mobile-lines select').forEach(el => el.disabled = true);
  } else {
    // Mobile: disable desktop table inputs
    document.querySelectorAll('#desktop-rows input, #desktop-rows select').forEach(el => el.disabled = true);
  }
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Submitting…';
});
</script>
</body>
</html>
"""

# ─── Token helpers ─────────────────────────────────────────────────────────────

def load_tokens():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)
    return None

def save_tokens(tokens):
    tmp = TOKEN_FILE + ".tmp"           # write-then-rename so a crash can't corrupt it
    with open(tmp, "w") as f:
        json.dump(tokens, f, indent=2)
    os.replace(tmp, TOKEN_FILE)

def refresh_if_needed(tokens):
    if datetime.now().timestamp() > tokens.get("expires_at", 0) - 300:
        r = requests.post(
            "https://identity.xero.com/connect/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id":     XERO_CLIENT_ID,
                "client_secret": XERO_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        new = r.json()
        new["expires_at"] = datetime.now().timestamp() + new.get("expires_in", 1800)
        new.setdefault("refresh_token", tokens["refresh_token"])
        save_tokens(new)
        return new
    return tokens

class WrongOrganisation(Exception):
    pass

def get_tenant_id(access_token):
    """Return the tenant ID of XERO_TENANT_NAME only; never fall back to another org."""
    r = requests.get(
        "https://api.xero.com/connections",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        timeout=15,
    )
    r.raise_for_status()
    conns = r.json()
    for c in conns:
        if (c.get("tenantName") or "").strip().lower() == XERO_TENANT_NAME.strip().lower():
            return c["tenantId"]
    if conns:
        names = ", ".join(c.get("tenantName") or "?" for c in conns)
        raise WrongOrganisation(
            f"The app is connected to {names}, not {XERO_TENANT_NAME} "
            f"— Finance needs to reconnect at /auth and choose {XERO_TENANT_NAME}")
    return None


# ─── Xero contacts for the Full Name list ─────────────────────────────────────
_contacts_cache = {"at": 0.0, "contacts": []}

def _xero_get(path, access_token, tenant_id, params=None):
    r = requests.get(f"https://api.xero.com/api.xro/2.0/{path}", params=params, timeout=15,
                     headers={"Authorization": f"Bearer {access_token}",
                              "Xero-tenant-id": tenant_id, "Accept": "application/json"})
    r.raise_for_status()
    return r.json()

def xero_contacts(force=False):
    """All active Xero contacts as [{id, name, email}], cached 5 minutes. Never raises."""
    now = datetime.now().timestamp()
    if not force and now - _contacts_cache["at"] < 300:
        return _contacts_cache["contacts"]
    try:
        tokens = refresh_if_needed(load_tokens())
        at = tokens["access_token"]
        tid = get_tenant_id(at)
        out = []
        if tid:
            for page in range(1, 21):
                batch = _xero_get("Contacts", at, tid, params={
                    "where": 'ContactStatus=="ACTIVE"', "summaryOnly": "true",
                    "order": "Name", "page": page, "pageSize": 500}).get("Contacts", [])
                out += [{"id": c["ContactID"], "name": c["Name"].strip(),
                         "email": c.get("EmailAddress") or ""}
                        for c in batch if c.get("Name")]
                if len(batch) < 500:
                    break
        out.sort(key=lambda c: c["name"].lower())
        _contacts_cache.update(at=now, contacts=out)
    except Exception as e:
        log.warning("Could not load Xero contacts: %s", e)
        _contacts_cache["at"] = now - 240   # retry in about a minute
    return _contacts_cache["contacts"]

def _xero_post(path, access_token, tenant_id, body):
    r = requests.post(f"https://api.xero.com/api.xro/2.0/{path}", json=body, timeout=20,
                      headers={"Authorization": f"Bearer {access_token}", "Xero-tenant-id": tenant_id,
                               "Accept": "application/json", "Content-Type": "application/json"})
    return r

def _mask(acct):
    digits = "".join(ch for ch in acct if ch.isalnum())
    return ("•" * max(len(digits) - 4, 0)) + digits[-4:] if digits else ""

def _acct_number_field(bank_name, acct_no, acct_name):
    """Text for the Xero contact's 'Account number' field (max 50 characters).
    Bank and account number always fit; the account name is added if there is room."""
    core = f"{bank_name} {acct_no}"
    full = f"{core} ({acct_name})"
    return (full if len(full) <= 50 else core)[:50]

def _save_contact(access_token, tenant_id, contact):
    """Create/update a Xero contact. If Xero rejects the Account number (e.g. already used
    by another contact), retry without it so the claim still goes through."""
    r = _xero_post("Contacts", access_token, tenant_id, {"Contacts": [contact]})
    note = ""
    if r.status_code not in (200, 201) and "AccountNumber" in contact:
        note = ("Bank details could not be saved to the contact's Account number "
                "(Xero rejected it, possibly already used by another contact).")
        log.warning("%s %s", note, r.text[:200])
        contact = {k: v for k, v in contact.items() if k != "AccountNumber"}
        r = _xero_post("Contacts", access_token, tenant_id, {"Contacts": [contact]})
    return r, note

def _norm_name(t):
    return " ".join((t or "").split()).lower()

def render(state="form", message="", contacts=None):
    return render_template_string(TEMPLATE, state=state, message=message,
                                  purposes=PURPOSES, has_logo=True, contacts=contacts or [])

# ─── Email notifications ───────────────────────────────────────────────────────

def send_email(to, cc, subject, html_body):
    """Send one email. Never raises: a mail problem must not break a submission."""
    to = [a for a in to if a]
    cc = [a for a in cc if a and a not in to]
    if not to and cc:
        to, cc = cc, []
    if not to:
        log.warning("Email skipped (no recipients): %s", subject)
        return False
    try:
        if RESEND_API_KEY:
            r = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={"from": EMAIL_FROM, "to": to, "cc": cc,
                      "subject": subject, "html": html_body},
                timeout=10,
            )
            if r.status_code >= 300:
                log.error("Resend error %s: %s", r.status_code, r.text[:300])
                return False
            return True
        if SMTP_HOST:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = EMAIL_FROM, ", ".join(to), subject
            if cc:
                msg["Cc"] = ", ".join(cc)
            msg.set_content("This email requires an HTML-capable mail client.")
            msg.add_alternative(html_body, subtype="html")
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as srv:
                srv.starttls()
                if SMTP_USER:
                    srv.login(SMTP_USER, SMTP_PASS)
                srv.send_message(msg)
            return True
        log.warning("Email skipped (no RESEND_API_KEY or SMTP_HOST set): %s", subject)
    except Exception as e:
        log.error("Email send failed: %s", e)
    return False


def _email_shell(title, colour, intro, body_html):
    return f"""<div style="font-family:Arial,sans-serif;color:#2d3748;max-width:720px">
  <div style="background:{colour};color:#fff;padding:14px 18px;border-radius:8px 8px 0 0">
    <strong style="font-size:16px">{escape(title)}</strong></div>
  <div style="border:1px solid #e2e8f0;border-top:none;padding:18px;border-radius:0 0 8px 8px">
    <p style="margin:0 0 14px">{intro}</p>{body_html}
    <p style="color:#718096;font-size:12px;margin-top:18px">
      Sent automatically by the RemotIQ Expense Submission app.</p></div></div>"""


def _claim_summary_html(claim, lines):
    rows = "".join(
        f"<tr><td>{escape(l['date'])}</td>"
        f"<td>{escape(l['account'] or '—')}</td><td>{escape(l['supplier'])}</td>"
        f"<td>{escape(l['desc'])}</td><td>{escape(l['tin'])}</td>"
        f"<td style='text-align:right'>{l['amount']:,.2f}</td></tr>"
        for l in lines)
    total = sum(l["amount"] for l in lines)
    td = "padding:6px 8px;border-bottom:1px solid #edf2f7;font-size:13px"
    rows = rows.replace("<td", f"<td style='{td}'").replace("style='" + td + "' style='", "style='" + td + ";")
    head = "".join(f"<th style='text-align:left;padding:6px 8px;background:#f7f9fc;font-size:12px'>{h}</th>"
                   for h in ["Date", "Account", "Supplier", "Description", "TIN", "Amount"])
    info = "".join(f"<tr><td style='padding:3px 12px 3px 0;color:#718096'>{k}</td><td><strong>{escape(str(v))}</strong></td></tr>"
                   for k, v in claim.items() if v)
    table = (f"<table style='border-collapse:collapse;width:100%;margin-top:14px'><tr>{head}</tr>{rows}"
             f"<tr><td colspan='5' style='padding:8px;text-align:right'><strong>Total</strong></td>"
             f"<td style='padding:8px;text-align:right'><strong>{total:,.2f}</strong></td></tr></table>") if lines else ""
    return f"<table style='font-size:14px'>{info}</table>{table}"


def notify(ok, employee_email, claim, lines, detail=""):
    """Email the filer and finance about the outcome of a submission."""
    who = claim.get("Employee") or "an employee"
    if ok:
        subject = f"✅ Expense claim submitted – {claim.get('Reference', '')} ({who})"
        intro = (f"The expense claim below was submitted to Xero as a <strong>Draft Bill</strong> "
                 f"and is awaiting finance review and approval.")
        if detail:
            intro += f"<br><br>{detail}"
        html = _email_shell("Expense claim submitted", "#276749", intro, _claim_summary_html(claim, lines))
    else:
        subject = f"⚠️ Expense claim NOT submitted – {who}"
        intro = (f"The expense claim below <strong>was not submitted</strong> to Xero.<br>"
                 f"<strong>Reason:</strong> {escape(detail)}<br><br>"
                 f"Please correct the issue and submit again.")
        html = _email_shell("Expense claim NOT submitted", "#c53030", intro, _claim_summary_html(claim, lines))
    return send_email([employee_email], [], subject, html)   # filer only

# ─── PWA asset routes ──────────────────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return jsonify(MANIFEST)

@app.route("/logo")
def logo():
    path, mime = find_logo()
    if path:
        with open(path, "rb") as f:
            data = f.read()
    else:
        import base64
        data, mime = base64.b64decode(LOGO_PNG_B64), "image/png"
    return Response(data, mimetype=mime, headers={"Cache-Control": "public, max-age=3600"})

@app.route("/icon.svg")
def icon():
    return Response(ICON_SVG, mimetype="image/svg+xml")

@app.route("/sw.js")
def sw():
    return Response(SERVICE_WORKER, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/"})

# ─── App routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if not load_tokens():
        _alert_finance_not_connected()
        return render("needs_auth")
    return render("form", contacts=xero_contacts())

_last_alert = {"at": 0.0}

def _alert_finance_not_connected():
    now = datetime.now().timestamp()
    if now - _last_alert["at"] < 3600 or not FINANCE_EMAIL:
        return
    _last_alert["at"] = now
    url = XERO_REDIRECT_URI.rsplit("/", 1)[0] + "/auth"
    send_email(FINANCE_EMAIL, [], "⚠️ Expense app needs Xero reconnection",
               _email_shell("Expense app is not connected to Xero", "#c53030",
                            f"An employee tried to open the expense form, but the app is not connected "
                            f"to Xero. Please sign in at <a href='{url}'>{escape(url)}</a> and choose "
                            f"<strong>{escape(XERO_TENANT_NAME)}</strong>.", ""))

@app.route("/auth")
def auth():
    state = secrets.token_urlsafe(16)
    session["xero_state"] = state
    url = (
        f"https://login.xero.com/identity/connect/authorize"
        f"?response_type=code"
        f"&client_id={XERO_CLIENT_ID}"
        f"&redirect_uri={XERO_REDIRECT_URI}"
        f"&scope={XERO_SCOPES}"
        f"&state={state}"
    )
    return redirect(url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return render("error", "Xero authorisation was cancelled or failed.")
    r = requests.post(
        "https://identity.xero.com/connect/token",
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  XERO_REDIRECT_URI,
            "client_id":     XERO_CLIENT_ID,
            "client_secret": XERO_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    t = r.json()
    t["expires_at"] = datetime.now().timestamp() + t.get("expires_in", 1800)
    save_tokens(t)
    return redirect(url_for("index"))

@app.route("/submit", methods=["POST"])
def submit():
    # ── Parse employee fields first, so a failure email can always be sent ──
    employee_name   = " ".join(request.form.get("employee_name", "").split())
    contact_id      = request.form.get("contact_id", "").strip()
    filer_type      = request.form.get("filer_type", "").strip()
    bank_name       = " ".join(request.form.get("bank_name", "").split())
    bank_acct_name  = " ".join(request.form.get("bank_acct_name", "").split())
    bank_acct_no    = " ".join(request.form.get("bank_acct_no", "").split())
    employee_email  = request.form.get("employee_email",  "").strip()
    submission_date = request.form.get("submission_date") or datetime.now().strftime("%Y-%m-%d")
    purpose         = request.form.get("purpose", "").strip()
    currency        = request.form.get("currency", "PHP").strip().upper() or "PHP"

    claim = {"Employee": employee_name, "Email": employee_email,
             "Submission date": submission_date, "Purpose / Notes": purpose,
             "Currency": currency}
    lines_summary = []

    def fail(reason):
        sent = notify(False, employee_email, claim, lines_summary, reason)
        extra = (" A notification email has been sent to you." if sent
                 else " (We could not send you a notification email.)")
        return render("error", escape(reason) + extra)

    if not employee_name:
        return fail("Please select your name from the Xero contacts, or type your full name.")
    claim["Filer type"] = filer_type
    if filer_type not in ("Employee", "Non-employee"):
        return fail("Please select the Filer Type (Employee or Non-employee).")
    is_non_employee = filer_type == "Non-employee"
    if is_non_employee and not (bank_name and bank_acct_name and bank_acct_no):
        return fail("Non-employee filers must give their Bank Name, Account Name and Account Number.")
    bank_details = (f"{bank_name} {bank_acct_no} ({bank_acct_name})"[:255]) if is_non_employee else ""
    acct_field   = _acct_number_field(bank_name, bank_acct_no, bank_acct_name) if is_non_employee else ""
    if purpose not in PURPOSE_ACCOUNT:
        return fail("Please select a Purpose / Notes from the list.")
    purpose_account = PURPOSE_ACCOUNT[purpose]
    short_purpose   = purpose.rsplit(" (", 1)[0]

    tokens = load_tokens()
    if not tokens:
        return fail("The app is not connected to Xero. Finance needs to reconnect it at /auth.")

    try:
        tokens = refresh_if_needed(tokens)
    except Exception as e:
        return fail(f"Xero token refresh failed: {e}")

    access_token = tokens["access_token"]

    try:
        tenant_id = get_tenant_id(access_token)
    except WrongOrganisation as e:
        return fail(str(e))
    except Exception as e:
        return fail(f"Could not reach the Xero organisation: {e}")

    if not tenant_id:
        return fail(f"No Xero organisation found. Finance needs to reconnect at /auth "
                    f"and choose {XERO_TENANT_NAME}.")

    # ── Collect expense lines (scan for amt0, amt1, … dynamically) ──
    form    = request.form.to_dict()
    indices = sorted({int(k[3:]) for k in form if k.startswith("amt") and k[3:].isdigit()})

    line_items = []
    for i in indices:
        date     = form.get(f"d{i}",    "").strip()
        account  = form.get(f"a{i}",    "").strip()
        supplier = form.get(f"s{i}",    "").strip()
        desc     = form.get(f"desc{i}", "").strip()
        tin      = form.get(f"tin{i}",  "").strip()
        if not tin:
            return fail(f"Expense line {len(line_items) + 1}: supplier TIN is required.")
        # Account is looked up from Purpose / Notes (chart of accounts mapping);
        # manual purposes (Liquidation, Reimbursement, Cash Advance, Others, …)
        # need an account chosen on each line
        if purpose_account:
            account = purpose_account
        elif not account:
            return fail(f"Expense line {len(line_items) + 1}: please choose the Account "
                        f"(required for '{short_purpose}').")
        try:
            amount = round(float(form.get(f"amt{i}", "0")), 2)
        except ValueError:
            continue
        if not (date and amount > 0):
            continue
        receipt = request.files.get(f"receipt{i}")
        if not receipt or not receipt.filename:
            return fail(f"Expense line {len(line_items) + 1} ({supplier or 'no supplier'}): "
                        f"a receipt attachment is required.")

        full_desc = f"[{short_purpose}] {desc}"
        if supplier:
            full_desc += f" — {supplier}"
        if tin:
            full_desc += f" | TIN: {tin}"
        full_desc += f" ({date})"

        # Tax type: 12% VAT if TIN provided, VAT Exempt if not (TIN is required as of v2,
        # so lines normally go in as INPUT; the fallback is kept for safety)
        tax_type = "INPUT" if tin else "EXEMPTEXPENSES"

        # Use account code if one of the supported codes; leave blank for "other"
        line = {
            "Description": full_desc,
            "Quantity":    1,
            "UnitAmount":  amount,
            "TaxType":     tax_type,
        }
        if account and account != "other":
            line["AccountCode"] = account

        line_items.append(line)
        lines_summary.append({"date": date,
                              "account": line.get("AccountCode", ""), "supplier": supplier,
                              "desc": desc, "tin": tin, "amount": amount})

    if not line_items:
        return fail("No valid expense lines found. Please go back and check your entries.")

    # ── Xero draft bill payload ──
    first = employee_name.split()[0].upper() if employee_name else "EMP"
    ref   = f"EXP-{first}-{submission_date.replace('-', '')}"
    claim["Reference"] = ref

    # ── Link to an existing Xero contact (no duplicates); create one only if new ──
    known = {c["id"]: c for c in xero_contacts()}
    match = known.get(contact_id) if contact_id else None
    if not match:
        match = next((c for c in known.values() if _norm_name(c["name"]) == _norm_name(employee_name)), None)
    try:
        if match:
            contact_ref = {"ContactID": match["id"]}
            employee_name = match["name"]
            if bank_details:   # non-employee: keep their bank details current on the contact
                ur, note = _save_contact(access_token, tenant_id,
                                         {"ContactID": match["id"], "AccountNumber": acct_field,
                                          "BankAccountDetails": bank_details})
                if note:
                    claim["Note"] = note
                if ur.status_code not in (200, 201):
                    log.warning("Could not update bank details for %s: %s", employee_name, ur.text[:200])
                    claim["Note"] = "Bank details could not be saved to the Xero contact — please update it manually."
        else:
            new_contact = {"Name": employee_name,
                           **({"EmailAddress": employee_email} if employee_email else {}),
                           **({"AccountNumber": acct_field,
                               "BankAccountDetails": bank_details} if bank_details else {})}
            cr, note = _save_contact(access_token, tenant_id, new_contact)
            if note:
                claim["Note"] = note
            if cr.status_code not in (200, 201):
                raise Exception(f"{cr.status_code}: {cr.text[:300]}")
            contact_ref = {"ContactID": cr.json()["Contacts"][0]["ContactID"]}
            _contacts_cache["at"] = 0.0
    except Exception as e:
        return fail(f"Could not create or update your contact in Xero ({e}). Please try again or contact Finance.")
    claim["Employee"] = employee_name
    claim["Xero contact"] = "Existing contact" if match else "New contact created"
    if bank_details:
        claim["Bank details"] = f"{bank_name} · {bank_acct_name} · {_mask(bank_acct_no)}"
    due   = (datetime.strptime(submission_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")

    payload = {
        "Invoices": [{
            "Type":            "ACCPAY",
            "Status":          "DRAFT",
            "Contact":         contact_ref,
            "Date":            submission_date,
            "DueDate":         due,
            "Reference":       ref,
            "CurrencyCode":    currency,
            "LineAmountTypes": "Inclusive",
            "LineItems":       line_items,
            "Narrative":       f"{purpose} – {filer_type}",
        }]
    }

    try:
      resp = requests.post(
        "https://api.xero.com/api.xro/2.0/Invoices",
        headers={
            "Authorization":  f"Bearer {access_token}",
            "Xero-tenant-id": tenant_id,
            "Content-Type":   "application/json",
            "Accept":         "application/json",
        },
        json=payload,
        timeout=30,
      )
    except Exception as e:
        return fail(f"Could not send the claim to Xero: {e}")

    if resp.status_code in (200, 201):
        inv     = resp.json().get("Invoices", [{}])[0]
        inv_num = inv.get("InvoiceNumber", ref)
        inv_id  = inv.get("InvoiceID")
        total   = inv.get("Total", sum(li["UnitAmount"] for li in line_items))

        # ── Upload receipt attachments ──
        mime_map = {
            "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "png": "image/png",  "pdf":  "application/pdf",
            "gif": "image/gif",  "heic": "image/heic",
            "webp": "image/webp",
        }
        att_count = att_failed = 0
        if inv_id:
            for i in indices:
                receipt = request.files.get(f"receipt{i}")
                if not receipt or not receipt.filename:
                    continue
                fname   = receipt.filename
                ext     = fname.rsplit(".", 1)[-1].lower() if "." in fname else "jpg"
                ctype   = mime_map.get(ext, "application/octet-stream")
                supplier = form.get(f"s{i}", "").strip()
                date_raw = form.get(f"d{i}", "").strip().replace("-", "")
                safe_sup = "".join(c for c in supplier if c.isalnum() or c in " -_")[:20].strip().replace(" ", "_")
                att_name = f"{safe_sup}_{date_raw}_receipt.{ext}" if safe_sup else fname
                xero_att = requests.put(
                    f"https://api.xero.com/api.xro/2.0/Invoices/{inv_id}/Attachments/{att_name}",
                    headers={
                        "Authorization":  f"Bearer {access_token}",
                        "Xero-tenant-id": tenant_id,
                        "Content-Type":   ctype,
                    },
                    data=receipt.read(),
                )
                if xero_att.status_code in (200, 201):
                    att_count += 1
                else:
                    att_failed += 1

        att_note = f" with {att_count} receipt(s) attached" if att_count else ""
        claim["Xero bill"] = inv_num
        claim["Receipts attached"] = str(att_count) if att_count else "None"
        warn = (f"⚠️ {att_failed} receipt(s) could not be attached in Xero — "
                f"please send them to Finance.") if att_failed else ""
        sent = notify(True, employee_email, claim, lines_summary, warn)
        msg = (
            f"Draft bill <strong>{escape(str(inv_num))}</strong> created in Xero "
            f"for <strong>{currency} {total:,.2f}</strong>{att_note}. "
            f"Go to <em>Xero → Bills to Pay</em> to review and approve."
            + (f"<br><br>{warn}" if warn else "")
            + ("<br><br>A confirmation email has been sent to you." if sent
               else "<br><br><small>We could not send you a confirmation email, "
                    "but your claim was submitted.</small>")
        )
        return render("success", msg)
    else:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        return fail(f"Xero rejected the claim ({resp.status_code}): {str(detail)[:400]}")

# ─── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("=" * 60)
    print("  RemotIQ Partners – Expense Tracker (PWA)")
    print("=" * 60)
    if XERO_CLIENT_ID == "YOUR_CLIENT_ID_HERE":
        print("\n  ⚠️  Set XERO_CLIENT_ID and XERO_CLIENT_SECRET first!")
        print("     Edit lines 37-38, or set as environment variables.")
        print("\n  Get credentials: https://developer.xero.com/app/manage")
    else:
        print("\n  ✅  Xero credentials loaded")
    print(f"\n  → Local:     http://localhost:{PORT}")
    print(f"  → Authorise: http://localhost:{PORT}/auth  (first run only)")
    print("\n  To enable mobile install, deploy to Railway/Render (HTTPS required).")
    print("=" * 60)
    print()
    app.run(debug=False, port=PORT, host="0.0.0.0")
