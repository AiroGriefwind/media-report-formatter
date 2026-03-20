import argparse
import os
import sys
import time
from datetime import datetime

import pytz
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.config import WISERS_URL
from utils.html_structure_config import HTML_STRUCTURE
from utils.wisers_utils import (
    clear_login_fields,
    go_back_to_search_form,
    inject_cjk_font_css,
    perform_login,
    reset_to_login_page,
    setup_webdriver,
    wait_for_results_panel_ready,
)
from utils.web_scraping_utils import (
    ensure_search_results_ready,
    perform_author_search,
    scrape_author_article_content,
)

HKT = pytz.timezone("Asia/Hong_Kong")
HOME_CANDIDATE_URLS = [
    "https://vertical-new-search.wisersone.com/wevo/home",
    "https://wisesearch6.wisers.net/wevo/home",
]


def _now_hkt_str() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S")


def _print(msg: str):
    print(f"[{_now_hkt_str()}] {msg}", flush=True)


def _load_toml_file(path: str) -> dict:
    try:
        import tomllib
    except Exception:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    with open(path, "rb") as f:
        return tomllib.load(f)


def _maybe_apply_secrets_toml(secrets_path: str, args) -> None:
    if not secrets_path or (not os.path.exists(secrets_path)):
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


def _safe_current_url(driver) -> str:
    try:
        return driver.current_url or ""
    except Exception as e:
        return f"<current_url_unavailable:{e}>"


def _is_login_page(driver) -> bool:
    login_cfg = HTML_STRUCTURE.get("login") or {}
    inputs = login_cfg.get("inputs") or {}
    required = [inputs.get("group"), inputs.get("user"), inputs.get("password")]
    submit = login_cfg.get("submit") or []
    try:
        url = (driver.current_url or "").lower()
    except Exception:
        url = ""
    url_ok = ("login.wisers.net" in url) or ("wisersone.com" in url and "home" in url)
    fields_ok = all(_find_first_visible(driver, selectors, timeout=2) for selectors in required)
    submit_ok = _find_first_visible(driver, submit, timeout=2) is not None
    return bool(url_ok and fields_ok and submit_ok)


def _is_home_search_page(driver) -> bool:
    for sel in (
        "button#toggle-query-execute.btn.btn-primary",
        "div.app-query-input",
        "#accordion-queryfilter",
    ):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            els = []
        for el in els:
            try:
                if el.is_displayed():
                    return True
            except Exception:
                continue
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


def _wait_heartbeat(stage_name: str, check_fn, timeout_sec: int = 60, interval_sec: int = 5):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        state, detail = check_fn()
        _print(f"[heartbeat] {stage_name}: state={state} detail={detail}")
        if state == "success":
            return detail
        if state == "failure":
            raise RuntimeError(f"{stage_name} failed: {detail}")
        time.sleep(interval_sec)
    raise TimeoutError(f"{stage_name} timeout after {timeout_sec}s")


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


