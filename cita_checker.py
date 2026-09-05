#!/usr/bin/env python3
"""
Cita Previa Checker - Extranjería (Barcelona - Toma de huellas TIE/NIE)
========================================================================
CRON MODE — designed to be triggered repeatedly by Render Cron Jobs (or
any external scheduler / cron), not to run as a long-lived process itself.

WHAT THIS DOES
--------------
Each time it's run, it loads the Spanish government "cita previa"
appointment site once, walks through the province/office/procedure
selection, and checks whether appointment slots are available. If it
finds slots (or hits something it doesn't recognize, like a CAPTCHA it
can't get past), it sends a Telegram message so you can jump in and
finish the booking yourself. Then it exits.

WHAT THIS DOES NOT DO
----------------------
It does NOT auto-book the appointment and does NOT try to bypass
CAPTCHAs -- the site puts a CAPTCHA in front of the booking step
specifically to stop full automation, and this script respects that.

SCHEDULING / TIME WINDOW
--------------------------
Render Cron Jobs run on UTC-only schedules with no timezone support, and
Spain switches between CET (UTC+1) and CEST (UTC+2) with daylight saving.
Rather than hand-adjust a UTC cron expression twice a year, this script
checks the CURRENT wall-clock time in Europe/Madrid on every invocation
and immediately exits (no site hit, no Telegram spam) if it's outside
your desired window. Set the schedule on Render to fire more often than
you actually want checks (e.g. every 10 minutes, across a UTC range wide
enough to cover both DST offsets) and let WINDOW_* below do the precise
filtering. See README.md for the exact Render cron expression to use.

ENVIRONMENT VARIABLES (set these in Render's dashboard, not in code)
-----------------------------------------------------------------------
  TELEGRAM_BOT_TOKEN     - your bot token
  TELEGRAM_CHAT_ID       - your chat id
  DOC_NUMBER             - your passport/NIE number
  FULL_NAME              - your full name
  NATIONALITY            - your nationality

Local fallback defaults are given below for testing on your own machine,
but on Render, set these as environment variables in the service
settings instead of committing them to the repo.
"""

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

# ============================== CONFIG ================================

@dataclass
class Config:
    # --- Telegram (from environment, falls back to placeholder for local testing) ---
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")

    # --- Site / procedure ---
    BASE_URL: str = "https://icp.administracionelectronica.gob.es/icpplus/index.html"
    PROVINCE_NAME: str = "Barcelona"
    TRAMITE_TEXT: str = "POLICIA-TOMA DE HUELLAS (EXPEDICION DE TARJETA) Y RENOVACION DE TARJETA DE LARGA DURACION"
    OFFICE_NAME: str = ""  # blank = any office in the province, if the site allows it

    # --- Personal details (from environment) ---
    DOC_TYPE: str = "PASAPORTE"  # or "NIE"
    DOC_NUMBER: str = os.getenv("DOC_NUMBER", "PUT_YOUR_PASSPORT_OR_NIE_HERE")
    FULL_NAME: str = os.getenv("FULL_NAME", "PUT_YOUR_FULL_NAME_HERE")
    NATIONALITY: str = os.getenv("NATIONALITY", "PUT_YOUR_NATIONALITY_HERE")

    # --- Desired active window (checked in Europe/Madrid wall-clock time) ---
    WINDOW_TZ: str = "Europe/Madrid"
    WINDOW_START_HOUR: int = 8   # 8am inclusive
    WINDOW_END_HOUR: int = 16    # 4pm exclusive
    WINDOW_WEEKDAYS: tuple = (0, 1, 2, 3, 4)  # Monday=0 ... Sunday=6 (Mon-Fri)

    PAGE_LOAD_TIMEOUT: int = 25
    HEADLESS: bool = True
    CHROME_BIN: str = os.getenv("CHROME_BIN", "")
    CHROMEDRIVER_PATH: str = os.getenv("CHROMEDRIVER_PATH", "")


CFG = Config()

# ============================== LOGGING ================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("cita_checker")


# ============================== TIME WINDOW ================================

def is_within_window() -> bool:
    now_madrid = datetime.now(ZoneInfo(CFG.WINDOW_TZ))
    if now_madrid.weekday() not in CFG.WINDOW_WEEKDAYS:
        return False
    return CFG.WINDOW_START_HOUR <= now_madrid.hour < CFG.WINDOW_END_HOUR


# ============================== TELEGRAM ================================

def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{CFG.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url, data={"chat_id": CFG.TELEGRAM_CHAT_ID, "text": message}, timeout=15
        )
        if not resp.ok:
            log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
    except requests.RequestException as e:
        log.error("Telegram send exception: %s", e)


