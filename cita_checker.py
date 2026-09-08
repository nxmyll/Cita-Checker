import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
# for the selected province/procedure (it bounces back to the province page with
# this message and empty dropdowns).
NO_CITAS_PHRASE = "no ofrece el servicio de Cita Previa Internet"

ACTIVE_WINDOW_START_HOUR = 8
ACTIVE_WINDOW_END_HOUR = 16
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


def build_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/chromium")

    service = Service(executable_path=os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver"))
    return webdriver.Chrome(service=service, options=options)


def run_single_check() -> bool:
    driver = build_driver()
    try:
        wait = WebDriverWait(driver, 25)

        # Step 1: homepage -> select office + procedure
        driver.get(BASE_URL)
        sede_el = wait.until(EC.presence_of_element_located((By.ID, "sede")))
        Select(sede_el).select_by_value(OFICINA_VALUE)

        tramite_el = wait.until(EC.presence_of_element_located((By.ID, "tramiteGrupo[0]")))
        Select(tramite_el).select_by_value(TRAMITE_VALUE)

        aceptar_btn = wait.until(EC.element_to_be_clickable((By.ID, "btnAceptar")))
        aceptar_btn.click()

        # Step 2: info/terms page -> "Presentación sin Cl@ve"
        entrar_btn = wait.until(EC.element_to_be_clickable((By.ID, "btnEntrar")))
        entrar_btn.click()

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

        # Step 4: result — either bounced back with "no citas" message,
        # a captcha, or (if slots exist) an actual booking/calendar page.
        wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
        driver.implicitly_wait(5)
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

    except Exception as e:
        print(f"❌ Error during check: {e}")
        return False
    finally:
        driver.quit()


if __name__ == "__main__":
    now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))
    if not within_active_window():
        print(f"[{now_madrid.strftime('%Y-%m-%d %H:%M:%S')}] Outside configured Madrid-time window — skipping")
        sys.exit(0)

    print(f"[{now_madrid.strftime('%Y-%m-%d %H:%M:%S')}] Within active window — running check")
    run_single_check()
    sys.exit(0)