def _is_tutorial_active(driver) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const roots = [
                  document.querySelector('#app-userstarterguide-0'),
                  document.querySelector('.carousel-userstarterguide'),
                  document.querySelector('#carousel-userstarterguide')
                ].filter(Boolean);
                const isVisible = (el) => {
                  if (!el) return false;
                  const style = window.getComputedStyle(el);
                  if (!style) return false;
                  const r = el.getBoundingClientRect();
                  return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && r.width > 10 && r.height > 10;
                };
                if (roots.some(isVisible)) return true;
                const txt = (document.body && document.body.innerText) || '';
                return txt.includes('Enter tutorial mode') || txt.includes('教程结束') || txt.includes('接下来向您介绍平台功能');
                """
            )
        )
    except Exception:
        return False


def _close_tutorial_by_overlay_click(driver) -> bool:
    if not _is_tutorial_active(driver):
        return True
    _wait_page_idle(driver, timeout_sec=8)

    for i in range(1, 7):
        try:
            clicked = driver.execute_script(
                """
                const points = [
                  [0.05, 0.20], [0.05, 0.80], [0.95, 0.20],
                  [0.95, 0.80], [0.05, 0.50], [0.95, 0.50]
                ];
                const p = points[Math.min(points.length - 1, arguments[0] - 1)];
                const x = Math.max(10, Math.floor(window.innerWidth * p[0]));
                const y = Math.max(10, Math.floor(window.innerHeight * p[1]));
                const target = document.elementFromPoint(x, y) || document.body || document.documentElement;
                if (!target) return false;
                ['mousedown','mouseup','click'].forEach(type => {
                  target.dispatchEvent(new MouseEvent(type, {
                    bubbles:true, cancelable:true, button:0, clientX:x, clientY:y
                  }));
                });
                return true;
                """,
                i,
            )
            _print(f"Tutorial close click #{i}, clicked={clicked}")
        except Exception:
            pass
        time.sleep(0.8)
        if not _is_tutorial_active(driver):
            return True
    # If home header is visible and no tutorial text markers, treat as closed.
    header = _get_home_header_text(driver)
    if header:
        _print(f"Tutorial close fallback by header visibility: homeHeader={header}")
        return True
    return not _is_tutorial_active(driver)


def _wait_wo_grid_modal_ready(driver, timeout_sec: int = 20) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            ok = driver.execute_script(
                """
                const modal = document.querySelector('div.modal.fade.in[style*="display: block"]')
                  || document.querySelector('div.modal.in');
                if (!modal) return false;
                const nav = modal.querySelector('nav.navbar.wo__header__nav');
                return !!nav;
                """
            )
            if ok:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _open_wo_grid_modal(driver) -> bool:
    _wait_page_idle(driver, timeout_sec=10)
    try:
        clicked = driver.execute_script(
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
              const r = el.getBoundingClientRect();
              if (r.width <= 0 || r.height <= 0) continue;
              if (r.top < 0 || r.left < 0) continue;
              try { el.click(); return true; } catch (e) {}
            }
            return false;
            """
        )
    except Exception:
        clicked = False
    if not clicked:
        return False
    return _wait_wo_grid_modal_ready(driver, timeout_sec=20)


def _get_home_header_text(driver) -> str:
    try:
        text = driver.execute_script(
            """
            const el = document.querySelector('#homeHeader');
            return el ? (el.textContent || '').trim() : '';
            """
        )
        return (text or "").strip()
    except Exception:
        return ""


def _switch_language_to_traditional_via_wo_modal(driver) -> bool:
    if not _open_wo_grid_modal(driver):
        _print("WO grid modal open failed.")
        return False
    time.sleep(0.8)
    try:
        opened = driver.execute_script(
            """
            const modal = document.querySelector('div.modal.fade.in[style*="display: block"]')
              || document.querySelector('div.modal.in');
            if (!modal) return false;
            const toggles = Array.from(modal.querySelectorAll('a.wo__header__nav__navbar__item__link.dropdown-toggle'));
            for (const t of toggles) {
              const path = t.querySelector('svg path');
              const d = (path && path.getAttribute('d')) || '';
              if (d.includes('M256 448q106 0 181 -75')) {
                t.click();
                return true;
              }
            }
            return false;
            """
        )
    except Exception:
        opened = False
    if not opened:
        _print("Language globe dropdown open failed.")
        return False
    time.sleep(0.6)
    try:
        selected = driver.execute_script(
            """
            const menus = Array.from(document.querySelectorAll('ul.dropdown-menu.wo__header__nav__navbar__item__menu'));
            for (const menu of menus) {
              const style = window.getComputedStyle(menu);
              if (!style || style.display === 'none' || style.visibility === 'hidden') continue;
              const options = Array.from(menu.querySelectorAll('a.wo__header__nav__navbar__item__menu__item__link'));
              for (const a of options) {
                const span = a.querySelector('span');
                const txt = ((span && span.textContent) || a.textContent || '').trim();
                if (txt.includes('繁體中文')) {
                  a.click();
                  return true;
                }
              }
            }
            return false;
            """
        )
    except Exception:
        selected = False
    if not selected:
        _print("Traditional Chinese option click failed.")
    return bool(selected)


