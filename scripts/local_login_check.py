import argparse
import base64
import os
import sys
import tempfile
import time
from datetime import datetime

import pytz

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from twocaptcha import TwoCaptcha

from utils.config import WISERS_URL
from utils.html_structure_config import HTML_STRUCTURE
from utils.wisers_utils import (
    clear_login_fields,
    reset_to_login_page,
    go_back_to_search_form,
    inject_cjk_font_css,
    setup_webdriver,
    switch_language_to_traditional_chinese,
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


def _is_login_page(driver) -> bool:
    login_cfg = HTML_STRUCTURE.get("login") or {}
    inputs = login_cfg.get("inputs") or {}
    required = [inputs.get("group"), inputs.get("user"), inputs.get("password")]
    submit = login_cfg.get("submit") or []

    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""
    url_ok = ("login.wisers.net" in url) or (
        "wisesearch6.wisers.net" in url and "wevo" in url
    )

    fields_ok = all(_find_first_visible(driver, selectors, timeout=2) for selectors in required)
    submit_ok = _find_first_visible(driver, submit, timeout=2) is not None
    return bool(url_ok and fields_ok and submit_ok)


def _is_home_search_page(driver) -> bool:
    signals = [
        {"by": "css", "value": "button#toggle-query-execute.btn.btn-primary"},
        {"by": "css", "value": "div.app-query-input"},
        {"by": "css", "value": "#accordion-queryfilter"},
    ]
    return _find_first_visible(driver, signals, timeout=2) is not None


def _tutorial_visible(driver) -> bool:
    cfg = (HTML_STRUCTURE.get("home") or {}).get("tutorial_modal") or {}
    if _find_first_visible(driver, cfg.get("root"), timeout=2):
        return True
    markers = cfg.get("text_markers") or []
    if not markers:
        return False
    try:
        return bool(
            driver.execute_script(
                """
                const markers = arguments[0] || [];
                const txt = (document.body && document.body.innerText) || '';
                return markers.some(m => txt.includes(m));
                """,
                markers,
            )
        )
    except Exception:
        return False


def _extract_login_error_text(driver) -> str:
    err_selectors = (HTML_STRUCTURE.get("login") or {}).get("error") or []
    for sel in err_selectors:
        by, value = _selector_to_by(sel)
        if not by or not value:
            continue
        try:
            elements = driver.find_elements(by, value)
        except Exception:
            elements = []
        for el in elements:
            try:
                txt = (el.text or "").strip()
            except Exception:
                txt = ""
            if txt:
                return txt
    return ""


def _safe_current_url(driver) -> str:
    try:
        return driver.current_url or ""
    except Exception as e:
        return f"<current_url_unavailable:{e}>"


def _is_captcha_error(err_text: str) -> bool:
    t = (err_text or "").strip().lower()
    return ("captcha error" in t) or ("驗證碼錯誤" in t) or ("验证码错误" in t)


def _wait_page_idle(driver, timeout_sec: int = 12, poll_sec: float = 0.4) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            ready, jquery_idle, visible_progress = driver.execute_script(
                """
                const ready = document.readyState === 'complete';
                let jqueryIdle = true;
                try {
                  if (window.jQuery) jqueryIdle = (jQuery.active === 0);
                } catch (e) { jqueryIdle = true; }
                let progressVisible = false;
                try {
                  const bars = document.querySelectorAll('div.progress.progress__pageTop');
                  for (const b of bars) {
                    const style = window.getComputedStyle(b);
                    const cls = b.className || '';
                    if (style.display !== 'none' && style.visibility !== 'hidden' && !cls.includes('hide')) {
                      progressVisible = true;
                      break;
                    }
                  }
                } catch (e) {}
                return [ready, jqueryIdle, progressVisible];
                """
            )
            if ready and jquery_idle and (not visible_progress):
                return True
        except Exception:
            pass
        time.sleep(poll_sec)
    return False


def _real_click_at_viewport_point(driver, x: int, y: int) -> bool:
    try:
        ok = driver.execute_script(
            """
            const x = Math.floor(arguments[0]), y = Math.floor(arguments[1]);
            const target = document.elementFromPoint(x, y) || document.body || document.documentElement;
            if (!target) return false;
            target.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
            return true;
            """,
            x,
            y,
        )
        if not ok:
            return False
        html = driver.find_element(By.TAG_NAME, "html")
        # Selenium pointer actions: close to real mouse down/up sequence.
        ActionChains(driver).move_to_element_with_offset(html, x, y).click_and_hold().pause(0.05).release().perform()
        return True
    except Exception:
        try:
            # JS fallback still dispatches full mouse sequence at exact coordinates.
            return bool(
                driver.execute_script(
                    """
                    const x = arguments[0], y = arguments[1];
                    const target = document.elementFromPoint(x, y) || document.body || document.documentElement;
                    if (!target) return false;
                    ['mousedown', 'mouseup', 'click'].forEach(type => {
                      target.dispatchEvent(new MouseEvent(type, {
                        bubbles: true, cancelable: true, button: 0, clientX: x, clientY: y
                      }));
                    });
                    return true;
                    """,
                    x,
                    y,
                )
            )
        except Exception:
            return False


def _wait_heartbeat(
    driver,
    stage_name: str,
    check_fn,
    timeout_sec: int = 60,
    interval_sec: int = 5,
    fallback_url: str = "",
):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        state, detail = check_fn()
        _print(f"[heartbeat] {stage_name}: state={state} detail={detail}")
        if state == "success":
            return detail
        if state == "failure":
            raise RuntimeError(f"{stage_name} failed: {detail}")
        time.sleep(interval_sec)

    _print(f"[heartbeat] {stage_name}: timeout after {timeout_sec}s")
    if fallback_url:
        try:
            driver.get(fallback_url)
            _print(f"[heartbeat] {stage_name}: fallback to {fallback_url}")
        except Exception as e:
            _print(f"[heartbeat] {stage_name}: fallback failed: {e}")
    raise TimeoutError(f"{stage_name} timeout after {timeout_sec}s")


def _perform_login_once_without_retry(
    driver,
    wait,
    group_name: str,
    username: str,
    password: str,
    api_key: str,
):
    try:
        if _is_home_search_page(driver):
            _print("Already in post-login state; skip login form submit.")
            return
    except Exception:
        pass

    reset_to_login_page(driver, st_module=None)
    clear_login_fields(driver, wait=wait, st_module=None)

    group_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='groupid']")))
    user_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='userid']")))
    pass_elem = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='password']")))

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

    try:
        captcha_img = driver.find_element(By.CSS_SELECTOR, "img.CaptchaField__CaptchaImage-hffgxm-5")
        captcha_src = captcha_img.get_attribute("src")
        img_data = base64.b64decode(captcha_src.split(",")[1])
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp_captcha:
            tmp_captcha.write(img_data)
            tmp_path = tmp_captcha.name
        solver = TwoCaptcha(api_key)
        captcha_text = solver.normal(tmp_path)["code"]
        os.remove(tmp_path)
        driver.find_element(By.CSS_SELECTOR, "input.CaptchaField__Input-hffgxm-4").send_keys(captcha_text)
    except Exception as captcha_error:
        raise Exception(f"Failed during 2Captcha solving process: {captcha_error}")

    login_btn = driver.find_element(By.CSS_SELECTOR, "input[data-qa-ci='button-login']")
    login_btn.click()


