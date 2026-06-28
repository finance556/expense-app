#!/usr/bin/env python3
"""
RemotIQ Partners – Expense Submission App  (PWA edition)
=========================================================
Cross-platform: works as an installable app on iOS, Android, Mac, and Windows.

ACCOUNTS SUPPORTED
  493 – Travel National
  420 – Entertainment

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

import os, json, secrets
from datetime import datetime, timedelta

import requests
from flask import Flask, request, redirect, session, render_template_string, url_for, Response, jsonify

# ─── Configuration ─────────────────────────────────────────────────────────────
XERO_CLIENT_ID     = os.environ.get("XERO_CLIENT_ID",     "YOUR_CLIENT_ID_HERE")
XERO_CLIENT_SECRET = os.environ.get("XERO_CLIENT_SECRET", "YOUR_CLIENT_SECRET_HERE")
XERO_REDIRECT_URI  = os.environ.get("XERO_REDIRECT_URI",  "http://localhost:5000/callback")
XERO_SCOPES        = "openid profile email accounting.contacts accounting.invoices offline_access"
TOKEN_FILE         = "xero_tokens.json"
PORT               = int(os.environ.get("PORT", 5000))

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
    "theme_color": "#1a3a5c",
    "orientation": "portrait-primary",
    "icons": [
        {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
    ]
}

# ─── SVG App Icon ──────────────────────────────────────────────────────────────
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="36" fill="#1a3a5c"/>
  <rect x="52" y="36" width="88" height="120" rx="10" fill="none" stroke="#fff" stroke-width="8"/>
  <rect x="72" y="26" width="48" height="24" rx="6" fill="#1a3a5c" stroke="#fff" stroke-width="8"/>
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
<meta name="theme-color" content="#1a3a5c">
<meta name="description" content="RemotIQ Partners expense submission">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon.svg">
<title>Expense Submission – RemotIQ Partners</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --brand:   #1a3a5c;
    --brand-d: #0f2640;
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
  .topbar .sub { font-size: 12px; opacity: .65; margin-left: auto; }

  /* ── Install banner ── */
  #install-banner {
    display: none; background: #1a3a5c; color: #fff;
    padding: 10px 16px; text-align: center; font-size: 13px;
    align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap;
  }
  #install-banner button {
    background: #fff; color: #1a3a5c; border: none; border-radius: 20px;
    padding: 5px 14px; font-size: 12px; font-weight: 700; cursor: pointer; }
  #install-banner .dismiss { background: transparent; color: rgba(255,255,255,.6);
    font-size: 12px; text-decoration: underline; }

  /* ── Page ── */
  .page { max-width: 720px; margin: 0 auto; padding: 16px 14px calc(var(--safe-bot) + 40px); }

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
  input[type=text], input[type=email], input[type=date],
  input[type=number], select {
    border: 1.5px solid var(--border); border-radius: 8px;
    padding: 12px 14px; font-size: 16px; /* 16px prevents iOS zoom */
    color: var(--text); background: #fafbfc; outline: none; width: 100%;
    -webkit-appearance: none; appearance: none;
    transition: border-color .15s, box-shadow .15s; }
  input:focus, select:focus {
    border-color: var(--brand); background: #fff;
    box-shadow: 0 0 0 3px rgba(26,58,92,.12); }
  input::placeholder { color: #b0bec5; }
  select { background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23718096' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
           background-repeat: no-repeat; background-position: right 14px center;
           padding-right: 38px; }

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
  .total-bar { background: linear-gradient(135deg, var(--brand) 0%, var(--brand-d) 100%);
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
  .btn-submit { width: 100%; background: linear-gradient(135deg, var(--brand), var(--brand-d));
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
    table { width: 100%; border-collapse: collapse; }
    thead { background: #f7f9fc; }
    th { font-size: 11px; font-weight: 700; text-transform: uppercase;
         letter-spacing: .06em; color: var(--muted); padding: 11px 12px;
         text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
    tbody tr { border-bottom: 1px solid #f0f4f8; }
    tbody tr:last-child { border-bottom: none; }
    td { padding: 6px 6px; vertical-align: middle; }
    td input, td select { background: transparent; border: 1px solid transparent;
      border-radius: 5px; padding: 7px 9px; font-size: 13px; width: 100%; }
    td input:hover, td select:hover { border-color: var(--border); background: #fff; }
    td input:focus, td select:focus { border-color: var(--brand); background: #fff;
      box-shadow: 0 0 0 2px rgba(26,58,92,.08); outline: none; }
    .totals-bar { background: #f7f9fc; border-top: 1px solid var(--border);
                  padding: 14px 20px; display: flex; justify-content: flex-end;
                  align-items: center; gap: 14px; }
    .totals-label  { font-size: 12px; font-weight: 700; color: var(--muted);
                     text-transform: uppercase; letter-spacing: .06em; }
    .totals-amount { font-size: 22px; font-weight: 800; color: var(--brand); }
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
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
       stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2"/>
    <rect x="9" y="3" width="6" height="4" rx="1"/>
    <line x1="9" y1="12" x2="15" y2="12"/>
    <line x1="9" y1="16" x2="13" y2="16"/>
  </svg>
  <h1>RemotIQ Partners</h1>
  <span class="sub">Expense Submission</span>
</div>

<div class="page">

{% if state == "needs_auth" %}
<div class="card">
  <div class="status-page">
    <div class="status-icon">🔗</div>
    <h2 style="color:#1a3a5c">Connect Xero</h2>
    <p>Authorise once so that submitted expenses are forwarded to Xero as Draft Bills automatically.</p>
    <a href="/auth" class="btn-back">Connect to Xero →</a>
  </div>
</div>

{% elif state == "success" %}
<div class="card">
  <div class="status-page success">
    <div class="status-icon">✅</div>
    <h2>Expenses Submitted!</h2>
    <p>{{ message }}</p>
    <a href="/" class="btn-back">Submit Another Claim</a>
  </div>
</div>

{% elif state == "error" %}
<div class="card">
  <div class="status-page error">
    <div class="status-icon">⚠️</div>
    <h2>Submission Error</h2>
    <p>{{ message }}</p>
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
      <label>Full Name *</label>
      <input type="text" name="employee_name" required placeholder="Your full name"
             autocomplete="name" autocapitalize="words">
    </div>
    <div class="field">
      <label>Email Address *</label>
      <input type="email" name="employee_email" required placeholder="you@remotiqpartners.com"
             autocomplete="email" inputmode="email">
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
      <label>Purpose / Notes</label>
      <input type="text" name="notes" placeholder="e.g. Q3 client visit expenses"
             autocapitalize="sentences">
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
            <th style="width:108px">Date *</th>
            <th style="width:195px">Account</th>
            <th style="width:125px">Supplier / Vendor *</th>
            <th>Description *</th>
            <th style="width:110px">TIN (optional)</th>
            <th style="width:105px;text-align:right">Amount *</th>
            <th style="width:110px">Receipt</th>
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

// ── Expense lines ───────────────────────────────────────────────────────────
let idx = 0;
const isMobile = () => window.innerWidth < 700;

function accountOptions(sel) {
  return `<option value="">Select account…</option>
          <option value="420" ${sel==='420'?'selected':''}>420 – Entertainment</option>
          <option value="421" ${sel==='421'?'selected':''}>421 – Engagement &amp; Training</option>
          <option value="489" ${sel==='489'?'selected':''}>489 – Telephone</option>
          <option value="493" ${sel==='493'?'selected':''}>493 – Travel National</option>
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
      <label>TIN (optional)</label>
      <input type="text" name="tin${i}" value="${tin||''}" placeholder="e.g. 123-456-789"
             autocapitalize="none" inputmode="numeric">
    </div>
    <div class="field">
      <label>Amount *</label>
      <input type="number" name="amt${i}" value="${amt||''}" placeholder="0.00"
             step="0.01" min="0.01" inputmode="decimal" required
             oninput="calcTotal()">
    </div>
    <div class="field">
      <label>Receipt (optional)</label>
      <input type="file" name="receipt${i}" accept="image/*,.pdf" capture="environment"
             style="font-size:13px">
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
    <td><input type="text"   name="tin${i}"  value="${tin||''}"  placeholder="TIN (optional)"></td>
    <td><input type="number" name="amt${i}"  value="${amt||''}"  placeholder="0.00"
               step="0.01" min="0.01" required style="text-align:right" oninput="calcTotal()"></td>
    <td><input type="file" name="receipt${i}" accept="image/*,.pdf" style="font-size:11px;width:100%"></td>
    <td><button type="button" class="btn-del" onclick="removeLine(${i})" title="Remove">×</button></td>
  `;
  document.getElementById('desktop-rows').appendChild(tr);

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
  // Sum unique field names (mobile and desktop share the same name, so dedup)
  const seen = new Set();
  let t = 0;
  document.querySelectorAll('[name^="amt"]').forEach(el => {
    if (!seen.has(el.name)) {
      seen.add(el.name);
      t += parseFloat(el.value) || 0;
    }
  });
  const fmt = currencySymbol() + t.toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.getElementById('mobile-total').textContent  = fmt;
  document.getElementById('desktop-total').textContent = fmt;
}

// ── Init ────────────────────────────────────────────────────────────────────
document.getElementById('sub_date').value = new Date().toISOString().slice(0, 10);
addLine();

document.getElementById('expForm').addEventListener('submit', function(e) {
  const cards = document.querySelectorAll('.exp-card');
  const rows  = document.querySelectorAll('#desktop-rows tr');
  if (!cards.length && !rows.length) {
    e.preventDefault();
    alert('Please add at least one expense line.');
    return;
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
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)

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

def get_tenant_id(access_token):
    r = requests.get(
        "https://api.xero.com/connections",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    r.raise_for_status()
    conns = r.json()
    return conns[0]["tenantId"] if conns else None

def render(state="form", message=""):
    return render_template_string(TEMPLATE, state=state, message=message)

# ─── PWA asset routes ──────────────────────────────────────────────────────────

@app.route("/manifest.json")
def manifest():
    return jsonify(MANIFEST)

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
        return render("needs_auth")
    return render("form")

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
    tokens = load_tokens()
    if not tokens:
        return redirect(url_for("auth"))

    try:
        tokens = refresh_if_needed(tokens)
    except Exception as e:
        return render("error", f"Xero token refresh failed: {e}")

    access_token = tokens["access_token"]

    try:
        tenant_id = get_tenant_id(access_token)
    except Exception as e:
        return render("error", f"Could not reach your Xero organisation: {e}")

    if not tenant_id:
        return render("error", "No Xero organisation found. Please reconnect at /auth.")

    # ── Parse employee fields ──
    employee_name   = request.form.get("employee_name",   "").strip()
    submission_date = request.form.get("submission_date") or datetime.now().strftime("%Y-%m-%d")
    notes           = request.form.get("notes", "").strip()
    currency        = request.form.get("currency", "PHP").strip().upper() or "PHP"

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
        try:
            amount = round(float(form.get(f"amt{i}", "0")), 2)
        except ValueError:
            continue
        if not (date and amount > 0):
            continue

        full_desc = desc
        if supplier:
            full_desc += f" — {supplier}"
        if tin:
            full_desc += f" | TIN: {tin}"
        full_desc += f" ({date})"

        # Use account code if one of the supported codes; leave blank for "other"
        line = {
            "Description": full_desc,
            "Quantity":    1,
            "UnitAmount":  amount,
        }
        if account and account != "other":
            line["AccountCode"] = account

        line_items.append(line)

    if not line_items:
        return render("error", "No valid expense lines found. Please go back and check your entries.")

    # ── Xero draft bill payload ──
    first = employee_name.split()[0].upper() if employee_name else "EMP"
    ref   = f"EXP-{first}-{submission_date.replace('-', '')}"
    due   = (datetime.strptime(submission_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")

    payload = {
        "Invoices": [{
            "Type":            "ACCPAY",
            "Status":          "DRAFT",
            "Contact":         {"Name": employee_name},
            "Date":            submission_date,
            "DueDate":         due,
            "Reference":       ref,
            "CurrencyCode":    currency,
            "LineAmountTypes": "Inclusive",
            "LineItems":       line_items,
            **({"Narrative": notes} if notes else {}),
        }]
    }

    resp = requests.post(
        "https://api.xero.com/api.xro/2.0/Invoices",
        headers={
            "Authorization":  f"Bearer {access_token}",
            "Xero-tenant-id": tenant_id,
            "Content-Type":   "application/json",
            "Accept":         "application/json",
        },
        json=payload,
    )

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
        att_count = 0
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

        att_note = f" with {att_count} receipt(s) attached" if att_count else ""
        msg = (
            f"Draft bill <strong>{inv_num}</strong> created in Xero "
            f"for <strong>${total:,.2f}</strong>{att_note}. "
            f"Go to <em>Xero → Bills to Pay</em> to review and approve."
        )
        return render("success", msg)
    else:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        return render("error", f"Xero API error ({resp.status_code}): {str(detail)[:400]}")

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
