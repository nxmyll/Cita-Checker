#!/usr/bin/env python3
"""
Playwright-based Cita Checker (non-evasive replacement for Selenium-based checker)

- Uses Playwright sync API and a persistent context (temporary user-data-dir) to avoid
  DevToolsActivePort/profile issues in CI.
- Saves artifacts (last_page.png, last_page.html, error.png) on errors for debugging.
- Detects known "no citas" site message and common CAPTCHA markers and sends Telegram alerts.
- Respects FORCE_RUN env var and Madrid working hours like the original script.
"""
from __future__ import annotations

import os
import sys
import time
import tempfile
import shutil
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeoutError, Error as PlayError

# ============== CONFIG ==============
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DOC_NUMBER = os.environ.get("DOC_NUMBER")
FULL_NAME = os.environ.get("FULL_NAME")
NATIONALITY = os.environ.get("NATIONALITY")

BASE_URL = "https://icp.administracionelectronica.gob.es/icpplus/index.html"

OFICINA_VALUE = "99"
TRAMITE_VALUE = "4010"
NATIONALITY_VALUE = "424"
NO_CITAS_PHRASE = "no ofrece el servicio de Cita Previa Internet"

ACTIVE_WINDOW_START_HOUR = 8
ACTIVE_WINDOW_END_HOUR = 16
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds

# Artifact filenames
ERROR_SCREENSHOT = os.environ.get("ERROR_SCREENSHOT_PATH", "error.png")
ERROR_PAGE_SOURCE = os.environ.get("ERROR_PAGE_SOURCE", "page_source.html")
LAST_PAGE_PNG = os.environ.get("LAST_PAGE_PNG", "last_page.png")
LAST_PAGE_HTML = os.environ.get("LAST_PAGE_HTML", "last_page.html")
# =====================================


def send_telegram_alert(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=data, timeout=15)
        print(f"📩 Telegram: {r.status_code} — {'OK' if r.ok else r.text}")
        return r.ok
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        return False


def within_active_window() -> bool:
    now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))
    if now_madrid.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    return ACTIVE_WINDOW_START_HOUR <= now_madrid.hour < ACTIVE_WINDOW_END_HOUR


def human_delay(min_s: float = 0.25, max_s: float = 0.9):
    time.sleep(random.uniform(min_s, max_s))


