# Cita Previa Checker

Checks the Spanish government appointment site for available fingerprinting
(TIE/NIE) slots in Barcelona province, Monday–Friday 8am–4pm Spanish time,
and pings you on Telegram the moment something looks available.

**It does not auto-book** — if a slot appears, the alert tells you to open
the site and finish the booking yourself, fast.

## What it actually does

The script drives a real (headless) Chromium browser through the exact flow
a person would use:

1. Opens the site, selects **Barcelona → "Cualquier oficina"** (any office)
   and the **POLICÍA-TOMA DE HUELLAS...** procedure
2. Clicks through the info/terms page using **"Presentación sin Cl@ve"**
   (no digital certificate needed)
3. Fills in your NIE, full name, and nationality
4. Submits, and reads the result:
   - If the page shows *"no ofrece el servicio de Cita Previa Internet"* →
     no slots, exits quietly
   - If a CAPTCHA appears → can't solve it (no script can), sends you a
     Telegram alert to check manually, since a CAPTCHA at this stage may
     mean slots exist
   - Anything else unexpected → also alerts you, on the assumption that a
     surprise is more likely to be real availability (or a site change)
     than something safe to ignore

## A known limitation: anti-bot protection

This site runs bot-detection middleware (TSPD/F5) that shows a blank
JavaScript-challenge page to automated traffic before letting it through.
A real browser resolves this invisibly. A headless Selenium browser
*might* get flagged and stuck on that challenge page instead of reaching
the real form — in which case the script will alert you (treating the
unrecognized page as "unexpected") rather than fail silently.

**If you start getting frequent alerts that turn out to be false alarms**,
that's the signal this protection is blocking the automated run itself,
not that slots have opened. There's no guaranteed fix for this — it's a
tradeoff of automating a page designed to resist automation.

## 1. Files in this repo

`cita_checker.py`, `requirements.txt`, `Dockerfile`, `README.md`

## 2. Running it: GitHub Actions (free, no credit card needed)

Create `.github/workflows/checker.yml` in this repo:

```yaml
name: Cita Checker

on:
  schedule:
    - cron: '*/5 6-15 * * 1-5'
  workflow_dispatch:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install Chromium
        run: |
          sudo apt-get update
          sudo apt-get install -y chromium-browser chromium-chromedriver
      - run: pip install -r requirements.txt
      - run: python cita_checker.py
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          DOC_NUMBER: ${{ secrets.DOC_NUMBER }}
          FULL_NAME: ${{ secrets.FULL_NAME }}
          NATIONALITY: ${{ secrets.NATIONALITY }}
          CHROME_BIN: /usr/bin/chromium-browser
          CHROMEDRIVER_PATH: /usr/bin/chromedriver
```

**Add secrets**: repo **Settings → Secrets and variables → Actions → New
repository secret**, one each for `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`DOC_NUMBER`, `FULL_NAME`, `NATIONALITY`.

The `*/5 6-15 * * 1-5` schedule runs every 5 minutes, 06:00–15:59 UTC,
Monday–Friday — covering 8am–4pm Madrid time in both winter (UTC+1) and
summer (UTC+2) DST, with the script's own Madrid-time check discarding runs
outside the exact window.

Test it manually first: **Actions tab → Cita Checker → Run workflow**, then
check the logs.

## 3. Alternative: Render Cron Job (needs a payment method on file)

Render requires a card on the account before creating any service, even
free-tier ones. If you add one:

- **New → Cron Job** → connect this repo → Render detects the `Dockerfile`
- **Schedule** (UTC): `*/5 6-15 * * 1-5`
- **Environment variables**: same five as above, set in the Render
  dashboard's Environment tab — never hardcoded in the repo

## 4. If it stops working

- Government sites like this change their HTML periodically. If logs show
  "Unexpected page state" on every run with no real availability, the
  element IDs (`sede`, `tramiteGrupo[0]`, `txtIdCitado`, etc.) or the
  no-citas phrase in `cita_checker.py` may need updating — open the site
  manually, inspect the relevant page, and compare against the constants
  at the top of the script.
- Frequent false-positive alerts are more likely the anti-bot wall
  blocking automation than real availability — see the limitation above.

## Practical notes

- This checks your **own** appointment, for personal use — not for
  reselling slots.
- If your Telegram bot token was ever shared or pasted somewhere public,
  regenerate it via @BotFather and update the secret/environment variable.
- Your NIE, name, and nationality are read from environment variables /
  GitHub Secrets at runtime only — never commit them into the script or
  any file in this repo.
