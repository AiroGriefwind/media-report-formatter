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
from utils.html_structure_config import HTML_STRUCTURE

WISERS_HOME_URL = "https://wisesearch6.wisers.net/wevo/home"
DEFAULT_INACTIVITY_TIMEOUT_SECONDS = 180


def _resolve_screenshot_dir(screenshot_dir=None):
    return screenshot_dir or os.getenv("WISERS_SCREENSHOT_DIR") or os.path.join(".", "artifacts", "screenshots")


def _log_recovery(message, st_module=None, logger=None, level="info"):
    if st_module:
        if level == "warning":
            st_module.warning(message)
        elif level == "error":
            st_module.error(message)
        else:
            st_module.info(message)
    if logger:
        try:
            if level == "warning" and hasattr(logger, "warn"):
                logger.warn(message)
            elif level == "error" and hasattr(logger, "error"):
                logger.error(message)
            elif hasattr(logger, "info"):
                logger.info(message)
        except Exception:
            pass


def _is_visible(driver, by, selector):
    try:
        elements = driver.find_elements(by, selector)
    except Exception:
        return False
    for el in elements:
        try:
            if el.is_displayed():
                return True
        except Exception:
            continue
    return False


def _detect_wisers_page_state(driver):
    """
    Return a normalized Wisers page state for reset routing.
    """
    state = {
        "url": "",
        "is_wisers": False,
        "page": "unknown",
        "signals": [],
    }
    if not driver:
        state["page"] = "driver_missing"
        return state

    try:
        url = driver.current_url or ""
    except Exception:
        url = ""
    state["url"] = url
    state["is_wisers"] = "wisers" in url.lower()

    if "timeout" in url.lower():
        state["page"] = "timeout"
        state["signals"].append("url_timeout")
        return state

    if _is_visible(driver, By.CSS_SELECTOR, 'input[data-qa-ci="groupid"]'):
        state["page"] = "login"
        state["signals"].append("login_groupid_input")
        return state

    if _is_visible(driver, By.CSS_SELECTOR, "#modal-saved-search-ws6"):
        state["page"] = "saved_search_modal"
        state["signals"].append("saved_search_modal_visible")
        return state

    edit_title_selectors = (HTML_STRUCTURE.get("edit_search", {}) or {}).get("modal_title") or []
    for sel in edit_title_selectors:
        by = (sel or {}).get("by")
        value = (sel or {}).get("value")
        if by != "css" or not value:
            continue
        try:
            titles = driver.find_elements(By.CSS_SELECTOR, value)
        except Exception:
            titles = []
        for title in titles:
            try:
                if not title.is_displayed():
                    continue
                txt = (title.text or "").strip()
                if "编辑搜索" in txt or "編輯搜索" in txt:
                    state["page"] = "edit_search_modal"
                    state["signals"].append("edit_search_modal_title")
                    return state
            except Exception:
                continue

    if _is_visible(driver, By.CSS_SELECTOR, "div.article-detail"):
        state["page"] = "article_detail"
        state["signals"].append("article_detail_container")
        return state

    if _is_visible(driver, By.CSS_SELECTOR, "button#toggle-query-execute.btn.btn-primary"):
        state["page"] = "home_search"
        state["signals"].append("home_search_button")
        return state

    if _is_visible(driver, By.CSS_SELECTOR, "div.media-left > a[href='/wevo/home']"):
        state["signals"].append("back_to_search_link")
        if _is_visible(driver, By.CSS_SELECTOR, "ul.nav-tabs.navbar-nav-pub"):
            state["page"] = "search_results"
            state["signals"].append("results_tabbar")
        else:
            state["page"] = "results_or_transition"
        return state

    if _is_visible(driver, By.CSS_SELECTOR, "ul.nav-tabs.navbar-nav-pub"):
        state["page"] = "search_results"
        state["signals"].append("results_tabbar")
        return state

    return state