def _refresh_and_reset_login_form(driver, wait) -> None:
    try:
        driver.refresh()
    except Exception:
        pass
    time.sleep(1.0)
    reset_to_login_page(driver, st_module=None)
    clear_login_fields(driver, wait=wait, st_module=None)


def _click_anywhere_to_close_tutorial(driver) -> bool:
    if not _tutorial_visible(driver):
        return True
    _wait_page_idle(driver, timeout_sec=10)

    # Build points guaranteed outside modal content box, then supplement with red-zone ratios.
    probe_points = []
    try:
        modal_rect = driver.execute_script(
            """
            const candidates = [
              '#app-userstarterguide-0 .modal-content',
              '#app-userstarterguide-0 .modal-dialog',
              '.carousel-userstarterguide .modal-content',
              '.carousel-userstarterguide .modal-dialog',
              'div.modal.in .modal-content',
              'div.modal.in .modal-dialog'
            ];
            let el = null;
            for (const sel of candidates) {
              el = document.querySelector(sel);
              if (el) break;
            }
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {left:r.left, right:r.right, top:r.top, bottom:r.bottom, w:window.innerWidth, h:window.innerHeight};
            """
        )
        if modal_rect:
            w = int(modal_rect.get("w") or 0)
            h = int(modal_rect.get("h") or 0)
            left = int(modal_rect.get("left") or 0)
            right = int(modal_rect.get("right") or 0)
            top = int(modal_rect.get("top") or 0)
            bottom = int(modal_rect.get("bottom") or 0)
            margin = 20
            probe_points.extend([
                (max(10, left - margin), max(10, top + 40)),                      # left of modal
                (max(10, left - margin), min(h - 10, bottom - 40)),               # left-lower
                (min(w - 10, right + margin), max(10, top + 40)),                 # right of modal
                (min(w - 10, right + margin), min(h - 10, bottom - 40)),          # right-lower
                (max(10, left + 30), max(10, top - margin)),                      # above modal
                (min(w - 10, right - 30), max(10, top - margin)),                 # above-right
                (max(10, left + 30), min(h - 10, bottom + margin)),               # below modal
                (min(w - 10, right - 30), min(h - 10, bottom + margin)),          # below-right
            ])
    except Exception:
        pass

    if not probe_points:
        for rx, ry in [
            (0.05, 0.18), (0.05, 0.50), (0.05, 0.80),
            (0.95, 0.18), (0.95, 0.50), (0.95, 0.80),
            (0.12, 0.08), (0.88, 0.08),
        ]:
            try:
                x, y = driver.execute_script(
                    "return [Math.max(10, Math.floor(window.innerWidth*arguments[0])), Math.max(10, Math.floor(window.innerHeight*arguments[1]))];",
                    rx,
                    ry,
                )
                probe_points.append((int(x), int(y)))
            except Exception:
                continue

    for idx, (x, y) in enumerate(probe_points, start=1):
        try:
            target_desc, in_content = driver.execute_script(
                """
                const x = arguments[0], y = arguments[1];
                const target = document.elementFromPoint(x, y) || document.body || document.documentElement;
                const content = document.querySelector('#app-userstarterguide-0 .modal-content')
                  || document.querySelector('.carousel-userstarterguide .modal-content')
                  || document.querySelector('div.modal.in .modal-content');
                let inContent = false;
                if (content) {
                  const r = content.getBoundingClientRect();
                  inContent = (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
                }
                if (!target) return ['no-target', inContent];

                let desc = target.tagName ? target.tagName.toLowerCase() : 'unknown';
                if (target.className) {
                  const cls = String(target.className).trim().split(/\\s+/).slice(0, 2).join('.');
                  if (cls) desc += '.' + cls;
                }
                return [desc, inContent];
                """,
                x,
                y,
            )
            if in_content:
                _print(f"Tutorial close attempt #{idx}: point=({x},{y}) target={target_desc} skipped=in-modal-content")
                continue
            clicked = _real_click_at_viewport_point(driver, x, y)
            _print(f"Tutorial close attempt #{idx}: point=({x},{y}) target={target_desc} real_click={clicked}")
        except Exception:
            _print(f"Tutorial close attempt #{idx}: point=({x},{y}) click_exception")

        time.sleep(0.8)
        if not _tutorial_visible(driver):
            return True
    return not _tutorial_visible(driver)


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


