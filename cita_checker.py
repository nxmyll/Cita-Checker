#!/usr/bin/env python3
import os
import sys
import time
import tempfile
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# ============== CONFIG ==============
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
DOC_NUMBER = os.environ.get("DOC_NUMBER")
FULL_NAME = os.environ.get("FULL_NAME")
NATIONALITY = os.environ.get("NATIONALITY")

BASE_URL = "https://icp.administracionelectronica.gob.es/icpplus/index.html"

# Confirmed from the live "sede" dropdown: "99" = Cualquier oficina (any Barcelona office)
OFICINA_VALUE = "99"

# Confirmed from the live "tramiteGrupo[0]" dropdown:
# "POLICÍA-TOMA DE HUELLAS (EXPEDICIÓN DE TARJETA) INICIAL, RENOVACIÓN, DUPLICADO Y LEY 14/2013"
TRAMITE_VALUE = "4010"

# Confirmed from the live "txtPaisNac" dropdown: "424" = PAKISTAN
NATIONALITY_VALUE = "424"

# Exact phrase confirmed from the live site when there is no availability at all
NO_CITAS_PHRASE = "no ofrece el servicio de Cita Previa Internet"

ACTIVE_WINDOW_START_HOUR = 8
ACTIVE_WINDOW_END_HOUR = 16
MAX_RETRIES = 2
RETRY_DELAY = 5  # seconds
# =====================================


def send_telegram_alert(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
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


def _find_chrome_binary():
    # Prefer explicit env var, otherwise try common names
    env_bin = os.environ.get("CHROME_BIN")
    if env_bin:
        return env_bin
    from shutil import which

    for name in ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome"):
        path = which(name)
        if path:
            return path
    # fallback to a common path so options.binary_location won't be empty
    return "/usr/bin/chromium-browser"


def _make_base_options(headless_variant: str, user_data_dir: str) -> Options:
    options = Options()
    # headless_variant: 'new' or 'chrome'
    if headless_variant == "new":
        options.add_argument("--headless=new")
    else:
        options.add_argument("--headless=chrome")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--no-zygote")
    options.add_argument("--single-process")
    options.add_argument("--window-size=1280,900")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )
    options.page_load_strategy = "normal"
    options.binary_location = _find_chrome_binary()
    return options


def build_driver() -> webdriver.Chrome:
    """
    Try to start Chrome with a temporary writable user-data-dir.
    First attempt uses new headless mode; on DevToolsActivePort failure we retry with classic headless.
    """
    # Create temp user data dir for Chrome to avoid permission issues
    user_data_dir = os.environ.get("CHROME_USER_DATA_DIR") or tempfile.mkdtemp(prefix="selenium-user-data-")
    last_exc = None

    for headless_variant in ("new", "chrome"):
        options = _make_base_options(headless_variant, user_data_dir)
        # Write chromedriver logs to chromedriver.log in the working dir
        service = Service(log_path="chromedriver.log", service_args=["--verbose"])
        try:
            print(f"Launching Chromium (headless={headless_variant}) with user-data-dir={user_data_dir} ...", flush=True)
            driver = webdriver.Chrome(service=service, options=options)
            driver.set_page_load_timeout(45)
            # attach the user-data-dir so we can clean it up later
            try:
                driver._selenium_user_data_dir = user_data_dir
            except Exception:
                pass
            print("Chromium launched successfully.", flush=True)
            try:
                print("Driver capabilities:", driver.capabilities, flush=True)
            except Exception:
                pass
            return driver
        except WebDriverException as e:
            print(f"⚠️ Chrome startup failed with headless={headless_variant}: {e}", flush=True)
            last_exc = e
            # If the driver was partially created, attempt to quit and continue
            try:
                # Some drivers may exist as local variables in the exception; ensure cleanup
                pass
            except Exception:
                pass
            # Try next headless variant
            continue

    # if we get here, both attempts failed: remove temp dir then raise the last exception
    try:
        shutil.rmtree(user_data_dir)
    except Exception:
        pass
    raise last_exc or RuntimeError("Could not start Chrome/WebDriver")


