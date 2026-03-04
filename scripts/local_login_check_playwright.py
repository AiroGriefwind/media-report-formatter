import argparse
import base64
import json
import os
import sys
import tempfile
import time
import urllib.parse
from datetime import datetime

import pytz
import requests
from twocaptcha import TwoCaptcha

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from utils.config import WISERS_URL
from utils.html_structure_config import HTML_STRUCTURE

HKT = pytz.timezone("Asia/Hong_Kong")


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


def _coerce_playwright_selector(selector_def):
    by = (selector_def or {}).get("by")
    value = (selector_def or {}).get("value")
    if not by or not value:
        return None
    if by == "css":
        return value
    if by == "xpath":
        return f"xpath={value}"
    if by == "id":
        return f"#{value}"
    if by == "name":
        return f"[name='{value}']"
    return None


def _first_visible_selector(page, selectors, timeout_ms=3000):
    end_time = time.time() + max(0.1, timeout_ms / 1000.0)
    while time.time() < end_time:
        for sel in selectors or []:
            pw_sel = _coerce_playwright_selector(sel)
            if not pw_sel:
                continue
            try:
                loc = page.locator(pw_sel).first
                if loc.is_visible(timeout=200):
                    return loc
            except Exception:
                continue
        time.sleep(0.1)
    return None


def _selector_strings(selectors):
    out = []
    for sel in selectors or []:
        pw_sel = _coerce_playwright_selector(sel)
        if pw_sel:
            out.append(pw_sel)
    return out


def _write_html_snapshot(page, output_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(page.content() or "")
    except Exception as e:
        _print(f"Failed to write HTML snapshot: {e}")


def _write_screenshot(page, output_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        page.screenshot(path=output_path, full_page=True)
    except Exception as e:
        _print(f"Failed to write screenshot: {e}")


def _write_diagnostics(page, stage: str, reason: str, args) -> None:
    stamp = datetime.now(HKT).strftime("%Y%m%d_%H%M%S")
    diag_dir = os.path.join(REPO_ROOT, "artifacts", "diagnostics")
    os.makedirs(diag_dir, exist_ok=True)
    slug = f"{stamp}_{stage}"
    html_path = os.path.join(diag_dir, f"{slug}.html")
    png_path = os.path.join(diag_dir, f"{slug}.png")
    txt_path = os.path.join(diag_dir, f"{slug}.txt")

    _write_html_snapshot(page, html_path)
    _write_screenshot(page, png_path)

    try:
        url = page.url
    except Exception:
        url = ""

    try:
        # Small, deterministic debug snippet.
        debug_text = page.evaluate(
            """
            () => {
              const body = document.body ? (document.body.innerText || "") : "";
              return body.slice(0, 1200);
            }
            """
        )
    except Exception:
        debug_text = ""

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"stage={stage}\n")
        f.write(f"reason={reason}\n")
        f.write(f"url={url}\n")
        f.write(f"group={bool(args.group)} user={bool(args.user)}\n")
        f.write("--- body_snippet ---\n")
        f.write(debug_text or "")
        f.write("\n")

    _print(f"Diagnostics saved: {txt_path}")


def _wait_login_page_ready(page, timeout_ms=15000) -> bool:
    login_cfg = HTML_STRUCTURE.get("login") or {}
    inputs = login_cfg.get("inputs") or {}
    required_selectors = []
    required_selectors.extend(_selector_strings(inputs.get("group")))
    required_selectors.extend(_selector_strings(inputs.get("user")))
    required_selectors.extend(_selector_strings(inputs.get("password")))
    required_selectors.extend(_selector_strings(login_cfg.get("submit")))
    if not required_selectors:
        required_selectors = [
            "input[data-qa-ci='groupid']",
            "input[data-qa-ci='userid']",
            "input[data-qa-ci='password']",
            "input[data-qa-ci='button-login']",
        ]

    end_time = time.time() + max(1, timeout_ms / 1000)
    while time.time() < end_time:
        if any(page.locator(sel).count() > 0 for sel in required_selectors):
            return True
        time.sleep(0.2)
    return False


def _set_input(page, selectors, value: str, timeout_ms=5000):
    loc = _first_visible_selector(page, selectors, timeout_ms=timeout_ms)
    if not loc:
        raise RuntimeError(f"Input not found for selectors: {selectors}")
    loc.click(timeout=timeout_ms)
    loc.fill("")
    loc.fill(value)


def _solve_captcha(page, api_key: str, timeout_ms=20000) -> str:
    login_cfg = HTML_STRUCTURE.get("login") or {}
    inputs = login_cfg.get("inputs") or {}
    img_loc = _first_visible_selector(page, inputs.get("captcha_image"), timeout_ms=timeout_ms)
    if not img_loc:
        # fallback for backward compatibility
        img_loc = page.locator("img.CaptchaField__CaptchaImage-hffgxm-5").first
        img_loc.wait_for(state="visible", timeout=timeout_ms)
    captcha_src = img_loc.get_attribute("src") or ""
    if "base64," not in captcha_src:
        raise RuntimeError("Captcha image src is not base64 data URL.")

    img_data = base64.b64decode(captcha_src.split("base64,", 1)[1])
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as fp:
        fp.write(img_data)
        captcha_path = fp.name

    try:
        solver = TwoCaptcha(api_key)
        solved = solver.normal(captcha_path)
        text = (solved or {}).get("code", "")
        if not text:
            raise RuntimeError(f"2Captcha returned empty code: {solved}")
        return text
    finally:
        try:
            os.remove(captcha_path)
        except Exception:
            pass


def _is_home_page(page) -> bool:
    candidates = [
        "button#toggle-query-execute.btn.btn-primary",
        "div.app-query-input",
        "#accordion-queryfilter",
    ]
    for sel in candidates:
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return False


def _has_login_error_banner(page) -> str:
    login_cfg = HTML_STRUCTURE.get("login") or {}
    error_selectors = _selector_strings(login_cfg.get("error"))
    if not error_selectors:
        error_selectors = [
            "div.NewContent__StyledNewErrorCode-q19ga1-5",
            "div[class*='ErrorCode']",
            "div[class*='error']",
            "p[class*='error']",
        ]
    for sel in error_selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=200):
                txt = (loc.inner_text(timeout=500) or "").strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def _wait_for_home_or_error(page, timeout_ms=15000):
    end_time = time.time() + max(1, timeout_ms / 1000)
    while time.time() < end_time:
        if _is_home_page(page):
            return True, ""
        err = _has_login_error_banner(page)
        if err:
            return False, err
        time.sleep(0.2)
    return False, "Login verification timed out."