def run_single_check() -> bool:
    """Run a single Playwright-based check. Returns True if availability or captcha detected (alerts sent), False if no slots or an error."""
    user_data_dir = os.environ.get("PLAYWRIGHT_USER_DATA_DIR") or tempfile.mkdtemp(prefix="pw-user-data-")
    created_temp_dir = os.environ.get("PLAYWRIGHT_USER_DATA_DIR") is None

    try:
        with sync_playwright() as p:
            browser_type = p.chromium

            # Launch a persistent context so the browser uses a writable profile directory
            context = browser_type.launch_persistent_context(
                user_data_dir,
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-setuid-sandbox",
                    "--single-process",
                ],
                viewport={"width": 1280, "height": 900},
                locale="es-ES",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
            )

            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(30000)  # 30s

            print("Loading homepage...", flush=True)
            try:
                page.goto(BASE_URL, wait_until="networkidle")
            except (PlayTimeoutError, PlayError) as e:
                print("Homepage navigation timed out or errored:", e, flush=True)

            # allow JS to run
            time.sleep(1.5)

            # Step 1: select office and procedure
            print("Selecting office and procedure...", flush=True)
            try:
                # Use CSS selectors; select_option expects the select's value attribute
                page.select_option("#sede", OFICINA_VALUE)
                human_delay()
                # escape square brackets in selector for tramiteGrupo[0]
                page.select_option("#tramiteGrupo\\[0\\]", TRAMITE_VALUE)
                human_delay()
                page.click("#btnAceptar")
            except PlayError as e:
                print("Error interacting with Step 1 elements:", e, flush=True)
                try:
                    page.screenshot(path=ERROR_SCREENSHOT, full_page=True)
                    with open(ERROR_PAGE_SOURCE, "w", encoding="utf-8") as f:
                        f.write(page.content())
                    print("Saved debug artifacts after Step 1 failure.", flush=True)
                except Exception:
                    pass
                context.close()
                return False

            # Step 2: continue without Cl@ve
            try:
                page.wait_for_selector("#btnEntrar", timeout=10000)
                human_delay()
                page.click("#btnEntrar")
            except PlayTimeoutError:
                print("btnEntrar not found; proceeding", flush=True)
            except PlayError as e:
                print("Error clicking btnEntrar:", e, flush=True)

            # Step 3: Fill personal details
            try:
                page.wait_for_selector("#txtIdCitado", timeout=10000)
                page.fill("#txtIdCitado", DOC_NUMBER or "")
                human_delay()
                page.fill("#txtDesCitado", FULL_NAME or "")
                human_delay()
                page.select_option("#txtPaisNac", NATIONALITY_VALUE)
                human_delay()
                page.click("#btnEnviar")
            except PlayError as e:
                print("Error on Step 3 interactions:", e, flush=True)
                try:
                    page.screenshot(path=ERROR_SCREENSHOT, full_page=True)
                    with open(ERROR_PAGE_SOURCE, "w", encoding="utf-8") as f:
                        f.write(page.content())
                    print("Saved debug artifacts after Step 3 failure.", flush=True)
                except Exception:
                    pass
                context.close()
                return False

            # Wait for result page to settle
            page.wait_for_timeout(2500)
            content = page.content()

            # Save last page artifacts
            try:
                page.screenshot(path=LAST_PAGE_PNG, full_page=True)
                with open(LAST_PAGE_HTML, "w", encoding="utf-8") as f:
                    f.write(content)
                print("Saved last_page artifacts.", flush=True)
            except Exception as e:
                print("Failed to save last page artifacts:", e, flush=True)

            # Check for known "no citas" text
            if NO_CITAS_PHRASE in content:
                print("No slots — known site message found.", flush=True)
                context.close()
                return False

            # Detect captcha heuristically
            captcha_selectors = ["#captcha", "iframe[src*='captcha']", "[id*=captcha]"]
            captcha_found = False
            for sel in captcha_selectors:
                try:
                    if page.query_selector(sel):
                        captcha_found = True
                        break
                except Exception:
                    pass

            if captcha_found:
                print("CAPTCHA detected — sending alert.", flush=True)
                send_telegram_alert(
                    "⚠️ Posible cita disponible pero hay un CAPTCHA que requiere verificación manual. Revisa: " + BASE_URL
                )
                context.close()
                return True

            # If neither no-citas nor captcha, assume possible availability
            print("Unexpected page state — possible availability. Sending alert.", flush=True)
            send_telegram_alert("🚨 Posible disponibilidad detectada — revisa manualmente: " + BASE_URL)
            context.close()
            return True

    except Exception as e:
        print("❌ Playwright error:", e, flush=True)
        try:
            # Attempt to save a short message as page source for debugging
            with open(ERROR_PAGE_SOURCE, "w", encoding="utf-8") as f:
                f.write(str(e))
        except Exception:
            pass
        return False

    finally:
        # cleanup temporary user-data-dir if we created it
        if created_temp_dir:
            try:
                shutil.rmtree(user_data_dir)
                print(f"Removed temporary user-data-dir: {user_data_dir}", flush=True)
            except Exception as e:
                print("Could not remove user-data-dir:", e, flush=True)


def run_with_retry() -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        print("\n" + "=" * 50)
        print(f"Attempt {attempt}/{MAX_RETRIES}")
        print("=" * 50 + "\n", flush=True)
        ok = run_single_check()
        if ok:
            return True
        else:
            if attempt < MAX_RETRIES:
                print(f"Waiting {RETRY_DELAY}s before retry...", flush=True)
                time.sleep(RETRY_DELAY)
            else:
                print("All retry attempts exhausted.", flush=True)
                return False
    return False


if __name__ == "__main__":
    now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))
    force_run = os.environ.get("FORCE_RUN", "").lower() == "true"

    if not force_run and not within_active_window():
        print(f"[{now_madrid.strftime('%Y-%m-%d %H:%M:%S')}] Outside configured Madrid-time window — skipping")
        sys.exit(0)

    if force_run:
        print(f"[{now_madrid.strftime('%Y-%m-%d %H:%M:%S')}] FORCE_RUN set — running check regardless of time window")
    else:
        print(f"[{now_madrid.strftime('%Y-%m-%d %H:%M:%S')}] Within active window — running check")

    try:
        result = run_with_retry()
    except Exception as e:
        print(f"❌ Fatal error after all retries: {e}", flush=True)
        sys.exit(1)

    if not result:
        print("❌ Check finished but reported failure (no availability / browser issue). Exiting with non-zero code.", flush=True)
        sys.exit(1)

    print("✅ Check finished and reported success.")
    sys.exit(0)
