# jal-flights-tracker

Automated scraper that runs on a schedule to track JAL award availability for business-class flights between San Francisco and Tokyo. Logs into JAL JMB, fetches the OTP from Gmail, reads the Award Reservation Calendar, and writes results to a Google Sheet.

## Mission

Catch low-mileage business-class award tickets (ideal: 55,000 miles; acceptable: 70,000 miles) shortly after JAL releases inventory ~360 days in advance. Capture the data on every run so we can see trends and (later) trigger alerts.

## Architecture

The Playwright MCP drives a real Chrome browser (direct Python Playwright gets blocked by JAL's Akamai bot wall). Python helpers handle Gmail OTP fetching and Google Sheet writes.

- **Playwright MCP** — navigates JAL, fills forms, scrapes the calendar.
- **`python gmail_otp.py`** — polls Gmail (read-only scope) for the JAL OTP email, returns the code.
- **`sheet_client.py`** / **`python update_sheet.py`** — writes to the Google Sheet tabs `Snapshot` / `History` / `Alerts`.

## Two separate OTP gates

JAL requires two authentications in sequence:

1. **JMB login** (`AUTH_TYPE=AUTH_THREEKEY_LOW`). The persistent browser profile at `browser-state/` usually lets this through silently — if "MR. MICHELOTTI JUSTIN" and a logout link are visible on the JMB dashboard after visiting it, you're in.
2. **Booking auth** (`AUTH_TYPE=AUTH_ONETIME`). Triggered when you click Search on the award form. JAL sends a fresh OTP every time. There is no bypass.

Plan for both: fetch one OTP at step 2 regardless of whether step 1 asked.

## How to Run a Session

Create a task at the start with `TaskCreate`, mark it in_progress, then completed at the end.

### Step 1 — Load credentials and compute dates

Read `.env` and note `JMB_NUMBER` and `JMB_PASSWORD`. **Never print `JMB_PASSWORD` to stdout or include it in any response.**

Compute:
- `raw_outbound = today − 12 days`
- `outbound_year = raw_outbound.year + 1` (JAL's form has no year field; past month/day resolves to the next future occurrence)
- Month abbreviation for outbound (e.g. `Apr`) and day number (e.g. `2`) — these are what you type into the dropdowns
- Expected inbound = outbound + 4 days

Log the derived date (month, day, interpreted year) at the start.

### Step 2 — Log in (JMB)

```
mcp__playwright__browser_navigate → https://www.jal.co.jp/arl/en/jmb/
mcp__playwright__browser_snapshot
```

Find the **Login** link in the main content area (there are two — prefer the one inside a paragraph saying "You can log in here"). Click it.

On the login page:
- Ref for JMB number textbox is labelled "JMB membership number (7 or 9 digits)".
- Ref for password is labelled "Password".
- Submit button is labelled "Log in".

Fill both via `browser_fill_form`, then click Log in. If the resulting URL is `https://www.jal.co.jp/arl/en/jmb/` with "LOG OUT" visible, you're in without OTP. If the URL contains `AUTH_TYPE=AUTH_ONETIME`, the session was stale; fetch a step-1 OTP with `gmail_otp.py` (see Step 4 for the pattern) and submit it.

### Step 3 — Fill the award form

```
mcp__playwright__browser_navigate → https://www.jal.co.jp/arl/en/jmb/award/
```

Dismiss the cookie banner if present (`Allow all cookies`). Take a snapshot to get refs.

The form uses native `<select>` dropdowns. Expected defaults: **Round-trip already selected**, **Destination = Japan / City = Tokyo**, **Inbound From = Japan / Tokyo**, **Inbound Destination region = North America**, **Adult = 1**, **"member of this JMB account will travel"** checkbox checked. You only need to change what differs.

Select these via `browser_select_option`:

| Field | Value |
| --- | --- |
| Outbound Departure Date (month) | `Apr` (or whatever the computed month is, e.g. `Mar`) |
| Outbound Day | `2(Fri)` — match the full option text including `(Dow)` in the select option value |
| Outbound From City | `San Francisco` |
| Outbound Class | `Business Class` |
| Inbound Departure Date (month) | same as outbound (e.g. `Apr`) |
| Inbound Day | `6(Tue)` — outbound day + 4 |
| Inbound Destination City | `San Francisco` |
| Inbound Class | `Business Class` (this dropdown is disabled until Outbound Class is set) |

**Day option format note:** the `(Dow)` labels can be wrong — JAL sometimes shows two Saturdays in a row, etc. Select by the day number regardless of the dow suffix; trust the form to store the integer day.

Click **Search**.

### Step 4 — Booking OTP

You'll land on a page whose URL contains `AUTH_TYPE=AUTH_ONETIME` with title "One-Time Password (OTP)". JAL has just sent a code to `michelotti12@gmail.com`.

```
Bash: python -c "import time; print(int(time.time()*1000) - 30000)"    # since_ms
Bash: python gmail_otp.py --poll --since-ms <SINCE_MS> --timeout 60
```

`gmail_otp.py` prints JSON like `{"code": "123456", ...}`. Parse the code and fill the "One-time password(6 digits)" textbox. Click the Submit button.

If the Gmail poll fails, record the failure in the summary but do not retry — the OTP window closes quickly and the next scheduled run will try again. Do not log the code.

### Step 5 — Go to calendar

You're now on the availability page (URL contains `DDS_PREVIOUS_REQUEST_ID=0`). The page is huge; **don't use `browser_snapshot`** — it will return tens of kilobytes. Instead, click via evaluate:

```js
() => { document.getElementById('goToCalendarButton').click(); return 'clicked'; }
```

Wait ~3 seconds. The calendar page URL contains `DDS_PREVIOUS_REQUEST_ID=1` and title "Flight Selection: JAL International Booking".

### Step 6 — Scrape both calendar tables

Each calendar cell's text includes the fully qualified date like `Friday, April 2, 2027`, so you don't need to guess the year. Run this in `browser_evaluate` — it returns `{"SFO->HND": [...], "HND->SFO": [...]}` with parsed cells already shaped for the sheet:

```javascript
() => {
  const months = {January:1,February:2,March:3,April:4,May:5,June:6,July:7,August:8,September:9,October:10,November:11,December:12};
  function parseCell(text) {
    const d = text.match(/([A-Za-z]+), ([A-Za-z]+) (\d{1,2}), (\d{4})/);
    if (!d) return null;
    const flightDate = `${d[4]}-${String(months[d[2]]).padStart(2,'0')}-${String(parseInt(d[3])).padStart(2,'0')}`;
    const m = text.match(/([\d,]+)\s*Miles/i);
    const miles = m ? parseInt(m[1].replace(/,/g, '')) : 0;
    const tx = text.match(/\$\s*([\d.,]+)/);
    return {
      'Flight Date': flightDate,
      'Day of Week': d[1].slice(0,3),
      'Miles': miles,
      'Taxes': tx ? `$${tx[1]}` : '',
      'Combinable': !/not combinable/i.test(text),
      'Available': miles > 0 && !/not available/i.test(text)
    };
  }
  function classify(t) {
    let el = t;
    for (let i = 0; i < 12; i++) {
      el = el.previousElementSibling || el.parentElement;
      if (!el) return 'UNKNOWN';
      const h = (el.innerText || '').toLowerCase();
      if (!/san francisco|tokyo|sfo|hnd/.test(h)) continue;
      const sf = h.indexOf('san francisco'), tk = h.indexOf('tokyo');
      if (sf !== -1 && (tk === -1 || sf < tk)) return 'SFO->HND';
      if (tk !== -1 && (sf === -1 || tk < sf)) return 'HND->SFO';
      return 'UNKNOWN';
    }
    return 'UNKNOWN';
  }
  const tables = Array.from(document.querySelectorAll('table')).filter(t => {
    const txt = (t.innerText || '').toLowerCase();
    return txt.includes('miles') && /\d{1,3}(,\d{3})+/.test(t.innerText || '');
  });
  const out = {'SFO->HND': [], 'HND->SFO': []};
  tables.forEach(t => {
    const dir = classify(t);
    if (!out[dir]) out[dir] = [];
    t.querySelectorAll('tbody tr td').forEach(td => {
      const text = (td.innerText || td.textContent || '').trim();
      if (!text) return;
      const p = parseCell(text);
      if (p && p.Available) { delete p.Available; out[dir].push({...p, Direction: dir}); }
    });
  });
  return out;
}
```

Expect ~13–15 cells per direction. If either direction returns zero cells, something broke — log and abort.

### Step 7 — Write to the sheet

Combine both directions into one list. Invoke SheetClient directly from Python (cleaner than passing JSON through a shell):

```
python -c "
import json
from sheet_client import SheetClient
cells = <PASTE THE COMBINED CELL LIST AS JSON>
client = SheetClient()
print('Snapshot:', client.upsert_snapshot_bulk(cells))
print('History:', client.append_history_bulk(cells))
alerts = []
for c in cells:
    m = c['Miles']
    if m <= 55000: alerts.append({**c, 'Threshold Hit': '55k'})
    elif m <= 70000: alerts.append({**c, 'Threshold Hit': '70k'})
print('Alerts:', client.append_alerts(alerts))
print('Alert rows:', json.dumps(alerts, indent=2))
"
```

If a sheet call errors, log it and continue — missed `Snapshot` upserts can be reconstructed from `History`.

### Step 8 — Close the browser and print summary

```
mcp__playwright__browser_close
```

Print a summary with:
- Outbound min miles and best date
- Inbound min miles and best date
- Alert count and each alert's direction + date + miles + tier
- Rows inserted/updated/appended

## Google Sheet schema

URL: `https://docs.google.com/spreadsheets/d/10ZbERCCjfLb9_ERhq-KkSXwoqss9BbXJHf_YOzEmA4c`

- **`Snapshot`** (one row per `Direction` + `Flight Date`, upserted): `Direction`, `Flight Date`, `Day of Week`, `Miles`, `Taxes`, `Combinable`, `Lowest Miles Ever`, `Lowest Miles Date Seen`, `First Seen`, `Last Scanned`.
- **`History`** (append-only): `Scan Time`, `Direction`, `Flight Date`, `Miles`, `Taxes`, `Combinable`.
- **`Alerts`** (append-only): `Scan Time`, `Direction`, `Flight Date`, `Miles`, `Taxes`, `Threshold Hit`, `Emailed`.

`update_sheet.py init` was run once. If headers get wiped, it's idempotent.

## Secrets and config

- `.env` — `JMB_NUMBER`, `JMB_PASSWORD`, `ALERT_THRESHOLD_MILES`. Never echo `JMB_PASSWORD`.
- `secrets/sa.json` — Google Sheets service account (same key as pc-deal-tracker).
- `secrets/gmail-credentials.json` — OAuth client for Gmail API.
- `secrets/gmail-token.json` — Refresh token for `michelotti12@gmail.com`, read-only Gmail scope. If it goes missing, run `python gmail_otp.py --auth` interactively.
- `browser-state/` — Persistent Chromium profile. Surviving this across runs is how we sometimes skip the step-1 JMB OTP. Never commit.

All of `secrets/`, `.env`, `browser-state/`, `failures/`, and `tracker-log.txt` are gitignored.

## Failure handling

When a step fails unexpectedly:
1. `mcp__playwright__browser_take_screenshot` and save under `failures/<timestamp>-<step>.png`.
2. Save `document.documentElement.outerHTML` via evaluate to `failures/<timestamp>-<step>.html`.
3. Log the error and the paths in the summary.
4. Do not retry blindly — surface the failure so the next debugging session can learn.

## Tasks

Use `TaskCreate`/`TaskUpdate` for each run. Create one "Run JAL tracker session" task at the start, mark it in_progress, mark completed at the end. Add subtasks only if debugging.

## What NOT to do

- Never commit anything under `secrets/`, `browser-state/`, `failures/`, or `.env`.
- Never log `JMB_PASSWORD` or the OTP code digits.
- Never broaden the Gmail OAuth scope beyond `gmail.readonly`.
- Do not send email from this tool. Alerting is a future phase and will use a separate mechanism.
- Do not create new Python source files or markdown docs beyond what exists. Surface area is fixed: `gmail_otp.py`, `sheet_client.py`, `update_sheet.py`, `CLAUDE.md`, `SCHEDULER-SETUP.md`.
- Do not commit or push to git from inside a scheduled session.
- Do not use `mcp__playwright__browser_snapshot` on the availability page or calendar page — the DOM is too large. Use `browser_evaluate` with targeted queries instead.
