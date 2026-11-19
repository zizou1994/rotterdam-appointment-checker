import os
import time
import datetime
import requests

from bs4 import BeautifulSoup
from dateutil import parser

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BOOKING_URL = (
    "https://concern.ir.rotterdam.nl/afspraak/maken/"
    "product/indienen-naturalisatieverzoek"
)


def send_telegram_notification(message, title):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError("Telegram credentials are missing.")

    text = f"{title}\n\n{message}"

    if len(text) > 4000:
        text = text[:3990] + "..."

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": [[
                    {
                        "text": "Open Rotterdam booking page",
                        "url": BOOKING_URL
                    }
                ]]
            }
        },
        timeout=20
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError("Telegram rejected the notification.")

    print("Telegram notification sent successfully.")


def create_driver():
    print("Starting Chrome...")

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-notifications")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)

    return driver


def select_one_person(driver):
    print("Looking for number-of-people dropdown...")

    dropdowns = driver.find_elements(By.TAG_NAME, "select")
    number_dropdown = None

    for dropdown in dropdowns:
        try:
            name = dropdown.get_attribute("name") or ""
            element_id = dropdown.get_attribute("id") or ""
            html = dropdown.get_attribute("outerHTML") or ""

            if name in ("taal", "sortering"):
                continue

            if element_id in ("id2", "ida"):
                continue

            if (
                "aantal" in html.lower()
                or len(dropdown.find_elements(By.TAG_NAME, "option")) <= 5
            ):
                number_dropdown = dropdown
                break

        except Exception:
            continue

    if number_dropdown is None:
        raise RuntimeError(
            "Could not find the number-of-people dropdown."
        )

    select = Select(number_dropdown)

    print(
        "Available options:",
        [option.text for option in select.options]
    )

    select.select_by_value("0")

    selected_value = select.first_selected_option.get_attribute("value")

    if selected_value != "0":
        raise RuntimeError("Could not select 1 person.")

    print("Selected appointment for 1 person.")

    time.sleep(1)


def click_continue(driver):
    print("Looking for the Verder button...")

    buttons = driver.find_elements(By.TAG_NAME, "button")
    continue_button = None

    for button in buttons:
        button_text = button.text.lower()

        if "verder" in button_text or "continue" in button_text:
            continue_button = button
            break

    if continue_button is None:
        submit_buttons = driver.find_elements(
            By.CSS_SELECTOR,
            "button[type='submit'], input[type='submit']"
        )

        if submit_buttons:
            continue_button = submit_buttons[0]

    if continue_button is None:
        raise RuntimeError("Could not find the Verder button.")

    driver.execute_script(
        "arguments[0].click();",
        continue_button
    )

    print("Clicked Verder.")

    time.sleep(5)


def get_date_filter():
    date_string = os.environ.get("MIN_DATE_FILTER", "").strip()

    if not date_string:
        print("No date filter configured.")
        return None

    cutoff = parser.parse(date_string, dayfirst=True)

    print("Date filter:", cutoff.date())

    return cutoff


def date_is_after_cutoff(date_string, cutoff):
    try:
        appointment_date = parser.parse(
            date_string,
            dayfirst=True,
            fuzzy=True
        )

        return appointment_date.date() > cutoff.date()

    except Exception as error:
        print(f"Could not parse appointment date: {error}")
        return True


def check_appointments(driver, cutoff):
    print("Opening Rotterdam booking page...")

    driver.get(BOOKING_URL)

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    time.sleep(3)

    select_one_person(driver)

    click_continue(driver)

    print("Reading appointment results...")

    soup = BeautifulSoup(driver.page_source, "html.parser")

    page_title = (
        soup.title.get_text(strip=True)
        if soup.title else "Unknown page"
    )

    available_dates = soup.find_all(
        "button",
        class_="list-group-item-action"
    )

    warning_alerts = [
        alert
        for alert in soup.find_all("div", class_="alert-warning")
        if "laden" not in alert.get_text(" ", strip=True).lower()
    ]

    if not available_dates and not warning_alerts:
        raise RuntimeError(
            f"Unexpected results page. "
            f"Title: {page_title}. "
            f"URL: {driver.current_url}"
        )

    actual_slots = []

    for date_button in available_dates:

        if date_button.has_attr("disabled"):
            continue

        heading = date_button.find("h3")
        paragraph = date_button.find("p")

        location = heading.get_text(strip=True) if heading else ""
        date_time = paragraph.get_text(strip=True) if paragraph else ""

        date_time = (
            date_time.replace("from ", "")
            .replace("vanaf ", "")
            .strip()
        )

        print(f"Found entry: {location} - {date_time}")

        if "wachtrij" in date_time.lower():
            print("Ignoring waiting-list entry.")
            continue

        if not date_time or len(date_time) < 5:
            continue

        if cutoff is not None:
            if not date_is_after_cutoff(date_time, cutoff):
                print("Ignoring appointment before date filter.")
                continue

        actual_slots.append(f"{location}: {date_time}")

    if actual_slots:
        message = (
            "APPOINTMENT SLOTS AVAILABLE!\n\n"
            + "\n".join(actual_slots)
            + f"\n\nCheck immediately:\n{BOOKING_URL}"
        )

        print(message)

        send_telegram_notification(
            message,
            "ROTTERDAM APPOINTMENTS AVAILABLE"
        )

    else:
        print(
            "No actual appointment slots found. "
            "Waiting-list entries were ignored."
        )


def main():
    current_time = datetime.datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    print("=" * 50)
    print("ROTTERDAM APPOINTMENT CHECKER")
    print("Started:", current_time)
    print("=" * 50)

    driver = None

    try:
        cutoff = get_date_filter()

        driver = create_driver()

        check_appointments(driver, cutoff)

        print("Appointment check completed successfully.")

    except Exception as error:
        print(f"ERROR: {error}")
        raise

    finally:
        if driver is not None:
            try:
                driver.quit()
                print("Chrome closed.")
            except Exception as error:
                print(f"Could not close Chrome: {error}")


if __name__ == "__main__":
    main()