def _attempt_logout_in_context(driver, wait) -> tuple[bool, str]:
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
            return False, "toggle_not_found"
    else:
        try:
            driver.execute_script("arguments[0].click();", toggle)
        except Exception:
            try:
                toggle.click()
            except Exception:
                return False, "toggle_click_failed"

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
            return False, "menu_not_found"

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
                return False, "logout_link_not_found"
        except Exception:
            return False, "logout_link_not_found"
    else:
        try:
            driver.execute_script("arguments[0].click();", logout_btn)
        except Exception:
            try:
                logout_btn.click()
            except Exception:
                return False, "logout_click_failed"

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='groupid']")))
        return True, "ok"
    except Exception:
        return False, "login_page_not_detected"


def _attempt_logout_via_wo_grid_modal(driver, wait) -> tuple[bool, str]:
    _wait_page_idle(driver, timeout_sec=10)
    try:
        clicked_grid = driver.execute_script(
            """
            const candidates = [];
            candidates.push(...Array.from(document.querySelectorAll('button.wo__header__nav__navbar__grid')));
            candidates.push(...Array.from(document.querySelectorAll('div.wo__header__nav__navbar--grid button')));
            candidates.push(...Array.from(document.querySelectorAll('button, a')).filter(el => {
              const p = el.querySelector && el.querySelector('svg path');
              if (!p) return false;
              const d = (p.getAttribute('d') || '').replace(/\\s+/g,' ');
              if (!d.includes('M128 348v72')) return false;
              const r = el.getBoundingClientRect();
              return r.width > 0 && r.height > 0 && r.top >= 0 && r.left >= 0 && r.left < 260;
            }));
            for (const el of candidates) {
              try { el.click(); return true; } catch (e) {}
            }
            return false;
            """
        )
        if not clicked_grid:
            return False, "wo_grid_button_not_found"
    except Exception:
        return False, "wo_grid_button_click_failed"

    _wait_page_idle(driver, timeout_sec=10)
    modal = _find_first_visible(
        driver,
        [
            {"by": "css", "value": "div.modal.fade.in[style*='display: block']"},
            {"by": "css", "value": "div.modal.in"},
            {"by": "xpath", "value": "//div[contains(@class,'modal') and contains(@class,'in')]"},
        ],
        timeout=8,
    )
    if not modal:
        return False, "wo_grid_modal_not_found"

    logout_btn = _find_first_visible(
        driver,
        [
            {
                "by": "xpath",
                "value": "//div[contains(@class,'modal') and contains(@class,'in')]//ul[contains(@class,'wo__header__nav__navbar--list')]/li[not(contains(@class,'dropdown'))]/a[contains(@class,'wo__header__nav__navbar__item__link')]",
            },
            {
                "by": "xpath",
                "value": "//div[contains(@class,'modal') and contains(@class,'in')]//a[contains(@class,'wo__header__nav__navbar__item__link')][.//svg/path[contains(@d,'M397 269l-54 63')]]",
            },
        ],
        timeout=8,
    )
    if not logout_btn:
        return False, "wo_logout_button_not_found"

    try:
        driver.execute_script("arguments[0].click();", logout_btn)
    except Exception:
        try:
            logout_btn.click()
        except Exception:
            return False, "wo_logout_button_click_failed"

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='groupid']")))
        return True, "ok"
    except Exception:
        return False, "wo_login_page_not_detected"


