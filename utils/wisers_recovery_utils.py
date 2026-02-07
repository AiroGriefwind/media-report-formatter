import os
import threading
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils.wisers_utils import (
    go_back_to_search_form,
    reset_to_login_page,
    perform_login,
    switch_language_to_traditional_chinese,
    robust_logout_request,
)

WISERS_HOME_URL = "https://wisesearch6.wisers.net/wevo/home"
DEFAULT_INACTIVITY_TIMEOUT_SECONDS = 180


def _resolve_screenshot_dir(screenshot_dir=None):
    return screenshot_dir or os.getenv("WISERS_SCREENSHOT_DIR") or os.path.join(".", "artifacts", "screenshots")


def _capture_inactivity_screenshot(driver, st_module=None, logger=None, screenshot_dir=None, reason="inactivity_timeout"):
    if not driver:
        return None
    try:
        screenshot_dir = _resolve_screenshot_dir(screenshot_dir)
        os.makedirs(screenshot_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        fname = f"{ts}_{reason}.png"
        local_fp = os.path.join(screenshot_dir, fname)
        img_bytes = driver.get_screenshot_as_png()
        with open(local_fp, "wb") as f:
            f.write(img_bytes)
        if st_module:
            st_module.image(img_bytes, caption=f"⏱️ {reason} screenshot")
        if logger and hasattr(logger, "upload_screenshot_bytes"):
            try:
                logger.upload_screenshot_bytes(img_bytes, filename=fname)
            except Exception:
                pass
        return local_fp
    except Exception:
        return None


def _go_home_via_url(driver, wait, st_module=None):
    if st_module:
        st_module.info("🔁 嘗試直接輸入 /wevo/home 回到主頁...")
    driver.get(WISERS_HOME_URL)
    time.sleep(1.5)
    try:
        waiter = wait or WebDriverWait(driver, 15)
        waiter.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button#toggle-query-execute.btn.btn-primary")))
    except Exception:
        pass
    return True


def reset_wisers_light(driver, wait, st_module=None, logger=None):
    """Light reset: return to search form via navbar."""
    try:
        if st_module:
            st_module.info("🔄 嘗試輕量復位：回到主搜索頁...")
        go_back_to_search_form(driver=driver, wait=wait, st_module=st_module)
        return True
    except Exception as e:
        if st_module:
            st_module.warning(f"輕量復位失敗：{e}")
        if logger and hasattr(logger, "warn"):
            try:
                logger.warn("Light reset failed", error=str(e))
            except Exception:
                pass
        try:
            return _go_home_via_url(driver=driver, wait=wait, st_module=st_module)
        except Exception as e2:
            if st_module:
                st_module.warning(f"直接回主頁失敗：{e2}")
            if logger and hasattr(logger, "warn"):
                try:
                    logger.warn("Direct home reset failed", error=str(e2))
                except Exception:
                    pass
            return False


def reset_wisers_full(driver, wait, st_module, group_name, username, password, api_key, logger=None):
    """Full reset: logout, clear cookies, relogin, and return to search form."""
    try:
        if st_module:
            st_module.info("🧼 嘗試完整復位：重新登入並回到搜索頁...")
        reset_to_login_page(driver=driver, st_module=st_module)
        perform_login(
            driver=driver,
            wait=wait,
            group_name=group_name,
            username=username,
            password=password,
            api_key=api_key,
            st_module=st_module,
        )
        switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st_module)
        time.sleep(1.5)
        try:
            go_back_to_search_form(driver=driver, wait=wait, st_module=st_module)
        except Exception:
            _go_home_via_url(driver=driver, wait=wait, st_module=st_module)
        return True
    except Exception as e:
        if st_module:
            st_module.warning(f"完整復位失敗：{e}")
        if logger and hasattr(logger, "warn"):
            try:
                logger.warn("Full reset failed", error=str(e))
            except Exception:
                pass
        return False


def abort_with_robust_logout(driver, st_module=None, reason: str = ""):
    """Final fallback: robust logout then abort."""
    msg = f"❌ 已嘗試復位仍失敗，終止流程。{reason}"
    if st_module:
        st_module.error(msg)
    try:
        robust_logout_request(driver=driver, st_module=st_module)
    except Exception:
        pass
    raise RuntimeError(msg)


class InactivityWatchdog:
    def __init__(
        self,
        driver,
        wait=None,
        st_module=None,
        logger=None,
        timeout_seconds=DEFAULT_INACTIVITY_TIMEOUT_SECONDS,
        screenshot_dir=None,
    ):
        self.driver = driver
        self.wait = wait
        self.st_module = st_module
        self.logger = logger
        self.timeout_seconds = timeout_seconds
        self.screenshot_dir = screenshot_dir
        self._lock = threading.Lock()
        self._last_activity = time.monotonic()
        self._paused = False
        self._stop_event = threading.Event()
        self._thread = None
        self.timed_out = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def beat(self):
        with self._lock:
            self._last_activity = time.monotonic()

    def pause(self):
        with self._lock:
            self._paused = True

    def resume(self):
        with self._lock:
            self._paused = False
            self._last_activity = time.monotonic()

    def _run(self):
        while not self._stop_event.is_set():
            time.sleep(1)
            with self._lock:
                if self._paused:
                    continue
                elapsed = time.monotonic() - self._last_activity
            if elapsed >= self.timeout_seconds:
                self.timed_out = True
                if self.st_module:
                    self.st_module.warning("⏱️ 超過 1 分鐘無反應，已觸發復位。")
                _capture_inactivity_screenshot(
                    driver=self.driver,
                    st_module=self.st_module,
                    logger=self.logger,
                    screenshot_dir=self.screenshot_dir,
                    reason="inactivity_timeout",
                )
                try:
                    reset_wisers_light(
                        driver=self.driver,
                        wait=self.wait,
                        st_module=self.st_module,
                        logger=self.logger,
                    )
                except Exception:
                    pass
                # Avoid repeated triggers while the main thread is still stuck
                with self._lock:
                    self._paused = True