def _close_tutorial_modal(page, timeout_ms=5000) -> bool:
    # Follow the old Selenium behavior: click "#app-userstarterguide-0 button.close".
    try:
        close_btn = page.locator("#app-userstarterguide-0 button.close").first
        if not close_btn.is_visible(timeout=timeout_ms):
            _print("Tutorial modal not found or already closed.")
            return False
        close_btn.click(timeout=2000)
        page.locator("#app-userstarterguide-0").first.wait_for(state="hidden", timeout=5000)
        _print("Tutorial modal closed.")
        return True
    except Exception:
        # Best effort only; do not fail login flow.
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        _print("Tutorial modal not found or already closed.")
        return False


def _is_timeout_page(page) -> bool:
    timeout_cfg = HTML_STRUCTURE.get("timeout") or {}
    timeout_url = (timeout_cfg.get("url") or "").strip()
    try:
        url = page.url or ""
    except Exception:
        url = ""
    if timeout_url and timeout_url in url:
        return True
    if "/wevo/timeout" in url:
        return True
    title_loc = _first_visible_selector(page, timeout_cfg.get("title"), timeout_ms=1500)
    if title_loc:
        try:
            txt = (title_loc.inner_text(timeout=500) or "").strip()
            if "timeout" in txt.lower() or "登出" in txt or "超时" in txt or "逾時" in txt:
                return True
        except Exception:
            return True
    return False


def _send_robust_logout_request(page, context, group_name: str, username: str) -> bool:
    try:
        cookies = context.cookies()
    except Exception as e:
        _print(f"Failed to read cookies for robust logout: {e}")
        return False

    session_cookies = {}
    for ck in cookies or []:
        name = ck.get("name")
        value = ck.get("value")
        if name and isinstance(value, str):
            session_cookies[name] = value

    if not session_cookies:
        _print("No cookies found for robust logout request.")
        return False

    criteria = {
        "groupId": group_name or "",
        "userId": username or "",
        "deviceType": "web",
        "deviceId": "",
    }
    criteria_encoded = urllib.parse.quote(
        json.dumps(criteria, ensure_ascii=False, separators=(",", ":")),
        safe="",
    )
    current_timestamp = int(time.time() * 1000)
    robust_logout_url = (
        "https://wisesearch6.wisers.net/wevo/api/AccountService;"
        f"criteria={criteria_encoded};"
        f"path=logout;timestamp={current_timestamp};updateSession=true"
        "?returnMeta=true"
    )
    headers = {
        "accept": "*/*",
        "x-requested-with": "XMLHttpRequest",
        "referer": page.url or WISERS_URL,
    }

    try:
        resp = requests.get(robust_logout_url, headers=headers, cookies=session_cookies, timeout=12)
        _print(f"Robust logout request status: {resp.status_code}")
        return bool(resp.ok)
    except Exception as e:
        _print(f"Robust logout request failed: {e}")
        return False


