import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


TARGET_URL = os.getenv(
    "TARGET_URL",
    "https://media-report-formatter-wvnugiohbpe9liuxgsz2tf.streamlit.app/",
)
WAIT_MINUTES = int(os.getenv("WAIT_MINUTES", "20"))


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
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(120000)

        page.goto(TARGET_URL, wait_until="domcontentloaded")

        wakeup_button = page.get_by_test_id("wakeup-button-owner")
        click_if_visible(wakeup_button)

        page.get_by_text(
            "AsiaNet Document Processing Tool (Beta)", exact=False
        ).wait_for(timeout=300000)

        tab_label = "🚦 一鍵三板塊（關鍵詞直搜）"
        page.get_by_text(tab_label, exact=False).click()
        page.get_by_text(tab_label, exact=False).wait_for(timeout=120000)

        preview_button = "🚀 一鍵三板塊：抓取預覽"
        page.get_by_text(preview_button, exact=False).click()

        page.wait_for_timeout(WAIT_MINUTES * 60 * 1000)
        browser.close()


if __name__ == "__main__":
    main()