def _click_first_result_single_pass(driver, wait, original_window: str) -> str:
    pre_handles = list(driver.window_handles)
    if original_window not in pre_handles:
        original_window = driver.current_window_handle

    headline = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "div.list-group .list-group-item h4 a"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", headline)
    try:
        headline.click()
    except Exception:
        driver.execute_script("arguments[0].click();", headline)

    deadline = time.time() + 10
    while time.time() < deadline:
        handles = list(driver.window_handles)
        if len(handles) > len(pre_handles):
            new_handles = [h for h in handles if h not in pre_handles]
            target = new_handles[-1]
            driver.switch_to.window(target)
            # If one click opened multiple tabs, keep only latest one.
            for h in new_handles[:-1]:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
            driver.switch_to.window(target)
            _print(f"First result opened in new tab(s): {len(new_handles)}; kept latest.")
            return "new_tab"
        try:
            if driver.find_elements(By.CSS_SELECTOR, "div.article-detail"):
                _print("First result opened in same tab.")
                return "same_tab"
        except Exception:
            pass
        time.sleep(0.4)

    raise TimeoutError("click_first_result_single_pass timeout: detail page/new tab not detected")


def _close_tutorial_with_recovery(driver, wait) -> bool:
    # First attempt
    closed = _close_tutorial_by_overlay_click(driver)
    if closed:
        try:
            _wait_heartbeat(
                stage_name="tutorial-closed-check",
                timeout_sec=20,
                check_fn=lambda: (
                    ("success", "tutorial_inactive")
                    if (not _is_tutorial_active(driver))
                    else ("pending", f"url={_safe_current_url(driver)}")
                ),
            )
            return True
        except Exception:
            pass

    # Recovery: refresh page, close again, and heartbeat-check.
    _print("Tutorial close recovery: refresh page -> click close again -> heartbeat check.")
    try:
        driver.refresh()
    except Exception:
        pass
    _return_to_home(driver, wait)
    _close_tutorial_by_overlay_click(driver)
    try:
        _wait_heartbeat(
            stage_name="tutorial-closed-check:retry",
            timeout_sec=25,
            check_fn=lambda: (
                ("success", "tutorial_inactive")
                if (not _is_tutorial_active(driver))
                else ("pending", f"url={_safe_current_url(driver)}")
            ),
        )
        return True
    except Exception:
        return False


def _wait_home_reached(driver, stage_name: str = "home-arrival-check", timeout_sec: int = 40):
    return _wait_heartbeat(
        stage_name=stage_name,
        timeout_sec=timeout_sec,
        check_fn=lambda: (
            ("success", "home_search_page_detected")
            if _is_home_search_page(driver)
            else ("pending", f"url={_safe_current_url(driver)}")
        ),
    )


def _force_home_refresh_then_check(driver, wait, reason: str = "") -> bool:
    if reason:
        _print(f"Force home refresh due to: {reason}")
    for url in HOME_CANDIDATE_URLS:
        try:
            driver.get(url)
            time.sleep(0.4)
            driver.refresh()
            _wait_home_reached(
                driver,
                stage_name=f"home-arrival-check:force-refresh:{url}",
                timeout_sec=30,
            )
            return True
        except Exception:
            continue
    return False


def _return_to_home(driver, wait) -> bool:
    try:
        go_back_to_search_form(driver=driver, wait=wait, st_module=None)
        _wait_home_reached(driver, stage_name="home-arrival-check:go_back", timeout_sec=25)
        return True
    except Exception:
        pass
    for url in HOME_CANDIDATE_URLS:
        try:
            driver.get(url)
            _wait_home_reached(
                driver,
                stage_name=f"home-arrival-check:url_fallback:{url}",
                timeout_sec=25,
            )
            return True
        except Exception:
            continue
    return False


def _heartbeat_with_home_retry(driver, wait, stage_name: str, check_fn, retry_action):
    try:
        return _wait_heartbeat(stage_name=stage_name, check_fn=check_fn)
    except TimeoutError:
        _print(f"[heartbeat] {stage_name}: timeout -> return home and retry once")
        if not _return_to_home(driver, wait):
            _force_home_refresh_then_check(driver, wait, reason=f"{stage_name}:timeout")
        retry_action()
        return _wait_heartbeat(stage_name=f"{stage_name}:retry", check_fn=check_fn)