def _refresh_and_check_logged_out(page) -> bool:
    try:
        page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    if _wait_login_page_ready(page, timeout_ms=12000):
        _print("Refresh check: login page detected.")
        return True
    if _is_timeout_page(page):
        _print("Refresh check: timeout page detected.")
        return True
    return False


def _state_with_retry(name: str, func, retries: int, backoff_seconds: float, page, args):
    last_error = None
    for attempt in range(1, retries + 1):
        _print(f"[state={name}] attempt {attempt}/{retries}")
        try:
            return func()
        except Exception as e:
            last_error = e
            _print(f"[state={name}] failed: {e}")
            _write_diagnostics(page, name, repr(e), args)
            if attempt < retries:
                sleep_s = backoff_seconds * (2 ** (attempt - 1))
                _print(f"[state={name}] retrying in {sleep_s:.1f}s...")
                time.sleep(sleep_s)
    raise RuntimeError(f"state '{name}' failed after {retries} attempts: {last_error}")


def _attempt_logout_in_context(page) -> bool:
    cfg = (HTML_STRUCTURE.get("home") or {}).get("user_menu") or {}
    toggle = _first_visible_selector(page, cfg.get("toggle_closed"), timeout_ms=4000)
    if not toggle:
        toggle = _first_visible_selector(page, cfg.get("toggle_open"), timeout_ms=2000)
    if toggle:
        try:
            toggle.click(timeout=2000)
        except Exception:
            return False
    else:
        try:
            clicked = page.evaluate(
                """
                () => {
                  const icon = document.querySelector('i.wf.wf-user');
                  if (!icon) return false;
                  const btn = icon.closest('a.dropdown-toggle') || icon.closest('a');
                  if (!btn) return false;
                  btn.click();
                  return true;
                }
                """
            )
            if not clicked:
                return False
        except Exception:
            return False

    logout_btn = _first_visible_selector(page, cfg.get("logout"), timeout_ms=5000)
    if logout_btn:
        try:
            logout_btn.click(timeout=2000)
        except Exception:
            return False
    else:
        try:
            clicked = page.evaluate(
                """
                () => {
                  const items = Array.from(document.querySelectorAll('a'));
                  const target = items.find(a => {
                    const txt = (a.textContent || '').trim();
                    return txt.includes('退出登錄') || txt.includes('退出登录');
                  });
                  if (!target) return false;
                  target.click();
                  return true;
                }
                """
            )
            if not clicked:
                return False
        except Exception:
            return False

    try:
        page.wait_for_selector("input[data-qa-ci='groupid']", timeout=10000)
        return True
    except Exception:
        return False


