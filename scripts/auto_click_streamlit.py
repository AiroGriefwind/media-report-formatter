import os
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://media-report-formatter-wvnugiohbpe9liuxgsz2tf.streamlit.app/",
)
WAIT_MINUTES = int(os.getenv("WAIT_MINUTES", "20"))
HEADLESS = os.getenv("HEADLESS", "true").lower() != "false"
SLOWMO_MS = int(os.getenv("SLOWMO_MS", "0"))
APP_READY_TIMEOUT_SEC = int(os.getenv("APP_READY_TIMEOUT_SEC", "600"))
DEBUG_DIR = os.getenv("DEBUG_DIR", "artifacts")
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))
POLL_MAX_SEC = int(os.getenv("POLL_MAX_SEC", "120"))
POLL_RETRIES = int(os.getenv("POLL_RETRIES", "2"))


def log(message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def get_locator_in_frames(page, selector):
    for frame in page.frames:
        locator = frame.locator(selector)
        try:
            if locator.count() > 0:
                return locator
        except PlaywrightTimeoutError:
            continue
    return None


def click_if_visible(locator, timeout_ms=5000):
    try:
        if locator.is_visible(timeout=timeout_ms):
            locator.click()
            return True
    except PlaywrightTimeoutError:
        return False
    return False


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOWMO_MS)
        page = browser.new_page()
        page.set_default_timeout(120000)

        wakeup_button = page.get_by_test_id("wakeup-button-owner")
        app_title_text = "AsiaNet Document Processing Tool (Beta)"
        app_title_selector = f"span:has-text('{app_title_text}')"

        for attempt in range(1, POLL_RETRIES + 1):
            log(f"Go to {TARGET_URL} (attempt {attempt}/{POLL_RETRIES})")
            page.goto(TARGET_URL, wait_until="domcontentloaded")

            log("Polling for wakeup page or app page...")
            found_state = False
            end_at = time.time() + POLL_MAX_SEC
            while time.time() < end_at:
                if wakeup_button.is_visible(timeout=1000):
                    log("Wakeup page detected; clicking button.")
                    wakeup_button.click()
                    found_state = True
                    break
                app_title = get_locator_in_frames(page, app_title_selector)
                if app_title:
                    log("App page detected.")
                    found_state = True
                    break
                log("Not ready yet; wait 5s.")
                time.sleep(POLL_INTERVAL_SEC)

            if not found_state:
                log("Polling timeout; refresh and retry.")
                continue

            try:
                log("Waiting for app title to be visible...")
                app_title = get_locator_in_frames(page, app_title_selector)
                if not app_title:
                    raise PlaywrightTimeoutError("App title not found in any frame.")
                app_title.first.wait_for(timeout=APP_READY_TIMEOUT_SEC * 1000, state="visible")
                log("App title is visible.")
                break
            except PlaywrightTimeoutError:
                log("App title wait timeout; refresh and retry.")
                continue
        else:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            page.screenshot(path=os.path.join(DEBUG_DIR, "timeout.png"), full_page=True)
            with open(os.path.join(DEBUG_DIR, "timeout.html"), "w", encoding="utf-8") as f:
                f.write(page.content())
            log(f"Timeout. Saved debug files to {DEBUG_DIR}.")
            raise PlaywrightTimeoutError("App did not become ready in time.")

        tab_label = "🚦 一鍵三板塊（關鍵詞直搜）"
        tab_selector = f"p:has-text('{tab_label}')"
        tab_header_selector = f"h3:has(span:has-text('{tab_label}'))"
        log(f"Click tab: {tab_label}")
        tab_locator = get_locator_in_frames(page, tab_selector)
        if not tab_locator:
            raise PlaywrightTimeoutError("Tab label not visible.")
        tab_locator.first.click()
        end_at = time.time() + POLL_MAX_SEC
        loop_count = 0
        while time.time() < end_at:
            loop_count += 1
            tab_header = get_locator_in_frames(page, tab_header_selector)
            if tab_header:
                tab_header.first.wait_for(timeout=5000, state="visible")
                log("Tab header is visible.")
                break
            log(f"Tab header not ready (loop {loop_count}); wait 5s.")
            time.sleep(POLL_INTERVAL_SEC)
        else:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            page.screenshot(
                path=os.path.join(DEBUG_DIR, "tab_timeout.png"), full_page=True
            )
            with open(
                os.path.join(DEBUG_DIR, "tab_timeout.html"), "w", encoding="utf-8"
            ) as f:
                f.write(page.content())
            log(f"Tab header timeout. Saved debug files to {DEBUG_DIR}.")
            raise PlaywrightTimeoutError("Tab header not visible.")

        preview_button = "🚀 一鍵三板塊：抓取預覽"
        preview_selector = (
            f"div[data-testid='stButton'] button:has-text('{preview_button}')"
        )
        log(f"Click button: {preview_button}")
        end_at = time.time() + POLL_MAX_SEC
        loop_count = 0
        while time.time() < end_at:
            loop_count += 1
            preview_locator = get_locator_in_frames(page, preview_selector)
            if preview_locator:
                preview_locator.first.click()
                log("Preview button clicked.")
                break
            log(f"Preview button not ready (loop {loop_count}); wait 5s.")
            time.sleep(POLL_INTERVAL_SEC)
        else:
            os.makedirs(DEBUG_DIR, exist_ok=True)
            page.screenshot(
                path=os.path.join(DEBUG_DIR, "preview_timeout.png"), full_page=True
            )
            with open(
                os.path.join(DEBUG_DIR, "preview_timeout.html"), "w", encoding="utf-8"
            ) as f:
                f.write(page.content())
            log(f"Preview timeout. Saved debug files to {DEBUG_DIR}.")
            raise PlaywrightTimeoutError("Preview button not visible.")

        log(f"Wait {WAIT_MINUTES} minutes for the job to run.")
        page.wait_for_timeout(WAIT_MINUTES * 60 * 1000)
        log("Done.")
        browser.close()


if __name__ == "__main__":
    main()
