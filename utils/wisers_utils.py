# =============================================================================
# CORE WISERS PLATFORM INTERACTION FUNCTIONS
# =============================================================================

import time
import base64
import tempfile
import os
import requests
import traceback
from functools import wraps
from datetime import datetime
import pytz

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.webdriver import WebDriver
from twocaptcha import TwoCaptcha

from .config import WISERS_URL

from utils.firebase_logging import get_logger

HKT = pytz.timezone("Asia/Hong_Kong")

# =============================================================================
# RETRY DECORATOR
# =============================================================================

def retry_step(func):
    """Retry decorator for Wisers functions - handles screenshots and logout on failure"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        st = kwargs.get('st_module')
        driver = kwargs.get('driver')
        logger = kwargs.get("logger")
        screenshot_dir = kwargs.get("screenshot_dir") or os.getenv("WISERS_SCREENSHOT_DIR") or os.path.join(".", "artifacts", "screenshots")
        retry_limit = 3
        
        for trial in range(1, retry_limit + 1):
            try:
                result = func(*args, **kwargs)
                if st:
                    st.write(f"✅ Step {func.__name__} succeeded on attempt {trial}")
                return result
            except Exception as e:
                if st:
                    st.warning(f"⚠️ Step {func.__name__} failed on attempt {trial}: {e}")
                if logger and hasattr(logger, "warn"):
                    try:
                        logger.warn(f"Step {func.__name__} failed on attempt {trial}", error=str(e))
                    except Exception:
                        pass
                
                # Screenshot on failure
                if driver:
                    try:
                        img_bytes = driver.get_screenshot_as_png()

                        # Streamlit path (existing behavior)
                        if st:
                            st.image(img_bytes, caption=f"Screencap after failure in {func.__name__}, attempt {trial}")
                            st.download_button(
                                label=f"Download {func.__name__}_attempt{trial}_screenshot.png",
                                data=img_bytes,
                                file_name=f"{func.__name__}_attempt{trial}_screenshot.png",
                                mime="image/png"
                            )

                        # CLI/local path: always save to disk
                        os.makedirs(screenshot_dir, exist_ok=True)
                        ts = time.strftime("%Y%m%d_%H%M%S")
                        fname = f"{ts}_{func.__name__}_attempt{trial}.png"
                        local_fp = os.path.join(screenshot_dir, fname)
                        with open(local_fp, "wb") as f:
                            f.write(img_bytes)

                        # Also save URL + tiny context for debugging
                        try:
                            url = driver.current_url
                        except Exception:
                            url = ""
                        meta_fp = local_fp.replace(".png", ".txt")
                        try:
                            with open(meta_fp, "w", encoding="utf-8") as f:
                                f.write(f"func={func.__name__}\n")
                                f.write(f"attempt={trial}\n")
                                f.write(f"url={url}\n")
                                f.write(f"error={repr(e)}\n")
                        except Exception:
                            pass

                        if st:
                            fb = get_logger(st)
                        else:
                            fb = None

                        # Upload to Firebase if logger is available (CLI logger or Streamlit logger)
                        up_logger = logger or fb
                        if up_logger and hasattr(up_logger, "upload_file_to_firebase"):
                            try:
                                # Prefer run-scoped folder if available
                                session_id = getattr(up_logger, "session_id", "cli")
                                run_id = getattr(up_logger, "run_id", "run")
                                remote_path = f"runs/{session_id}/{run_id}/screens/{fname}"
                                gs_url = up_logger.upload_file_to_firebase(local_fp, remote_path)
                                if logger and hasattr(logger, "info"):
                                    logger.info("Uploaded failure screenshot", gs_url=gs_url, local_fp=local_fp)
                            except Exception:
                                pass


                    except Exception as screencap_err:
                        if st:
                            st.warning(f"Screencap failed: {screencap_err}")
                        if logger and hasattr(logger, "warn"):
                            try:
                                logger.warn("Screencap failed", error=str(screencap_err))
                            except Exception:
                                pass
                
                time.sleep(2)
                
                if trial == retry_limit:
                    if st:
                        st.error(f"❌ Step {func.__name__} failed after {retry_limit} attempts.")
                    if logger and hasattr(logger, "error"):
                        try:
                            logger.error(f"Step {func.__name__} failed after {retry_limit} attempts.", error=str(e))
                        except Exception:
                            pass
                    
                    # Robust logout on final failure
                    try:
                        if driver:
                            robust_logout_request(driver=driver, st_module=st)
                        elif st:
                            st.warning("Driver not available for robust logout request.")
                    except Exception as logout_err:
                        if st:
                            st.warning(f"Robust logout request failed: {logout_err}")
                        if logger and hasattr(logger, "warn"):
                            try:
                                logger.warn("Robust logout request failed", error=str(logout_err))
                            except Exception:
                                pass
                    
                    raise Exception(f"Step {func.__name__} failed after {retry_limit} attempts.")
    return wrapper

# =============================================================================
# CORE BROWSER & SESSION MANAGEMENT
# =============================================================================

@retry_step
def setup_webdriver(**kwargs):
    """Setup Chrome WebDriver with optimal settings for Wisers"""
    headless = kwargs.get('headless')
    st_module = kwargs.get('st_module')
    
    try:
        if st_module:
            st_module.write("Setting up Chrome options...")
            
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless")
            
        # Stability options for Streamlit Cloud
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--remote-debugging-port=9222")
        
        if st_module:
            st_module.write("Using Selenium Manager for automatic driver management...")
            
        driver = webdriver.Chrome(options=options)
        driver.set_window_size(1200, 800)
        driver.get(WISERS_URL)
        
        if st_module:
            st_module.write("✅ WebDriver setup complete.")
            
        return driver
        
    except Exception as e:
        if st_module:
            st_module.error(f"WebDriver setup failed: {e}")
        return None

def reset_to_login_page(driver, st_module=None):
    try:
        # Attempt to force logout before clearing cookies
        try:
            if st_module:
                st_module.write("Sending robust logout request before reset...")
            robust_logout_request(driver, st_module)
        except Exception as e:
            msg = f"Robust logout failed/prelogin: {e}"
            if st_module:
                st_module.warning(msg)
            else:
                print(msg)

        driver.delete_all_cookies()
        driver.get(WISERS_URL)
        time.sleep(2)
    except Exception as e:
        msg = f"Pre-login reset failed: {e}"
        if st_module:
            st_module.warning(msg)
        else:
            print(msg)

def clear_login_fields(driver, wait=None, st_module=None):
    """Clear login page fields if populated, will use .clear() if possible and also overwrite."""
    try:
        if wait:
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-qa-ci="groupid"]')))
            except TimeoutException:
                return
        selectors = {
            'groupid': 'input[data-qa-ci="groupid"]',
            'userid': 'input[data-qa-ci="userid"]',
            'password': 'input[data-qa-ci="password"]',
            'captcha': 'input.CaptchaField__Input-hffgxm-4',
        }
        for field, selector in selectors.items():
            elems = driver.find_elements(By.CSS_SELECTOR, selector)
            if elems:
                try:
                    elems[0].clear()
                except Exception:
                    # fallback: overwrite with empty string
                    elems[0].send_keys('\ue009' + 'a')  # Ctrl+A
                    elems[0].send_keys('\ue003')       # Del
                try:
                    driver.execute_script("arguments[0].value = '';", elems[0])
                except Exception:
                    pass
    except Exception as e:
        msg = f"Field clearing failed: {e}"
        if st_module:
            st_module.warning(msg)
        else:
            print(msg)


def is_logged_in_state(driver):
    """Heuristic check for post-login state (dashboard or search page)."""
    if not driver:
        return False
    selectors = [
        (By.CSS_SELECTOR, 'div.sc-1kg7aw5-0.dgeiTV > button'),  # dashboard/waffle
        (By.CSS_SELECTOR, 'button#toggle-query-execute.btn.btn-primary'),  # search form
        (By.CSS_SELECTOR, 'div.media-left > a[href="/wevo/home"]'),  # back to search
    ]
    for by, sel in selectors:
        try:
            if driver.find_elements(by, sel):
                return True
        except Exception:
            continue
    return False


def is_hkt_monday() -> bool:
    return datetime.now(HKT).weekday() == 0


@retry_step
def set_date_range_period(**kwargs):
    """
    Set Wisers date range via dropdown.
    period_name: "today" | "yesterday" | "last-week" | "last-month" | "last-6-months" | "last-year" | "custom" | "2025"
    """
    driver = kwargs.get("driver")
    wait = kwargs.get("wait")
    st = kwargs.get("st_module")
    period_name = kwargs.get("period_name", "").strip()
    if not period_name:
        return

    try:
        label_map = {
            "today": "今天",
            "yesterday": "昨天",
            "last-week": "最近一周",
            "last-month": "最近一个月",
            "last-6-months": "最近六个月",
            "last-year": "最近一年",
            "2025": "2025",
            "custom": "自定义",
        }

        toggle = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "li#DatePickerApp a.dropdown-toggle.btn"))
        )
        driver.execute_script("arguments[0].click();", toggle)
        time.sleep(0.5)

        menu = None
        for _ in range(3):
            menus = driver.find_elements(
                By.CSS_SELECTOR,
                "ul.dropdown-menu.dropdown-menu-right.datepicker-opt[name='dataRangePeriod']",
            )
            if menus:
                menu = menus[0]
                break
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", toggle)

        if not menu:
            raise Exception("Date range menu not found")

        item = None
        elems = driver.find_elements(
            By.CSS_SELECTOR,
            f"ul[name='dataRangePeriod'] li[name='{period_name}'] a",
        )
        if not elems:
            label = label_map.get(period_name)
            if label:
                elems = driver.find_elements(
                    By.XPATH,
                    f"//ul[@name='dataRangePeriod']//li/a[contains(normalize-space(), '{label}')]",
                )
        if elems:
            item = elems[0]
        else:
            raise Exception(f"Date range option not found: {period_name}")

        driver.execute_script("arguments[0].click();", item)
        time.sleep(0.2)

        try:
            apply_btn = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "ul[name='dataRangePeriod'] li[name='dateRangePeriod_apply'] a.btn")
                )
            )
            driver.execute_script("arguments[0].click();", apply_btn)
        except TimeoutException:
            # Some pages auto-apply without a clickable "apply" state.
            if st:
                st.warning("日期范围已切换，但未检测到可点击的“应用”按钮，继续执行。")
            # Try to close the dropdown to avoid overlays
            driver.execute_script("document.body.click();")

        try:
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, "ul.dropdown-menu.dropdown-menu-right.datepicker-opt[name='dataRangePeriod']")
                )
            )
        except TimeoutException:
            pass

        time.sleep(1.0)
        if st:
            st.write(f"📅 日期范围已切换到：{period_name}")
    except Exception as e:
        if st:
            st.warning(f"日期范围切换失败: {e}")
        raise


@retry_step
def perform_login(**kwargs):
    """Perform login to Wisers with captcha solving & robust error handling."""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    group_name = kwargs.get('group_name')
    username = kwargs.get('username')
    password = kwargs.get('password')
    api_key = kwargs.get('api_key')
    st_module = kwargs.get('st_module')

    # === 0. Check if already post-login / tutorial modal ===
    # If dashboard or search page detected, close modal if any then return early (already logged in!)
    try:
        if is_logged_in_state(driver):
            if st_module:
                st_module.write("Already logged in (post-login page detected).")
            try:
                close_tutorial_modal_ROBUST(driver=driver, wait=wait, status_text=st_module, st_module=st_module)
            except Exception as e:
                if st_module:
                    st_module.warning(f"Tutorial modal close failed: {e}")
            return
    except Exception:
        pass

    # === 1. Reset and clear: always start from fresh login page ===
    if st_module: st_module.write("Resetting to login page...")
    reset_to_login_page(driver, st_module=st_module)
    clear_login_fields(driver, wait=wait, st_module=st_module)

    # === 2. Fill login form ===
    group_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-qa-ci="groupid"]')))
    user_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-qa-ci="userid"]')))
    pass_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-qa-ci="password"]')))

    for elem in (group_elem, user_elem, pass_elem):
        try:
            elem.clear()
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].value = '';", elem)
        except Exception:
            pass

    group_elem.send_keys(group_name)
    user_elem.send_keys(username)
    pass_elem.send_keys(password)

    # === 3. Solve captcha ===
    try:
        captcha_img = driver.find_element(By.CSS_SELECTOR, 'img.CaptchaField__CaptchaImage-hffgxm-5')
        captcha_src = captcha_img.get_attribute('src')
        img_data = base64.b64decode(captcha_src.split(',')[1])
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_captcha:
            tmp_captcha.write(img_data)
            tmp_captcha_path = tmp_captcha.name
        solver = TwoCaptcha(api_key)
        captcha_text = solver.normal(tmp_captcha_path)['code']
        os.remove(tmp_captcha_path)
        driver.find_element(By.CSS_SELECTOR, 'input.CaptchaField__Input-hffgxm-4').send_keys(captcha_text)
    except Exception as captcha_error:
        raise Exception(f"Failed during 2Captcha solving process: {captcha_error}")

    # === 4. Submit login ===
    login_btn = driver.find_element(By.CSS_SELECTOR, 'input[data-qa-ci="button-login"]')
    login_btn.click()

    # === 5. Wait for known post-login structure or error ===
    try:
        WebDriverWait(driver, 10).until(
            EC.any_of(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.sc-1kg7aw5-0.dgeiTV > button')),  # Success/dashboard
                EC.visibility_of_element_located((By.CSS_SELECTOR, 'div.NewContent__StyledNewErrorCode-q19ga1-5'))    # Failure/error
            )
        )
    except TimeoutException:
        raise Exception("Login verification failed: The page did not load the dashboard or a known error message.")

    # === 6. Error Handling + Robust Logout if needed ===
    try:
        error_element = driver.find_element(By.CSS_SELECTOR, 'div.NewContent__StyledNewErrorCode-q19ga1-5')
        error_text = error_element.text.strip()
        if "User over limit" in error_text:
            if st_module: st_module.warning("Login Failed: User over limit, triggering robust logout.")
            robust_logout_request(driver, st_module)
            raise Exception("Login Failed: The account has reached its login limit.")
        elif "captcha error" in error_text:
            if st_module: st_module.warning("Login Failed: The captcha code was incorrect.")
            raise Exception("Login Failed: Incorrect captcha code.")
        elif "Sorry, your login details are incorrect, please try again." in error_text:
            if st_module: st_module.warning("Login Failed: Incorrect Group, Username, or Password.")
            raise Exception("Login Failed: Wrong credentials.")
        else:
            msg = f"Login Failed: Unrecognized error: '{error_text}'"
            if st_module: st_module.warning(msg)
            raise Exception(msg)
    except NoSuchElementException:
        # No error found = successful login
        if st_module: st_module.write("✅ Login successfully verified.")
        try:
            close_tutorial_modal_ROBUST(driver=driver, wait=wait, status_text=st_module, st_module=st_module)
        except Exception as e:
            if st_module: st_module.warning(f"Could not close tutorial modal: {e}")
        return


@retry_step
def close_tutorial_modal_ROBUST(**kwargs):
    """Close tutorial modal if present"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    status_text = kwargs.get('status_text')
    st = kwargs.get('st_module')
    
    status_text.text("Attempting to close tutorial modal...")
    
    try:
        close_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, '#app-userstarterguide-0 button.close')))
        ActionChains(driver).move_to_element(close_btn).click(close_btn).perform()
        time.sleep(2)
        wait.until(EC.invisibility_of_element_located((By.ID, 'app-userstarterguide-0')))
        status_text.text("Modal closed successfully!")
    except TimeoutException:
        status_text.text("Modal did not appear or was already closed.")
    except Exception as e:
        st.warning(f"Modal could not be closed. Continuing... Error: {e}")

