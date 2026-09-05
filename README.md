# Cita Previa Checker — Render Cron Job setup

Checks the Spanish government appointment site for available fingerprinting
(TIE/NIE) slots in Barcelona province, Monday–Friday 8am–4pm Spanish time,
and pings you on Telegram the moment something looks available. **It does
not auto-book** — the site's CAPTCHA prevents that. When you get the alert,
open the link yourself and finish the booking fast.

## Why "cron mode" instead of a always-running worker

Render Cron Jobs only bill for the seconds they actually run, which fits
"only active 8am–4pm weekdays" much better than a 24/7 worker. The catch:
**Render cron schedules are UTC-only**, and Spain shifts between UTC+1
(winter) and UTC+2 (summer/DST). Rather than editing the schedule twice a
year, the script itself checks the real Madrid wall-clock time on every run
and instantly exits if it's outside your window — so the Render schedule
just needs to be *generous enough* to cover both DST cases, and the script
does the precise filtering.

## 1. Put this code in a Git repo

Create a new GitHub repo (private is fine) and push these four files:
`cita_checker.py`, `requirements.txt`, `Dockerfile`, `README.md`.

## 2. Create the Cron Job on Render

In the Render dashboard: **New → Cron Job** → connect the repo you just
created → Render will detect the `Dockerfile` and build from it.

**Schedule** (this field is UTC): use
```
*/10 6-15 * * 1-5
```
This runs every 10 minutes, 06:00–15:59 UTC, Monday–Friday — which covers
8am–4pm Madrid time in both winter (UTC+1 → 07:00–15:00 UTC) and summer
(UTC+2 → 06:00–14:00 UTC), with the script's internal check discarding the
extra runs outside your exact window. Outside this UTC range, the job
doesn't run at all, so there's no cost or Telegram noise overnight/weekends.

**Environment variables** — add these in the Render service's Environment
tab (not in the code):
| Key | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your bot token |
| `TELEGRAM_CHAT_ID` | your chat id |
| `DOC_NUMBER` | your passport/NIE number |
| `FULL_NAME` | your full name |
| `NATIONALITY` | your nationality |

Keeping these as environment variables (rather than hardcoded in the repo)
means they're not sitting in plaintext in your Git history.

## 3. Deploy and watch the logs

Render will run the job on schedule automatically. Check the **Logs** tab
after the next scheduled run to confirm it says either "Outside configured
Madrid-time window — skipping" (if triggered outside 8–16) or "Within
active window — running check."

## If it stops working

Government sites like this change their HTML periodically. If logs start
showing "unexpected page" on every in-window run, the CSS/element IDs in
`run_single_check()` in `cita_checker.py` likely need updating — open the
site manually in a browser, inspect the relevant dropdown/button with
DevTools, and update the matching `By.ID` / visible-text values.

## Practical notes

- This checks your **own** appointment, for personal use — not for
  reselling slots.
- If your Telegram bot token was ever shared outside your own devices,
  regenerate it via @BotFather and update the Render environment variable.