# ============================== BROWSER SETUP ================================

def build_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if CFG.HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,1024")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    if CFG.CHROME_BIN:
        options.binary_location = CFG.CHROME_BIN
    if CFG.CHROMEDRIVER_PATH:
        service = webdriver.chrome.service.Service(CFG.CHROMEDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=options)
    else:
        driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(CFG.PAGE_LOAD_TIMEOUT)
    return driver


# ============================== CHECK LOGIC ================================

class CheckResult:
    SLOTS_AVAILABLE = "slots_available"
    NO_SLOTS = "no_slots"
    UNEXPECTED = "unexpected"


def select_dropdown_by_visible_text(driver, select_id: str, text: str, wait: WebDriverWait):
    el = wait.until(EC.presence_of_element_located((By.ID, select_id)))
    Select(el).select_by_visible_text(text)


def run_single_check(driver) -> str:
    wait = WebDriverWait(driver, CFG.PAGE_LOAD_TIMEOUT)
    driver.get(CFG.BASE_URL)

    try:
        select_dropdown_by_visible_text(driver, "form", CFG.PROVINCE_NAME, wait)
        driver.find_element(By.ID, "btnAceptar").click()

        if CFG.OFFICE_NAME:
            select_dropdown_by_visible_text(driver, "sede", CFG.OFFICE_NAME, wait)
        select_dropdown_by_visible_text(driver, "tramiteGrupo[0]", CFG.TRAMITE_TEXT, wait)
        driver.find_element(By.ID, "btnAceptar").click()

        entrar_btn = wait.until(EC.element_to_be_clickable((By.ID, "btnEntrar")))
        entrar_btn.click()

        try:
            doc_type_el = wait.until(EC.presence_of_element_located((By.ID, "tipoDocumentacion")))
            Select(doc_type_el).select_by_visible_text(CFG.DOC_TYPE)
            driver.find_element(By.ID, "rellenarCamposEspecificosCitar").clear()
            driver.find_element(By.ID, "rellenarCamposEspecificosCitar").send_keys(CFG.DOC_NUMBER)
            driver.find_element(By.ID, "txtDesCitar").clear()
            driver.find_element(By.ID, "txtDesCitar").send_keys(CFG.FULL_NAME)
            driver.find_element(By.ID, "btnEnviar").click()
        except NoSuchElementException:
            log.warning("Personal data form fields didn't match expected layout.")
            return CheckResult.UNEXPECTED

        page_text = driver.page_source.lower()

        no_slots_markers = [
            "no hay citas disponibles",
            "en este momento no hay citas",
            "no existen citas disponibles",
        ]
        if any(m in page_text for m in no_slots_markers):
            return CheckResult.NO_SLOTS

        captcha_markers = ["captcha", "recaptcha", "g-recaptcha"]
        if any(m in page_text for m in captcha_markers):
            log.info("Hit a CAPTCHA -- can't check further automatically.")
            return CheckResult.UNEXPECTED

        calendar_markers = ["seleccione oficina", "seleccione la oficina", "citar"]
        if any(m in page_text for m in calendar_markers):
            return CheckResult.SLOTS_AVAILABLE

        return CheckResult.UNEXPECTED

    except (TimeoutException, NoSuchElementException) as e:
        log.warning("Flow didn't match expected page structure: %s", e)
        return CheckResult.UNEXPECTED
    except WebDriverException as e:
        log.error("WebDriver error: %s", e)
        return CheckResult.UNEXPECTED


# ============================== MAIN (single run) ================================

def main():
    if not is_within_window():
        log.info("Outside configured Madrid-time window — skipping this run.")
        return

    log.info("Within active window — running check.")
    driver = None
    try:
        driver = build_driver()
        result = run_single_check(driver)

        if result == CheckResult.SLOTS_AVAILABLE:
            send_telegram(
                "🚨 POSSIBLE SLOT AVAILABLE 🚨\n"
                f"Time: {datetime.now(ZoneInfo(CFG.WINDOW_TZ)).strftime('%Y-%m-%d %H:%M:%S')} (Madrid)\n"
                f"Go NOW: {CFG.BASE_URL}\n"
                "Complete the booking yourself — this may disappear within minutes."
            )
            log.info("Slots appear available. Alert sent.")
        elif result == CheckResult.NO_SLOTS:
            log.info("No slots available this run.")
        else:
            log.info("Unexpected page state (captcha or layout change) — no alert sent for this alone.")

    except Exception as e:  # noqa: BLE001
        log.exception("Unhandled error during check: %s", e)
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    main()