@retry_step
def switch_language_to_traditional_chinese(**kwargs):
    """Switch Wisers interface to traditional Chinese"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')
    
    waffle_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.sc-1kg7aw5-0.dgeiTV > button')))
    waffle_button.click()
    
    lang_toggle = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'li.wo__header__nav__navbar__item.dropdown > a.dropdown-toggle')))
    driver.execute_script("arguments[0].click();", lang_toggle)
    
    trad_chinese_link = wait.until(EC.element_to_be_clickable((By.XPATH, '//a[span[text()="繁體中文"]]')))
    trad_chinese_link.click()
    
    wait.until(EC.staleness_of(waffle_button))
    time.sleep(3)
    return True

# =============================================================================
# SEARCH RESULTS & PAGE INTERACTION
# =============================================================================

@retry_step
def wait_for_search_results(**kwargs):
    """Wait for search results to load and determine if results found"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st_module = kwargs.get('st_module')
    logger = kwargs.get('logger')
    screenshot_dir = (
        kwargs.get("screenshot_dir")
        or os.getenv("WISERS_SCREENSHOT_DIR")
        or os.path.join(".", "artifacts", "screenshots")
    )
    loading_grace_seconds = kwargs.get("loading_grace_seconds", 20)
    verify_no_results_wait = kwargs.get("verify_no_results_wait", 6)

    def _log_info(msg):
        if st_module:
            st_module.write(msg)
        if logger and hasattr(logger, "info"):
            try:
                logger.info(msg)
            except Exception:
                pass

    def _log_warn(msg):
        if st_module:
            st_module.warning(msg)
        if logger and hasattr(logger, "warn"):
            try:
                logger.warn(msg)
            except Exception:
                pass

    def _save_search_screenshot(reason: str):
        if not driver:
            return None
        try:
            os.makedirs(screenshot_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            fname = f"{ts}_{reason}.png"
            local_fp = os.path.join(screenshot_dir, fname)
            img_bytes = driver.get_screenshot_as_png()
            with open(local_fp, "wb") as f:
                f.write(img_bytes)
            if st_module:
                st_module.image(img_bytes, caption=f"{reason} screenshot")
            return local_fp
        except Exception:
            return None

    def _detect_no_article_banner() -> bool:
        try:
            els = driver.find_elements(
                By.XPATH,
                "//h5[contains(text(),'没有文章') or contains(text(),'沒有文章')]"
                " | //div[contains(@class,'empty-result')]"
                " | //div[contains(@class,'no-results')]"
                " | //div[contains(@id,'article-tab') and contains(@class,'tab-pane')]"
                "//h5[contains(text(),'没有文章') or contains(text(),'沒有文章')]"
            )
            return len(els) > 0
        except Exception:
            return False

    def _results_are_empty() -> bool:
        try:
            bar = driver.find_element(
                By.XPATH,
                "//ul[contains(@class,'nav-tabs') and contains(@class,'navbar-nav-pub')]"
            )
            items = bar.find_elements(By.XPATH, "./li[not(contains(@class,'dropdown'))]")
            total = 0
            zeros = 0
            for li in items:
                spans = li.find_elements(By.XPATH, "./a/span")
                for s in spans:
                    txt = s.text.strip()
                    if txt.startswith("(") and txt.endswith(")"):
                        total += 1
                        if txt == "(0)":
                            zeros += 1
                        break
            return total > 0 and total == zeros
        except Exception:
            return False

    def _confirm_no_results() -> bool:
        return _results_are_empty() or _detect_no_article_banner()
    
    try:
        wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR,
            'div.list-group, div.list-group-item, ul.nav-tabs.navbar-nav-pub, .no-results, [class*="empty"]'
        )))
    except TimeoutException:
        raise TimeoutException("Page did not load any known content after search.")
    
    time.sleep(1)  # Brief pause for JS rendering
    
    # Check for results
    result_selectors = [
        'div.list-group-item.no-excerpt',
        'div.list-group-item',
        '.article-main'
    ]
    
    for selector in result_selectors:
        if driver.find_elements(By.CSS_SELECTOR, selector):
            # Guard: if tabs are all zero, treat as no results
            if _confirm_no_results():
                _log_warn("ℹ️ Detected results markup but tab counters are all 0. Verifying...")
                break
            _log_info("✅ Search results found.")
            return True
    
    # If no results yet, allow extra time for loading and double-check empty state
    end_time = time.time() + max(0, loading_grace_seconds)
    last_logged = 0
    while time.time() <= end_time:
        for selector in result_selectors:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                _log_info("✅ Search results found.")
                return True

        if _confirm_no_results():
            _log_warn("ℹ️ No-article signal detected, verifying once more...")
            time.sleep(max(0, verify_no_results_wait))
            for selector in result_selectors:
                if driver.find_elements(By.CSS_SELECTOR, selector):
                    _log_info("✅ Results appeared after verification wait.")
                    return True
            if _confirm_no_results():
                _log_warn("ℹ️ No results confirmed for this query.")
                _save_search_screenshot("no_results_confirmed")
                return False

        now = time.time()
        if now - last_logged > 6:
            _log_info("⏳ Search still loading, waiting a bit longer...")
            last_logged = now
        time.sleep(2)

    # Final check after grace period
    if _confirm_no_results():
        _log_warn("ℹ️ No results confirmed after wait.")
        _save_search_screenshot("no_results_confirmed")
        return False

    # Ambiguous state
    raise Exception("Search page loaded, but content was unrecognized.")

