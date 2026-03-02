import argparse
import os
import sys
from datetime import datetime

import pytz

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from utils.config import WISERS_URL
from utils.html_structure_config import HTML_STRUCTURE
from utils.wisers_utils import (
    go_back_to_search_form,
    perform_login,
    robust_logout_request,
    setup_webdriver,
)


HKT = pytz.timezone("Asia/Hong_Kong")


def _now_hkt_str() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")


def _print(msg: str):
    print(f"[{_now_hkt_str()}] {msg}", flush=True)


def _load_toml_file(path: str) -> dict:
    try:
        import tomllib  # py>=3.11
    except Exception:  # pragma: no cover
        import tomli as tomllib  # type: ignore

    with open(path, "rb") as f:
        return tomllib.load(f)


def _maybe_apply_secrets_toml(secrets_path: str, args) -> None:
    if not secrets_path:
        return
    if not os.path.exists(secrets_path):
        return

    sec = _load_toml_file(secrets_path) or {}
    wisers = sec.get("wisers", {}) or {}

    if not args.group:
        args.group = wisers.get("group_name", "") or ""
    if not args.user:
        args.user = wisers.get("username", "") or ""
    if not args.password:
        args.password = wisers.get("password", "") or ""
    if not args.captcha_api_key:
        args.captcha_api_key = wisers.get("api_key", "") or ""


def _selector_to_by(selector_def):
    by = (selector_def or {}).get("by")
    value = (selector_def or {}).get("value")
    if not by or not value:
        return None, None
    by_map = {
        "css": By.CSS_SELECTOR,
        "xpath": By.XPATH,
        "id": By.ID,
        "name": By.NAME,
    }
    return by_map.get(by), value


def _find_first_visible(driver, selectors, timeout=6):
    for sel in selectors or []:
        by, value = _selector_to_by(sel)
        if not by or not value:
            continue
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((by, value))
            )
            if el:
                return el
        except Exception:
            continue
    return None