def _close_visible_modals(driver, st_module=None, logger=None):
    closed = 0
    selectors = [
        "button.close[data-dismiss='modal']",
        "button[data-dismiss='modal']",
        "#modal-saved-search-ws6 button.close",
    ]
    for sel in selectors:
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            buttons = []
        for btn in buttons:
            try:
                if not btn.is_displayed():
                    continue
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.3)
                closed += 1
            except Exception:
                continue
    try:
        active = driver.switch_to.active_element
        if active:
            active.send_keys("\uE00C")  # ESC
    except Exception:
        pass
    if closed > 0:
        _log_recovery(f"🧩 已嘗試關閉 {closed} 個可見彈窗。", st_module=st_module, logger=logger)
    return closed > 0


def _route_light_reset_by_page(driver, wait, st_module=None, logger=None):
    state = _detect_wisers_page_state(driver)
    _log_recovery(
        f"🧭 Light reset 頁面判斷：{state.get('page')} | signals={state.get('signals')} | url={state.get('url')}",
        st_module=st_module,
        logger=logger,
    )

    page = state.get("page")
    if page == "home_search":
        return True
    if page in ("edit_search_modal", "saved_search_modal"):
        _close_visible_modals(driver, st_module=st_module, logger=logger)
    if page == "article_detail":
        try:
            driver.back()
            time.sleep(1.2)
        except Exception:
            pass
    if page in ("search_results", "results_or_transition", "article_detail", "edit_search_modal", "saved_search_modal"):
        try:
            go_back_to_search_form(driver=driver, wait=wait, st_module=st_module)
            return True
        except Exception:
            pass
    return _go_home_via_url(driver=driver, wait=wait, st_module=st_module)


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
        _log_recovery("🔄 嘗試輕量復位：回到主搜索頁...", st_module=st_module, logger=logger)
        ok = _route_light_reset_by_page(
            driver=driver,
            wait=wait,
            st_module=st_module,
            logger=logger,
        )
        post_state = _detect_wisers_page_state(driver)
        _log_recovery(
            f"🧭 Light reset 後頁面：{post_state.get('page')} | signals={post_state.get('signals')}",
            st_module=st_module,
            logger=logger,
        )
        return bool(ok and post_state.get("page") in ("home_search", "search_results", "results_or_transition"))
    except Exception as e:
        _log_recovery(f"輕量復位失敗：{e}", st_module=st_module, logger=logger, level="warning")
        try:
            return _go_home_via_url(driver=driver, wait=wait, st_module=st_module)
        except Exception as e2:
            _log_recovery(f"直接回主頁失敗：{e2}", st_module=st_module, logger=logger, level="warning")
            return False


def reset_wisers_full(driver, wait, st_module, group_name, username, password, api_key, logger=None):
    """Full reset: logout, clear cookies, relogin, and return to search form."""
    try:
        pre_state = _detect_wisers_page_state(driver)
        _log_recovery(
            f"🧭 Full reset 前頁面：{pre_state.get('page')} | signals={pre_state.get('signals')} | url={pre_state.get('url')}",
            st_module=st_module,
            logger=logger,
        )
        _log_recovery("🧼 嘗試完整復位：重新登入並回到搜索頁...", st_module=st_module, logger=logger)
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
        post_state = _detect_wisers_page_state(driver)
        _log_recovery(
            f"🧭 Full reset 後頁面：{post_state.get('page')} | signals={post_state.get('signals')}",
            st_module=st_module,
            logger=logger,
        )
        return True
    except Exception as e:
        _log_recovery(f"完整復位失敗：{e}", st_module=st_module, logger=logger, level="warning")
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
                # Do not call st_module from this thread – causes "missing ScriptRunContext"
                # and can block the main run / cause 503 health-check timeouts.
                if self.logger:
                    try:
                        self.logger.info("⏱️ 超過 1 分鐘無反應，已觸發復位。")
                    except Exception:
                        pass
                _capture_inactivity_screenshot(
                    driver=self.driver,
                    st_module=None,  # never touch Streamlit from background thread
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