@retry_step
def scroll_to_load_all_content(**kwargs):
    """Scroll to bottom to trigger lazy loading of all content"""
    driver = kwargs.get('driver')
    st_module = kwargs.get('st_module')
    
    max_attempts = 10
    last_height = driver.execute_script("return document.body.scrollHeight")
    
    for attempt in range(max_attempts):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        if st_module:
            st_module.write(f"[Scroll] Pass {attempt+1}: Height {new_height}")
            
        if new_height == last_height:
            break
        last_height = new_height
    
    if st_module:
        st_module.write("Scrolling finished (all content should be loaded now).")
    return True

def wait_for_ajax_complete(driver, timeout=10):
    """Wait for jQuery AJAX calls to complete if jQuery is present"""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return jQuery.active == 0") if d.execute_script("return typeof jQuery != 'undefined'") else True
        )
    except Exception:
        pass  # If jQuery not defined, just continue

@retry_step
def go_back_to_search_form(**kwargs):
    """Return to main search form"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')
    
    re_search_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.media-left > a[href="/wevo/home"]')))
    re_search_button.click()
    
    wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button#toggle-query-execute.btn.btn-primary')))
    time.sleep(3)
    return True


def _clear_tag_editor(container, st_module=None):
    """Clear existing tags in a tag-editor container, best-effort."""
    try:
        delete_buttons = container.find_elements(By.CSS_SELECTOR, "li .tag-editor-delete")
        for btn in delete_buttons:
            try:
                btn.click()
                time.sleep(0.1)
            except Exception:
                continue
    except Exception as e:
        if st_module:
            st_module.warning(f"清理搜索关键词失败: {e}")


def _fill_tag_editor_keyword(driver, container, keyword: str, st_module=None):
    """Enter a single keyword into a tag-editor container."""
    keyword = (keyword or "").strip()
    if not keyword:
        return False

    # First try: visible tag-editor input
    try:
        inputs = container.find_elements(By.CSS_SELECTOR, "input.tag-editor-input")
        if inputs:
            inputs[0].click()
            inputs[0].clear()
            inputs[0].send_keys(keyword)
            inputs[0].send_keys(Keys.ENTER)
            return True
    except Exception:
        pass

    # Second try: click editor and type via ActionChains
    try:
        editor = container.find_element(By.CSS_SELECTOR, "ul.tag-editor")
        editor.click()
        ActionChains(driver).send_keys(keyword).send_keys(Keys.ENTER).perform()
        return True
    except Exception:
        pass

    # Last resort: set hidden textarea value
    try:
        hidden = container.find_element(By.CSS_SELECTOR, "textarea.tag-editor-hidden-src")
        driver.execute_script("arguments[0].value = arguments[1];", hidden, keyword)
        return True
    except Exception as e:
        if st_module:
            st_module.warning(f"输入搜索关键词失败: {e}")
    return False


@retry_step
def search_title_from_home(**kwargs):
    """On /wevo/home, input a title in the main search box and execute search."""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')
    keyword = kwargs.get('keyword')

    home_panel = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div#query-instant")))
    editor_container = home_panel.find_element(By.CSS_SELECTOR, "div.app-query-tageditor-instance")
    _clear_tag_editor(editor_container, st_module=st)
    _fill_tag_editor_keyword(driver, editor_container, keyword, st_module=st)

    search_button = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button#toggle-query-execute.btn.btn-primary"))
    )
    search_button.click()
    return True


@retry_step
def search_title_via_edit_search_modal(**kwargs):
    """On search results page, open '编辑搜索' modal and search by title."""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')
    keyword = kwargs.get('keyword')

    edit_btn = wait.until(
        EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(.,'編輯搜索') or contains(.,'编辑搜索')]")
        )
    )
    edit_btn.click()

    modal_search_btn = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button.edit-search-button-track"))
    )
    modal_root = modal_search_btn.find_element(By.XPATH, "./ancestor::div[contains(@class,'modal')]")

    editor_container = None
    try:
        editor_container = modal_root.find_element(By.CSS_SELECTOR, "div.app-query-tageditor-instance")
    except Exception:
        editor_container = modal_root

    _clear_tag_editor(editor_container, st_module=st)
    _fill_tag_editor_keyword(driver, editor_container, keyword, st_module=st)

    modal_search_btn.click()
    time.sleep(1.5)
    return True

# =============================================================================
# LOGOUT FUNCTIONS
# =============================================================================

@retry_step
def logout(**kwargs):
    """Standard logout process"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')
    
    waffle_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.sc-1kg7aw5-0.dgeiTV > button')))
    waffle_button.click()
    time.sleep(1)
    
    logout_link = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.wo__header__nav__navbar__item:not(.dropdown) a")))
    logout_link.click()
    
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-qa-ci="groupid"]')))