def run_single_check() -> bool:
    driver = None
    try:
        driver = build_driver()
        wait = WebDriverWait(driver, 20)

        # Step 1: homepage -> select office + procedure
        print("Loading homepage...", flush=True)
        try:
            driver.get(BASE_URL)
        except TimeoutException:
            print("Homepage load timed out — proceeding with whatever loaded so far.", flush=True)

        # Wait for page and JavaScript to settle
        time.sleep(2)

        sede_el = wait.until(EC.presence_of_element_located((By.ID, "sede")))
        Select(sede_el).select_by_value(OFICINA_VALUE)

        tramite_el = wait.until(EC.presence_of_element_located((By.ID, "tramiteGrupo[0]")))
        Select(tramite_el).select_by_value(TRAMITE_VALUE)

        aceptar_btn = wait.until(EC.element_to_be_clickable((By.ID, "btnAceptar")))
        aceptar_btn.click()
        print("Step 1 done: office + procedure selected.", flush=True)

        # Step 2: info/terms page -> "Presentación sin Cl@ve"
        entrar_btn = wait.until(EC.element_to_be_clickable((By.ID, "btnEntrar")))
        entrar_btn.click()
        print("Step 2 done: continued without Cl@ve.", flush=True)

        # Step 3: personal details form (NIE, name, nationality)
        nie_field = wait.until(EC.presence_of_element_located((By.ID, "txtIdCitado")))
        nie_field.clear()
        nie_field.send_keys(DOC_NUMBER)

        name_field = driver.find_element(By.ID, "txtDesCitado")
        name_field.clear()
        name_field.send_keys(FULL_NAME)

        pais_el = driver.find_element(By.ID, "txtPaisNac")
        Select(pais_el).select_by_value(NATIONALITY_VALUE)

        enviar_btn = driver.find_element(By.ID, "btnEnviar")
        enviar_btn.click()
        print("Step 3 done: personal details submitted.", flush=True)

        # Step 4: result — either bounced back with "no citas" message,
        # a captcha, or (if slots exist) an actual booking/calendar page.
        time.sleep(3)
        page_text = driver.page_source

        if NO_CITAS_PHRASE in page_text:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ❌ No slots — confirmed via real site message")
            return False

        # Heuristic CAPTCHA detection (site uses eu-captcha elsewhere on the flow)
        captcha_present = any(
            len(driver.find_elements(By.ID, cid)) > 0
            for cid in ["captcha", "captchaAnswer", "txtCodigoVerificacion"]
        )
        if captcha_present:
            msg = (
                "⚠️ *Posible cita disponible — verificación manual necesaria*\n\n"
                "El sitio mostró un CAPTCHA que el script no puede resolver. "
                "Esto puede significar que hay citas disponibles.\n\n"
                "👉 Revisa manualmente ahora:\n"
                f"{BASE_URL}"
            )
            send_telegram_alert(msg)
            print("⚠️ CAPTCHA encountered — alerted for manual check.")
            return True

        # Anything else at this point is unexpected — did not match the known
        # "no citas" message and no captcha was found. This is the most likely
        # place real availability would show up, so alert rather than stay silent.
        msg = (
            "🚨 *Posible disponibilidad detectada — revisa manualmente*\n\n"
            f"👉 {BASE_URL}\n\n"
            "(El script no reconoció el mensaje habitual de 'no hay citas' — "
            "la página puede mostrar disponibilidad real o haber cambiado.)"
        )
        send_telegram_alert(msg)
        print("🚨 Unexpected page state (not the known no-citas message) — alert sent.")
        return True

    except WebDriverException as e:
        # Capture debugging artifacts to help diagnose CI/browser failures
        try:
            if driver:
                screenshot_path = os.environ.get("ERROR_SCREENSHOT_PATH", "error.png")
                page_source_path = os.environ.get("ERROR_PAGE_SOURCE", "page_source.html")
                try:
                    driver.save_screenshot(screenshot_path)
                    print(f"Saved screenshot to {screenshot_path}", flush=True)
                except Exception:
                    pass
                try:
                    with open(page_source_path, "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    print(f"Saved page source to {page_source_path}", flush=True)
                except Exception:
                    pass
        except Exception:
            pass

        print(f"❌ WebDriver error: {e}", flush=True)
        return False
    except Exception as e:
        print(f"❌ Fatal error: {e}", flush=True)
        return False
    finally:
        # Quit the driver and cleanup any temporary user-data-dir we created
        if driver:
            try:
                user_data_dir = getattr(driver, "_selenium_user_data_dir", None)
                try:
                    driver.quit()
                except Exception as e:
                    print(f"⚠️ Error closing driver: {e}", flush=True)
                if user_data_dir:
                    try:
                        shutil.rmtree(user_data_dir)
                        print(f"Removed temporary user-data-dir: {user_data_dir}", flush=True)
                    except Exception as e:
                        print(f"⚠️ Could not remove user-data-dir {user_data_dir}: {e}", flush=True)
            except Exception:
                pass


def run_with_retry() -> bool:
    """Run the check with retry logic for transient failures."""
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n{'='*50}")
        print(f"Attempt {attempt}/{MAX_RETRIES}")
        print(f"{'='*50}\n", flush=True)
        
        try:
            result = run_single_check()
            if result:
                return True
            else:
                print(f"❌ Attempt {attempt} returned failure (False).", flush=True)
                if attempt < MAX_RETRIES:
                    print(f"⏳ Waiting {RETRY_DELAY}s before retry...", flush=True)
                    time.sleep(RETRY_DELAY)
                else:
                    print("❌ All retry attempts exhausted.", flush=True)
                    return False
        except Exception as e:
            print(f"❌ Attempt {attempt} failed with exception: {e}", flush=True)
            if attempt < MAX_RETRIES:
                print(f"⏳ Waiting {RETRY_DELAY}s before retry...", flush=True)
                time.sleep(RETRY_DELAY)
            else:
                print("❌ All retry attempts exhausted.", flush=True)
                raise
    
    return False


if __name__ == "__main__":
    now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))
    # FORCE_RUN should be "true" to force execution outside window
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
        print("❌ Check finished but reported failure (no availability / webdriver issue). Exiting with non-zero code.", flush=True)
        sys.exit(1)

    print("✅ Check finished and reported success.")
    sys.exit(0)
