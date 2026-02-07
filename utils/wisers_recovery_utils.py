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