def robust_logout_request(driver, st_module=None):
    """Send robust logout API GET request to forcibly close session"""
    if not driver:
        if st_module:
            st_module.warning("robust_logout_request: driver is None")
        return
    
    if not isinstance(driver, WebDriver):
        if st_module:
            st_module.warning("robust_logout_request requires a selenium WebDriver instance.")
        return
    
    try:
        # Extract session cookies from Selenium driver
        selenium_cookies = driver.get_cookies()
        if st_module:
            st_module.write(f"Found {len(selenium_cookies)} cookies from driver")
            
        session_cookies = {}
        skipped_count = 0
        for cookie in selenium_cookies:
            try:
                cookie['value'].encode('latin-1')  # 測試是否能安全編碼
                session_cookies[cookie['name']] = cookie['value']
            except (UnicodeEncodeError, UnicodeDecodeError, AttributeError):
                skipped_count += 1
                continue

        
        # Get current timestamp for the logout URL
        current_timestamp = int(time.time() * 1000)
        robust_logout_url = (
            "https://wisesearch6.wisers.net/wevo/api/AccountService;criteria=%7B%22groupId%22%3A%22SPRG1%22%2C"
            "%22userId%22%3A%22AsiaNet1%22%2C%22deviceType%22%3A%22web%22%2C%22deviceId%22%3A%22%22%7D;"
            f"path=logout;timestamp={current_timestamp};updateSession=true"
            "?returnMeta=true"
        )
        
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-requested-with": "XMLHttpRequest"
        }
        
        if st_module:
            st_module.write("Sending robust logout request...")
            
        response = requests.get(robust_logout_url, headers=headers, cookies=session_cookies, timeout=10)
        
        if st_module:
            st_module.write(f"Logout response status: {response.status_code}")
            st_module.write(f"Logout response text: {response.text[:200]}...")
            
        if response.ok:
            if st_module:
                st_module.write("✅ Robust logout request sent successfully.")
        else:
            if st_module:
                st_module.warning(f"Robust logout request failed with status: {response.status_code}")
                
    except Exception as e:
        if st_module:
            st_module.warning(f"Exception during robust logout request: {e}")
            st_module.code(traceback.format_exc())