def _attempt_logout_via_wo_grid_modal(driver) -> tuple[bool, str]:
    if not _open_wo_grid_modal(driver):
        return False, "wo_grid_button_or_modal_not_found"
    try:
        clicked = driver.execute_script(
            """
            const modal = document.querySelector('div.modal.fade.in[style*="display: block"]')
              || document.querySelector('div.modal.in');
            if (!modal) return false;
            const candidates = Array.from(modal.querySelectorAll('a.wo__header__nav__navbar__item__link'));
            for (const a of candidates) {
              const p = a.querySelector('svg path');
              const d = (p && p.getAttribute('d')) || '';
              if (d.includes('M397 269l-54 63')) { a.click(); return true; }
            }
            return false;
            """
        )
        if not clicked:
            return False, "wo_logout_button_not_found"
    except Exception:
        return False, "wo_logout_click_failed"
    return True, "ok"


def _attempt_logout_via_legacy_user_menu(driver) -> tuple[bool, str]:
    cfg = (HTML_STRUCTURE.get("home") or {}).get("user_menu") or {}
    toggle = _find_first_visible(driver, cfg.get("toggle_closed"), timeout=4) or _find_first_visible(
        driver, cfg.get("toggle_open"), timeout=3
    )
    if not toggle:
        return False, "legacy_toggle_not_found"
    try:
        driver.execute_script("arguments[0].click();", toggle)
    except Exception:
        try:
            toggle.click()
        except Exception:
            return False, "legacy_toggle_click_failed"

    logout_btn = _find_first_visible(driver, cfg.get("logout"), timeout=5)
    if not logout_btn:
        return False, "legacy_logout_link_not_found"
    try:
        driver.execute_script("arguments[0].click();", logout_btn)
    except Exception:
        try:
            logout_btn.click()
        except Exception:
            return False, "legacy_logout_click_failed"
    return True, "ok"


def _logout_and_confirm_once(driver, wait) -> bool:
    if not _return_to_home(driver, wait):
        _force_home_refresh_then_check(driver, wait, reason="final_logout")
    ok, reason = _attempt_logout_via_wo_grid_modal(driver)
    if not ok:
        _print(f"WO-grid logout unavailable: {reason}; fallback to legacy menu.")
        ok, reason = _attempt_logout_via_legacy_user_menu(driver)
        if not ok:
            _print(f"Legacy logout failed: {reason}")
    # Even if logout buttons are not found/clicked, still heartbeat-check login state.
    # Session can be invalidated asynchronously by server.
    try:
        _wait_heartbeat(
            stage_name="final-logout-check",
            timeout_sec=20,
            check_fn=lambda: (
                ("success", "login_page_detected")
                if _is_login_page(driver)
                else ("pending", f"url={_safe_current_url(driver)}")
            ),
        )
        return True
    except Exception as e:
        _print(f"Logout heartbeat confirm failed: {e}")
        return False