def _logout_via_user_menu(driver, wait) -> tuple[bool, str]:
    ok, reason = _attempt_logout_in_context(driver, wait)
    if ok:
        return True, "ok"

    try:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        iframes = []
    for idx, frame in enumerate(iframes):
        try:
            driver.switch_to.frame(frame)
            ok, _reason = _attempt_logout_in_context(driver, wait)
            if ok:
                driver.switch_to.default_content()
                return True, "ok"
        except Exception:
            pass
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    return False, reason


def _logout_with_fallback(driver, wait) -> tuple[bool, str]:
    ok, reason = _attempt_logout_via_wo_grid_modal(driver, wait)
    if ok:
        return True, "ok_via_wo_grid"
    _print(f"Primary logout path unavailable: {reason}; fallback to user-menu path.")
    ok2, reason2 = _logout_via_user_menu(driver, wait)
    if ok2:
        return True, "ok_via_user_menu"
    return False, f"{reason} | {reason2}"


def _close_tutorial_if_present(driver, _wait) -> bool:
    return _click_anywhere_to_close_tutorial(driver)


class _NullStatus:
    def text(self, *_args, **_kwargs):
        return None


def _refresh_and_confirm_logout(driver, wait) -> bool:
    try:
        driver.refresh()
    except Exception:
        try:
            driver.get(WISERS_URL)
        except Exception:
            pass
    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-qa-ci='groupid']")),
                EC.url_contains("timeout"),
            )
        )
        return True
    except Exception:
        return False


