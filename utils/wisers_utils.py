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

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.webdriver import WebDriver
from twocaptcha import TwoCaptcha

from .config import WISERS_URL

from utils.firebase_logging import get_logger

# =============================================================================
# RETRY DECORATOR
# =============================================================================

def retry_step(func):
    """Retry decorator for Wisers functions - handles screenshots and logout on failure"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        st = kwargs.get('st_module')
        driver = kwargs.get('driver')
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
                
                # Screenshot on failure
                if driver and st:
                    try:
                        img_bytes = driver.get_screenshot_as_png()
                        st.image(img_bytes, caption=f"Screencap after failure in {func.__name__}, attempt {trial}")
                        st.download_button(
                            label=f"Download {func.__name__}_attempt{trial}_screenshot.png",
                            data=img_bytes,
                            file_name=f"{func.__name__}_attempt{trial}_screenshot.png",
                            mime="image/png"
                        )
                        fb = get_logger()
                        if fb:
                            uri = fb.upload_screenshot(img_bytes, name_hint=f"{func.__name__}_attempt{trial}")
                            fb.info("screenshot_uploaded", step=func.__name__, attempt=trial, uri=uri)

                    except Exception as screencap_err:
                        if st:
                            st.warning(f"Screencap failed: {screencap_err}")
                
                time.sleep(2)
                
                if trial == retry_limit:
                    if st:
                        st.error(f"❌ Step {func.__name__} failed after {retry_limit} attempts.")
                    
                    # Robust logout on final failure
                    try:
                        if driver:
                            robust_logout_request(driver=driver, st_module=st)
                        elif st:
                            st.warning("Driver not available for robust logout request.")
                    except Exception as logout_err:
                        if st:
                            st.warning(f"Robust logout request failed: {logout_err}")
                    
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
        driver.get(WISERS_URL)
        
        if st_module:
            st_module.write("✅ WebDriver setup complete.")
            
        return driver
        
    except Exception as e:
        if st_module:
            st_module.error(f"WebDriver setup failed: {e}")
        return None

def reset_to_login_page(driver):
    try:
        driver.delete_all_cookies()
        driver.get(WISERS_URL)
        time.sleep(2)
        # Attempt to force logout if available
        try:
            robust_logout_request(driver)
        except Exception as e:
            print("Robust logout failed/prelogin:", e)
    except Exception as e:
        print("Pre-login reset failed:", e)


@retry_step
def perform_login(**kwargs):
    """Perform login to Wisers with captcha solving"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    group_name = kwargs.get('group_name')
    username = kwargs.get('username')
    password = kwargs.get('password')
    api_key = kwargs.get('api_key')
    st_module = kwargs.get('st_module')
    
    # Ensure on login page
    st_module.write("Resetting to login page...")
    reset_to_login_page(driver)

    # Fill login form
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[data-qa-ci="groupid"]'))).send_keys(group_name)
    driver.find_element(By.CSS_SELECTOR, 'input[data-qa-ci="userid"]').send_keys(username)
    driver.find_element(By.CSS_SELECTOR, 'input[data-qa-ci="password"]').send_keys(password)
    
    # Solve captcha
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
    
    # Submit login
    login_btn = driver.find_element(By.CSS_SELECTOR, 'input[data-qa-ci="button-login"]')
    login_btn.click()
    
    # Verify login success/failure
    try:
        WebDriverWait(driver, 10).until(
            EC.any_of(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.sc-1kg7aw5-0.dgeiTV > button')),  # Success
                EC.visibility_of_element_located((By.CSS_SELECTOR, 'div.NewContent__StyledNewErrorCode-q19ga1-5'))  # Failure
            )
        )
    except TimeoutException:
        raise Exception("Login verification failed: The page did not load the dashboard or a known error message.")
    
    # Check for error messages
    try:
        error_element = driver.find_element(By.CSS_SELECTOR, 'div.NewContent__StyledNewErrorCode-q19ga1-5')
        error_text = error_element.text.strip()
        
        if "User over limit" in error_text:
            msg = "Login Failed: The account has reached its login limit. It is likely already logged in elsewhere."
        elif "captcha error" in error_text:
            msg = "Login Failed: The captcha code was incorrect."
        elif "Sorry, your login details are incorrect, please try again." in error_text:
            msg = "Login Failed: Incorrect Group, Username, or Password."
        else:
            msg = f"Login Failed: An unrecognized error appeared: '{error_text}'"
            
        if st_module:
            st_module.warning(msg)
        raise Exception(msg)
        
    except NoSuchElementException:
        # No error found = successful login
        if st_module:
            st_module.write("✅ Login successfully verified.")
        # NEW: Try to close tutorial modal if present
        try:
            close_tutorial_modal_ROBUST(driver=driver, wait=wait, status_text=st_module, st_module=st_module)
        except Exception as e:
            if st_module:
                st_module.warning(f"Could not close tutorial modal: {e}")
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
    
    try:
        wait.until(EC.presence_of_element_located((
            By.CSS_SELECTOR,
            'div.list-group, div.list-group-item, .no-results, [class*="empty"]'
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
            if st_module:
                st_module.write("✅ Search results found.")
            return True
    
    # Check for no results
    no_results_selectors = [
        ".no-results",
        "[class*='no-result']",
        "[class*='empty']"
    ]
    
    for selector in no_results_selectors:
        if driver.find_elements(By.CSS_SELECTOR, selector):
            if st_module:
                st_module.warning("ℹ️ No results found for this query.")
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
            
        session_cookies = {cookie['name']: cookie['value'] for cookie in selenium_cookies}
        
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