def _ensure_logout_or_raise(driver, wait, max_seconds: int = 300) -> bool:
    deadline = time.time() + max(60, max_seconds)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        _print(f"Final logout attempt #{attempt} ...")
        ok = _logout_and_confirm_once(driver, wait)
        if ok:
            return True

        _print("Final logout not confirmed; refresh home and retry.")
        if not _force_home_refresh_then_check(driver, wait, reason=f"final_logout_retry_{attempt}"):
            # Hard fallback to login URL, then continue loop.
            try:
                driver.get(WISERS_URL)
            except Exception:
                pass
        time.sleep(1.5)

    raise RuntimeError(f"Unable to confirm logout within {max_seconds} seconds.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Local run for 作者指定社評 workflow (author-specific editorial)."
    )
    parser.add_argument("--secrets-toml", default="")
    parser.add_argument("--group", default=os.getenv("WISERS_GROUP_NAME") or os.getenv("WISERS_GROUP") or "")
    parser.add_argument("--user", default=os.getenv("WISERS_USERNAME") or os.getenv("WISERS_USER") or "")
    parser.add_argument("--password", default=os.getenv("WISERS_PASSWORD") or "")
    parser.add_argument("--captcha-api-key", default=os.getenv("WISERS_2CAPTCHA_KEY") or os.getenv("WISERS_API_KEY") or "")
    parser.add_argument("--author", action="append", default=[], help="Can be used multiple times.")
    parser.set_defaults(headless=False)
    parser.add_argument("--headless", dest="headless", action="store_true")
    parser.add_argument("--headed", dest="headless", action="store_false")
    parser.add_argument("--stay-open", dest="stay_open", action="store_true", default=True)
    parser.add_argument("--no-stay-open", dest="stay_open", action="store_false")
    args = parser.parse_args(argv)

    secrets_candidates = []
    if args.secrets_toml:
        secrets_candidates.append(args.secrets_toml)
    else:
        secrets_candidates.extend(
            [
                os.path.join(REPO_ROOT, "secrets.local.toml"),
                os.path.join(REPO_ROOT, ".streamlit", "secrets.toml"),
            ]
        )
    for p in secrets_candidates:
        _maybe_apply_secrets_toml(p, args)

    authors = [a.strip() for a in (args.author or []) if a.strip()]
    if not authors:
        authors = ["李先知"]

    if not (args.group and args.user and args.password and args.captcha_api_key):
        _print("Missing credentials or captcha api key.")
        return 2

    driver = None
    wait = None
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
        except Exception:
            pass

        _wait_heartbeat(
            stage_name="pre-login-page-check",
            check_fn=lambda: (
                ("success", "login_page_detected")
                if _is_login_page(driver)
                else ("pending", f"url={_safe_current_url(driver)}")
            ),
        )

        _print("Performing login...")
        reset_to_login_page(driver, st_module=None)
        clear_login_fields(driver, wait=wait, st_module=None)
        perform_login(
            driver=driver,
            wait=wait,
            group_name=args.group,
            username=args.user,
            password=args.password,
            api_key=args.captcha_api_key,
            st_module=None,
        )

        _wait_heartbeat(
            stage_name="post-login-check",
            check_fn=lambda: (
                ("failure", f"login_error={err}")
                if (err := _extract_login_error_text(driver))
                else (
                    ("success", "home_detected")
                    if _is_home_search_page(driver)
                    else ("pending", f"url={_safe_current_url(driver)}")
                )
            ),
        )

        if not _return_to_home(driver, wait):
            if not _force_home_refresh_then_check(driver, wait, reason="post-login-home"):
                raise RuntimeError("Failed to reach home search page after login.")
        _print("Closing tutorial modal if present...")
        tutorial_ok = _close_tutorial_with_recovery(driver, wait)
        _print(f"Tutorial closed (with recovery): {tutorial_ok}")
        _print("Switch language via WO modal: grid -> globe -> 繁體中文 ...")
        lang_clicked = _switch_language_to_traditional_via_wo_modal(driver)
        _print(f"Language click action success={lang_clicked}")
        _heartbeat_with_home_retry(
            driver=driver,
            wait=wait,
            stage_name="traditional-chinese-check",
            check_fn=lambda: (
                ("success", "homeHeader=新聞搜索")
                if ("新聞搜索" in _get_home_header_text(driver))
                else (
                    ("pending", f"homeHeader={_get_home_header_text(driver) or '<empty>'}")
                )
            ),
            retry_action=lambda: _switch_language_to_traditional_via_wo_modal(driver),
        )

        original_window = driver.current_window_handle
        author_articles_data = {}
        for idx, author in enumerate(authors, start=1):
            _print(f"[{idx}/{len(authors)}] Search author: {author}")

            perform_author_search(driver=driver, wait=wait, author=author, st_module=None)

            _print("Waiting for results panel top progress to complete...")
            wait_for_results_panel_ready(driver=driver, wait=wait, st_module=None, timeout=20)

            _heartbeat_with_home_retry(
                driver=driver,
                wait=wait,
                stage_name=f"author-results-check:{author}",
                check_fn=lambda: (
                    ("success", "results_or_empty_ready")
                    if ensure_search_results_ready(driver=driver, wait=wait, st_module=None) in (True, False)
                    else ("pending", f"url={_safe_current_url(driver)}")
                ),
                retry_action=lambda: perform_author_search(
                    driver=driver, wait=wait, author=author, st_module=None
                ),
            )
            has_results = ensure_search_results_ready(driver=driver, wait=wait, st_module=None)
            if not has_results:
                _print(f"{author}: no results.")
                author_articles_data[author] = {"title": "無法找到文章", "content": ""}
                if not _return_to_home(driver, wait):
                    if not _force_home_refresh_then_check(driver, wait, reason=f"{author}:no_results"):
                        raise RuntimeError(f"Failed to return home after no-results for author={author}.")
                continue

            scraped = None
            for open_try in (1, 2):
                try:
                    _click_first_result_single_pass(
                        driver=driver,
                        wait=wait,
                        original_window=original_window,
                    )
                    _wait_heartbeat(
                        stage_name=f"article-detail-check:{author}:try{open_try}",
                        timeout_sec=25,
                        check_fn=lambda: (
                            ("success", "article_detail_detected")
                            if bool(driver.find_elements(By.CSS_SELECTOR, "div.article-detail"))
                            else ("pending", f"url={_safe_current_url(driver)}")
                        ),
                    )
                    scraped = scrape_author_article_content(
                        driver=driver,
                        wait=wait,
                        author_name=author,
                        st_module=None,
                    )
                    break
                except Exception as e:
                    if open_try == 1:
                        _print(
                            f"{author}: open/scrape try1 failed ({e}); return home and re-run author search once."
                        )
                        if not _return_to_home(driver, wait):
                            _force_home_refresh_then_check(driver, wait, reason=f"{author}:open_scrape_retry")
                        perform_author_search(driver=driver, wait=wait, author=author, st_module=None)
                        wait_for_results_panel_ready(driver=driver, wait=wait, st_module=None, timeout=20)
                        _heartbeat_with_home_retry(
                            driver=driver,
                            wait=wait,
                            stage_name=f"author-results-check-retry:{author}",
                            check_fn=lambda: (
                                ("success", "results_or_empty_ready")
                                if ensure_search_results_ready(driver=driver, wait=wait, st_module=None) in (True, False)
                                else ("pending", f"url={_safe_current_url(driver)}")
                            ),
                            retry_action=lambda: perform_author_search(
                                driver=driver, wait=wait, author=author, st_module=None
                            ),
                        )
                        has_results_retry = ensure_search_results_ready(driver=driver, wait=wait, st_module=None)
                        if not has_results_retry:
                            _print(f"{author}: no results after retry.")
                            author_articles_data[author] = {"title": "無法找到文章", "content": ""}
                            scraped = None
                            break
                        continue
                    raise

            if not scraped:
                if author not in author_articles_data:
                    raise RuntimeError(f"{author}: failed to open/scrape article after retry.")
                if not _return_to_home(driver, wait):
                    if not _force_home_refresh_then_check(driver, wait, reason=f"{author}:post_no_result"):
                        raise RuntimeError(f"Failed to return home after fallback no-result for author={author}.")
                continue
            author_articles_data[author] = scraped
            _print(f"{author}: scraped title={scraped.get('title', '')[:80]}")

            if driver.current_window_handle != original_window:
                driver.close()
                driver.switch_to.window(original_window)
            if not _return_to_home(driver, wait):
                if not _force_home_refresh_then_check(driver, wait, reason=f"{author}:after_scrape"):
                    raise RuntimeError(f"Failed to return home after scraping author={author}.")

        _print("Workflow run finished.")
        for k, v in author_articles_data.items():
            _print(f"- {k}: {v.get('title', 'N/A')}")
        return 0

    except Exception as e:
        _print(f"Workflow failed: {e}")
        return 1
    finally:
        if driver:
            try:
                _print("Finalizing: return home -> logout -> confirm...")
                logout_ok = _ensure_logout_or_raise(driver, wait, max_seconds=300)
                _print(f"Final logout confirmed={logout_ok}")
            except Exception as e:
                _print(f"Final logout phase error: {e}")

            if args.stay_open:
                _print("Browser stays open. Press Ctrl+C to stop this script.")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    _print("Received Ctrl+C; exiting script without quitting browser.")


if __name__ == "__main__":
    raise SystemExit(main())