def _is_timeout_page(driver, wait) -> bool:
    timeout_cfg = HTML_STRUCTURE.get("timeout") or {}
    url = timeout_cfg.get("url") or ""
    title_selectors = timeout_cfg.get("title") or []
    btn_selectors = timeout_cfg.get("logout_button") or []

    if url:
        try:
            driver.get(url)
        except Exception:
            pass

    title_el = _find_first_visible(driver, title_selectors, timeout=6)
    if title_el:
        return True
    btn_el = _find_first_visible(driver, btn_selectors, timeout=6)
    if btn_el:
        return True

    # Fallback: URL contains "timeout"
    try:
        return "timeout" in (driver.current_url or "").lower()
    except Exception:
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
    parser.set_defaults(headless=False)
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        help="Run Chrome in headless mode.",
    )
    parser.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Run Chrome with UI (default).",
    )
    parser.add_argument(
        "--stay-open",
        dest="stay_open",
        action="store_true",
        default=True,
        help="Keep browser open after script flow completes (default: true).",
    )
    parser.add_argument(
        "--no-stay-open",
        dest="stay_open",
        action="store_false",
        help="Allow script to exit without keeping browser open.",
    )
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
        driver = setup_webdriver(headless=args.headless, st_module=None)
        if not driver:
            _print("WebDriver setup failed.")
            return 3

        wait = WebDriverWait(driver, 20)

        _print(f"Opening login URL: {WISERS_URL}")
        driver.get(WISERS_URL)
        try:
            inject_cjk_font_css(driver, st_module=None)
            _print("CJK font CSS injected (best-effort).")
        except Exception as e:
            _print(f"CJK font CSS inject skipped: {e}")

        _print("Heartbeat check: waiting for login page signature...")
        _wait_heartbeat(
            driver=driver,
            stage_name="pre-login-page-check",
            timeout_sec=60,
            interval_sec=5,
            check_fn=lambda: (
                ("success", "login_page_detected")
                if _is_login_page(driver)
                else ("pending", f"url={_safe_current_url(driver)}")
            ),
        )

        post_login_result = ""
        max_login_rounds = 2  # Initial login + one captcha-error re-login.
        for login_round in range(1, max_login_rounds + 1):
            _print(f"Performing login round {login_round}/{max_login_rounds}...")
            _perform_login_once_without_retry(
                driver=driver,
                wait=wait,
                group_name=args.group,
                username=args.user,
                password=args.password,
                api_key=args.captcha_api_key,
            )

            _print("Heartbeat check: waiting for post-login result (success or error)...")
            try:
                post_login_result = _wait_heartbeat(
                    driver=driver,
                    stage_name="post-login-check",
                    timeout_sec=60,
                    interval_sec=5,
                    check_fn=lambda: (
                        ("failure", f"login_error={err}")
                        if (err := _extract_login_error_text(driver))
                        else (
                            ("success", "home+tutorial")
                            if (_is_home_search_page(driver) and _tutorial_visible(driver))
                            else (
                                ("success", "home_without_tutorial")
                                if _is_home_search_page(driver)
                                else ("pending", f"url={_safe_current_url(driver)}")
                            )
                        )
                    ),
                    fallback_url="https://wisesearch6.wisers.net/wevo/home",
                )
                break
            except RuntimeError as e:
                err_text = _extract_login_error_text(driver)
                if _is_captcha_error(err_text) and login_round < max_login_rounds:
                    _print("Detected captcha error. Refreshing page and resetting blank login form before retry...")
                    _refresh_and_reset_login_form(driver, wait)
                    _wait_heartbeat(
                        driver=driver,
                        stage_name="captcha-retry-login-page-check",
                        timeout_sec=60,
                        interval_sec=5,
                        check_fn=lambda: (
                            ("success", "login_page_detected")
                            if _is_login_page(driver)
                            else ("pending", f"url={_safe_current_url(driver)}")
                        ),
                    )
                    continue
                raise e
        _print(f"Post-login heartbeat result: {post_login_result}")

        _print("Switching UI language to 繁體中文...")
        try:
            switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=None)
        except Exception as e:
            _print(f"Language switch failed (continuing): {e}")

        _print("Navigating to home search page...")
        go_back_to_search_form(driver=driver, wait=wait, st_module=None)

        _print("Closing tutorial by clicking anywhere on page...")
        closed = _close_tutorial_if_present(driver, wait)
        _print(f"Tutorial modal closed: {closed}")

        _print("Home page detected. Writing HTML snapshot...")
        _write_html_snapshot(driver, args.html_out)
        _print(f"HTML snapshot saved: {args.html_out}")
        _print("Writing homepage screenshot...")
        try:
            inject_cjk_font_css(driver, st_module=None)
        except Exception:
            pass
        _write_screenshot(driver, args.screenshot_out)
        _print(f"Homepage screenshot saved: {args.screenshot_out}")

        _print("Preparing logout: ensure home page and probe available logout entry...")
        try:
            go_back_to_search_form(driver=driver, wait=wait, st_module=None)
        except Exception:
            pass
        has_wo_grid = _find_first_visible(
            driver,
            [
                {"by": "css", "value": "button.wo__header__nav__navbar__grid"},
                {"by": "css", "value": "div.wo__header__nav__navbar--grid button"},
                {"by": "xpath", "value": "//button[.//svg/path[contains(@d,'M128 348v72')]]"},
            ],
            timeout=2,
        ) is not None
        has_legacy_menu = _find_first_visible(
            driver,
            (HTML_STRUCTURE.get("home") or {}).get("user_menu", {}).get("toggle_closed"),
            timeout=2,
        ) is not None
        _print(f"Logout entry probe: wo_grid={has_wo_grid}, legacy_user_menu={has_legacy_menu}")

        _print("Logging out (primary: wo-grid modal, fallback: legacy user menu)...")
        ok, reason = _logout_with_fallback(driver, wait)
        if ok:
            _print("Logged out successfully.")
        else:
            _print(f"Logout failed: {reason}")
        _print("Heartbeat check: waiting for login page after logout...")
        _wait_heartbeat(
            driver=driver,
            stage_name="post-logout-check",
            timeout_sec=60,
            interval_sec=5,
            check_fn=lambda: (
                ("success", "login_page_detected")
                if _is_login_page(driver)
                else ("pending", f"url={_safe_current_url(driver)}")
            ),
        )
        _print("Logout confirmed by heartbeat check.")
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
            if args.stay_open:
                _print("Browser stays open. Press Ctrl+C to stop this script.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    _print("Received Ctrl+C; exiting script without quitting browser.")


if __name__ == "__main__":
    raise SystemExit(main())
