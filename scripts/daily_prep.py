import argparse
import json
import os
import sys
from datetime import datetime

import pytz

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.config import LOCATION_ORDER
from utils.firebase_logging import create_cli_logger
from utils.international_news_stage1 import run_international_news_stage1


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
    """
    Read a Streamlit-like secrets.toml and populate:
      - Wisers args (group/user/password/2captcha key)
      - Firebase env vars for CLI logger
      - Optional AI env vars
    """
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
        # In Streamlit secrets this is called api_key (2captcha)
        args.captcha_api_key = wisers.get("api_key", "") or ""

    # AI (optional): allow you to keep KIMI keys out of code.
    ai = sec.get("ai", {}) or {}
    if ai.get("kmi_api_key") and not os.getenv("KIMI_API_KEY"):
        os.environ["KIMI_API_KEY"] = str(ai.get("kmi_api_key"))
    if ai.get("kmi_base_url") and not os.getenv("KIMI_BASE_URL"):
        os.environ["KIMI_BASE_URL"] = str(ai.get("kmi_base_url"))

    # Firebase -> environment variables expected by FirebaseLoggerCLI
    fb = sec.get("firebase", {}) or {}
    sa = (fb.get("service_account") or {}) if isinstance(fb, dict) else {}

    if fb.get("database_url") and not os.getenv("FIREBASE_DATABASE_URL"):
        os.environ["FIREBASE_DATABASE_URL"] = str(fb.get("database_url"))
    if fb.get("storage_bucket") and not os.getenv("FIREBASE_STORAGE_BUCKET"):
        os.environ["FIREBASE_STORAGE_BUCKET"] = str(fb.get("storage_bucket"))

    if sa and not os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"):
        # NOTE: Keep it in-memory/env to avoid writing private keys to disk.
        os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"] = json.dumps(sa, ensure_ascii=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="International News daily prep (Stage 1): login → search → hover scrape → AI → write preview_articles.json"
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

    parser.add_argument("--max-articles", type=int, default=int(os.getenv("INTL_MAX_ARTICLES", "30")))
    parser.add_argument("--min-words", type=int, default=int(os.getenv("INTL_MIN_WORDS", "200")))
    parser.add_argument("--max-words", type=int, default=int(os.getenv("INTL_MAX_WORDS", "1000")))
    parser.add_argument("--headed", action="store_true", help="Run Chrome with UI (not headless).")

    parser.add_argument("--no-firebase", action="store_true", help="Do not upload to Firebase; only write local JSON.")
    parser.add_argument("--local-out", default=os.getenv("INTL_LOCAL_OUT") or "", help="Local output directory for JSON (optional).")
    parser.add_argument("--export-logs", action="store_true", help="Export logger events to local JSON file.")

    args = parser.parse_args(argv)

    # Auto-load secrets (optional)
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
        _print("缺少 Wisers 登录参数。请通过参数或环境变量提供：")
        _print("  --group / WISERS_GROUP_NAME")
        _print("  --user / WISERS_USERNAME")
        _print("  --password / WISERS_PASSWORD")
        _print("  --captcha-api-key / WISERS_2CAPTCHA_KEY")
        return 2

    logger = None
    if not args.no_firebase:
        try:
            logger = create_cli_logger(run_context="international_news_daily_prep_stage1")
        except Exception as e:
            _print(f"Firebase logger 初始化失败：{e}")
            _print("你可以先用 --no-firebase 跑通流程，或检查 FIREBASE_* 环境变量。")
            return 3

    def progress(stage: str, payload: dict):
        if stage == "ai":
            cur = payload.get("current")
            total = payload.get("total")
            title = payload.get("title", "")
            _print(f"AI 分析中 {cur}/{total}: {title[:60]}")
        else:
            msg = payload.get("message", "")
            _print(msg)

    try:
        res = run_international_news_stage1(
            group_name=args.group,
            username=args.user,
            password=args.password,
            api_key=args.captcha_api_key,
            max_articles=args.max_articles,
            min_words=args.min_words,
            max_words=args.max_words,
            headless=not args.headed,
            logger=logger,
            progress=progress,
        )
    except Exception as e:
        if logger:
            logger.error("Stage1 failed", error=str(e))
            logger.end_run(status="error", summary={"error": str(e)})
        _print(f"Stage 1 失败：{e}")
        return 1

    analyzed_list = res.analyzed_list

    # Optional: local output
    if args.local_out:
        os.makedirs(args.local_out, exist_ok=True)
        fp = os.path.join(args.local_out, "preview_articles.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(analyzed_list, f, ensure_ascii=False, indent=2)
        _print(f"已写本地：{fp}")

        # Helpful: also write an empty user_final_list.json scaffold so UI can jump straight to sorting if you want
        user_list_fp = os.path.join(args.local_out, "user_final_list.json")
        with open(user_list_fp, "w", encoding="utf-8") as f:
            json.dump({loc: [] for loc in LOCATION_ORDER}, f, ensure_ascii=False, indent=2)
        _print(f"已写本地：{user_list_fp}")

    # Firebase upload
    if logger:
        gs1 = logger.save_json_to_date_folder(analyzed_list, "preview_articles.json")
        _print(f"已上传：{gs1}")

        # Optional: pre-create empty user_final_list.json so Streamlit 打开后可直接进入排序（无需再点一次“初始化”按钮）
        gs2 = logger.save_json_to_date_folder({loc: [] for loc in LOCATION_ORDER}, "user_final_list.json")
        _print(f"已上传：{gs2}")

        if args.export_logs:
            log_fp = logger.export_log_json(log_dir=os.path.join(".", "logs"))
            _print(f"已导出本地日志：{log_fp}")

        logger.end_run(status="completed", summary={"preview_count": len(analyzed_list)})

    _print(f"完成：preview_articles.json = {len(analyzed_list)} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