def _write_html_snapshot(driver, output_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(driver.page_source or "")
    except Exception as e:
        _print(f"Failed to write HTML snapshot: {e}")


def _write_screenshot(driver, output_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        driver.save_screenshot(output_path)
    except Exception as e:
        _print(f"Failed to write screenshot: {e}")


def _attempt_logout_in_context(driver, wait) -> bool:
    cfg = (HTML_STRUCTURE.get("home") or {}).get("user_menu") or {}
    toggle = _find_first_visible(driver, cfg.get("toggle_closed"), timeout=6)
    if not toggle:
        toggle = _find_first_visible(driver, cfg.get("toggle_open"), timeout=3)
    if not toggle:
        try:
            driver.execute_script(
                """
                const icon = document.querySelector('i.wf.wf-user');
                if (!icon) return false;
                const btn = icon.closest('a.dropdown-toggle') || icon.closest('a');
                if (!btn) return false;
                btn.click();
                return true;
                """
            )
        except Exception:
            return False
    else:
        try:
            driver.execute_script("arguments[0].click();", toggle)
        except Exception:
            try:
                toggle.click()
            except Exception:
                return False

    menu = _find_first_visible(driver, cfg.get("menu"), timeout=6)
    if not menu:
        try:
            driver.execute_script(
                """
                const menus = document.querySelectorAll('ul.dropdown-menu');
                for (const m of menus) {
                  const style = window.getComputedStyle(m);
                  if (style && style.display !== 'none' && style.visibility !== 'hidden') return true;
                }
                return false;
                """
            )
        except Exception:
            return False

    logout_btn = _find_first_visible(driver, cfg.get("logout"), timeout=6)
    if not logout_btn:
        try:
            clicked = driver.execute_script(
                """
                const items = Array.from(document.querySelectorAll('a'));
                const target = items.find(a => (a.textContent || '').includes('退出登錄') || (a.textContent || '').includes('退出登录'));
                if (target) { target.click(); return true; }
                return false;
                """
            )
            if not clicked:
                return False
        except Exception:
            return False
    else:
        try:
            driver.execute_script("arguments[0].click();", logout_btn)
        except Exception:
            try:
                logout_btn.click()
            except Exception:
                return False

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='groupid']")))
        return True
    except Exception:
        return False


def _logout_via_user_menu(driver, wait) -> bool:
    if _attempt_logout_in_context(driver, wait):
        return True

    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        iframes = []
    for idx, frame in enumerate(iframes):
        try:
            driver.switch_to.frame(frame)
            if _attempt_logout_in_context(driver, wait):
                driver.switch_to.default_content()
                return True
        except Exception:
            pass
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Local Wisers login check: verify home HTML after login."
    )
    parser.add_argument(
        "--secrets-toml",
        default="",
        help="Path to local secrets toml (Streamlit-compatible). If omitted, will try secrets.local.toml then .streamlit/secrets.toml.",
    )
    parser.add_argument("--group", default=os.getenv("WISERS_GROUP_NAME") or os.getenv("WISERS_GROUP") or "")
    parser.add_argument("--user", default=os.getenv("WISERS_USERNAME") or os.getenv("WISERS_USER") or "")
    parser.add_argument("--password", default=os.getenv("WISERS_PASSWORD") or "")
    parser.add_argument("--captcha-api-key", default=os.getenv("WISERS_2CAPTCHA_KEY") or os.getenv("WISERS_API_KEY") or "")
    parser.add_argument("--headed", action="store_true", help="Run Chrome with UI (not headless).")
    parser.add_argument(
        "--html-out",
        default=os.path.join(REPO_ROOT, "artifacts", "wisers_home.html"),
        help="Path to write homepage HTML snapshot.",
    )
    parser.add_argument(
        "--screenshot-out",
        default=os.path.join(REPO_ROOT, "artifacts", "wisers_home.png"),
        help="Path to write homepage screenshot.",
    )

    args = parser.parse_args(argv)

    secrets_candidates = []
    if args.secrets_toml:
        secrets_candidates.append(args.secrets_toml)
    else:
        secrets_candidates.extend([
            os.path.join(REPO_ROOT, "secrets.local.toml"),
            os.path.join(REPO_ROOT, ".streamlit", "secrets.toml"),
        ])
    for p in secrets_candidates:
        _maybe_apply_secrets_toml(p, args)

    if not (args.group and args.user and args.password and args.captcha_api_key):
        _print("Missing Wisers login parameters. Provide via args or env:")
        _print("  --group / WISERS_GROUP_NAME")
        _print("  --user / WISERS_USERNAME")
        _print("  --password / WISERS_PASSWORD")
        _print("  --captcha-api-key / WISERS_2CAPTCHA_KEY")
        return 2

    driver = None
    try:
        _print("Setting up WebDriver...")
        driver = setup_webdriver(headless=not args.headed, st_module=None)
        if not driver:
            _print("WebDriver setup failed.")
            return 3

        wait = WebDriverWait(driver, 20)

        _print(f"Opening login URL: {WISERS_URL}")
        driver.get(WISERS_URL)

        _print("Performing login...")
        perform_login(
            driver=driver,
            wait=wait,
            group_name=args.group,
            username=args.user,
            password=args.password,
            api_key=args.captcha_api_key,
            st_module=None,
        )

        _print("Navigating to home search page...")
        go_back_to_search_form(driver=driver, wait=wait, st_module=None)

        _print("Home page detected. Writing HTML snapshot...")
        _write_html_snapshot(driver, args.html_out)
        _print(f"HTML snapshot saved: {args.html_out}")
        _print("Writing homepage screenshot...")
        _write_screenshot(driver, args.screenshot_out)
        _print(f"Homepage screenshot saved: {args.screenshot_out}")

        _print("Logging out via user menu...")
        if _logout_via_user_menu(driver, wait):
            _print("Logged out successfully.")
            return 0
        _print("Logout via user menu failed.")
        return 0
    except Exception as e:
        _print(f"Login check failed: {e}")
        try:
            if driver:
                _write_html_snapshot(driver, args.html_out)
                _print(f"HTML snapshot saved: {args.html_out}")
        except Exception:
            pass
        return 1
    finally:
        if driver:
            try:
                robust_logout_request(driver, None)
            except Exception:
                pass
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