def _logout_with_fallback(page, context, args) -> bool:
    _print("Logging out via user menu...")
    if _attempt_logout_in_context(page):
        if _refresh_and_check_logged_out(page):
            _print("Logged out successfully.")
            return True
        _print("User-menu logout clicked, but post-refresh state is not login/timeout.")

    _print("Logout via user menu failed, sending robust logout request as fallback...")
    _send_robust_logout_request(
        page=page,
        context=context,
        group_name=args.group,
        username=args.user,
    )
    if _refresh_and_check_logged_out(page):
        _print("Fallback robust logout succeeded.")
        return True

    _print("Robust logout fallback did not settle session, applying local cleanup...")
    try:
        context.clear_cookies()
    except Exception:
        pass
    try:
        page.evaluate(
            """
            () => {
              try { localStorage.clear(); } catch (e) {}
              try { sessionStorage.clear(); } catch (e) {}
            }
            """
        )
    except Exception:
        pass

    try:
        page.goto(WISERS_URL, wait_until="domcontentloaded", timeout=30000)
        if _wait_login_page_ready(page, timeout_ms=12000) or _is_timeout_page(page):
            _print("Fallback reset successful (login/timeout page visible).")
            return True
    except Exception:
        pass

    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Local Wisers login check with Playwright: verify home HTML after login."
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
    parser.add_argument("--headed", action="store_true", help="Run Chromium with UI (not headless).")
    parser.add_argument("--timeout-seconds", type=int, default=20, help="Per-state timeout.")
    parser.add_argument("--retries", type=int, default=2, help="Retries per state.")
    parser.add_argument("--backoff-seconds", type=float, default=1.5, help="Retry backoff base seconds.")
    parser.add_argument(
        "--allow-logout-failure",
        action="store_true",
        help="If set, logout failure still returns exit code 0.",
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
        secrets_candidates.extend(
            [
                os.path.join(REPO_ROOT, "secrets.local.toml"),
                os.path.join(REPO_ROOT, ".streamlit", "secrets.toml"),
            ]
        )
    for p in secrets_candidates:
        _maybe_apply_secrets_toml(p, args)

    if not (args.group and args.user and args.password and args.captcha_api_key):
        _print("Missing Wisers login parameters. Provide via args or env:")
        _print("  --group / WISERS_GROUP_NAME")
        _print("  --user / WISERS_USERNAME")
        _print("  --password / WISERS_PASSWORD")
        _print("  --captcha-api-key / WISERS_2CAPTCHA_KEY")
        return 2

    page = None
    context = None
    browser = None
    playwright_ctx = None
    try:
        _print("Setting up Playwright Chromium...")
        playwright_ctx = sync_playwright().start()
        browser = playwright_ctx.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1200, "height": 800})
        page = context.new_page()

        def _open_login():
            _print(f"Opening login URL: {WISERS_URL}")
            page.goto(WISERS_URL, wait_until="domcontentloaded", timeout=30000)
            if not _wait_login_page_ready(page, timeout_ms=args.timeout_seconds * 1000):
                raise RuntimeError("Login page did not become ready in time.")
            return True

        def _fill_form():
            _print("Filling login form...")
            login_cfg = HTML_STRUCTURE.get("login") or {}
            inputs = login_cfg.get("inputs") or {}
            _set_input(page, inputs.get("group") or [{"by": "css", "value": "input[data-qa-ci='groupid']"}], args.group)
            _set_input(page, inputs.get("user") or [{"by": "css", "value": "input[data-qa-ci='userid']"}], args.user)
            _set_input(page, inputs.get("password") or [{"by": "css", "value": "input[data-qa-ci='password']"}], args.password)
            return True

        def _solve_and_fill_captcha():
            _print("Solving captcha...")
            code = _solve_captcha(page, args.captcha_api_key, timeout_ms=args.timeout_seconds * 1000)
            login_cfg = HTML_STRUCTURE.get("login") or {}
            inputs = login_cfg.get("inputs") or {}
            _set_input(page, inputs.get("captcha") or [{"by": "css", "value": "input.CaptchaField__Input-hffgxm-4"}], code)
            return True

        def _submit():
            _print("Submitting login form...")
            login_cfg = HTML_STRUCTURE.get("login") or {}
            btn = _first_visible_selector(page, login_cfg.get("submit"), timeout_ms=5000)
            if not btn:
                raise RuntimeError("Login submit button not found.")
            btn.click(timeout=5000)
            return True

        def _verify_home():
            _print("Verifying post-login home page...")
            ok, err = _wait_for_home_or_error(page, timeout_ms=args.timeout_seconds * 1000)
            if not ok:
                raise RuntimeError(f"Login failed: {err}")
            if not _is_home_page(page):
                raise RuntimeError("Home page signals missing after login.")
            return True

        _state_with_retry("open_login", _open_login, args.retries, args.backoff_seconds, page, args)
        _state_with_retry("fill_form", _fill_form, args.retries, args.backoff_seconds, page, args)
        _state_with_retry("solve_captcha", _solve_and_fill_captcha, args.retries, args.backoff_seconds, page, args)
        _state_with_retry("submit", _submit, args.retries, args.backoff_seconds, page, args)
        _state_with_retry("verify_home", _verify_home, args.retries, args.backoff_seconds, page, args)
        _close_tutorial_modal(page, timeout_ms=6000)

        _print("Home page detected. Writing HTML snapshot...")
        _write_html_snapshot(page, args.html_out)
        _print(f"HTML snapshot saved: {args.html_out}")
        _print("Writing homepage screenshot...")
        _write_screenshot(page, args.screenshot_out)
        _print(f"Homepage screenshot saved: {args.screenshot_out}")

        logout_ok = _logout_with_fallback(page, context, args)
        if logout_ok:
            return 0
        _print("Logout failed after fallback.")
        return 0 if args.allow_logout_failure else 4
    except Exception as e:
        _print(f"Login check failed: {e}")
        try:
            if page:
                _write_html_snapshot(page, args.html_out)
                _print(f"HTML snapshot saved: {args.html_out}")
                _write_diagnostics(page, "fatal", repr(e), args)
        except Exception:
            pass
        return 1
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if playwright_ctx:
                playwright_ctx.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
