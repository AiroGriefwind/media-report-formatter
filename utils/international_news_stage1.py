import re
import time
from dataclasses import dataclass
from typing import Callable, Optional, Dict, List

from selenium.webdriver.support.ui import WebDriverWait

from utils.ai_screening_utils import run_ai_screening
from utils.config import LOCATION_ORDER
from utils.international_news_utils import extract_news_id_from_html, parse_metadata, run_international_news_task
from utils.web_scraping_utils import scrape_hover_popovers
from utils.wisers_utils import (
    perform_login,
    robust_logout_request,
    setup_webdriver,
    switch_language_to_traditional_chinese,
)


@dataclass
class Stage1Result:
    analyzed_list: List[Dict]
    grouped_pool: Dict[str, List[Dict]]


def _extract_raw_meta_from_hover_text(title: str, hover_text: str) -> str:
    """
    Mirror the Streamlit tab logic:
    - If hover_text contains lines, and first line equals title, use second line as metadata.
    - Else use first line as metadata.
    """
    if not hover_text:
        return ""
    if "\n" not in hover_text:
        return ""
    lines = hover_text.split("\n", 2)
    if len(lines) <= 1:
        return ""
    if lines[0].strip() == (title or "").strip():
        return lines[1].strip()
    return lines[0].strip()


def run_international_news_stage1(
    *,
    group_name: str,
    username: str,
    password: str,
    api_key: str,
    max_articles: int = 30,
    min_words: int = 200,
    max_words: int = 1000,
    headless: bool = True,
    logger=None,
    progress: Optional[Callable[[str, dict], None]] = None,
    ai_sleep_seconds: float = 1.0,
) -> Stage1Result:
    """
    Stage 1 (headless/CLI friendly):
      登录 → 搜索 → 悬浮抓取 → AI分析 → 写回 preview_articles.json（由调用方决定是否写回）

    - `logger`: any object with .info/.warn/.error optional; can be Streamlit FirebaseLogger or CLI logger
    - `progress(stage, payload)`: optional callback for progress updates
    """

    def _log(level: str, msg: str, **extra):
        if progress:
            try:
                progress(level, {"message": msg, **extra})
            except Exception:
                pass
        if logger:
            fn = getattr(logger, level, None)
            if callable(fn):
                fn(msg, **extra)

    driver = None
    try:
        _log("info", "Stage1: setup webdriver", headless=headless)
        driver = setup_webdriver(headless=headless, st_module=None, logger=logger)
        if not driver:
            raise RuntimeError("setup_webdriver returned None")

        wait = WebDriverWait(driver, 20)

        _log("info", "Stage1: login")
        perform_login(
            driver=driver,
            wait=wait,
            group_name=group_name,
            username=username,
            password=password,
            api_key=api_key,
            st_module=None,
            logger=logger,
        )
        _log("info", "Stage1: switch language to 繁體中文")
        switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=None, logger=logger)

        _log("info", "Stage1: run saved search 國際新聞", max_articles=max_articles)
        run_international_news_task(driver=driver, wait=wait, st_module=None, max_articles=max_articles, logger=logger)

        _log("info", "Stage1: scrape hover popovers", max_articles=max_articles)
        rawlist = scrape_hover_popovers(
            driver=driver,
            wait=wait,
            st_module=None,
            max_articles=max_articles,
            logger=logger,
        ) or []

        _log("info", "Stage1: hover popovers captured", count=len(rawlist))

        # Logout early to release session
        try:
            _log("info", "Stage1: robust logout request (best effort)")
            robust_logout_request(driver, None)
        except Exception as e:
            _log("warn", "Stage1: robust logout failed (ignored)", error=str(e))

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    # Filter by word count in hover_text (keep articles without explicit word count)
    filtered_rawlist: List[Dict] = []
    for item in rawlist:
        hover_text = item.get("hover_text", "") or ""
        word_matches = re.findall(r"(\d+)\s*字", hover_text)
        if word_matches:
            word_count = int(word_matches[0])
            if min_words <= word_count <= max_words:
                filtered_rawlist.append(item)
        else:
            filtered_rawlist.append(item)

    rawlist = filtered_rawlist
    _log("info", "Stage1: word-count filtered", count=len(rawlist), min_words=min_words, max_words=max_words)

    # Attach original_index for later stages
    filtered_list: List[Dict] = []
    for i, item in enumerate(rawlist):
        item = dict(item)
        item["original_index"] = i
        filtered_list.append(item)

    # AI analysis
    _log("info", "Stage1: AI screening start", count=len(filtered_list))

    def _ai_progress(i: int, total: int, title: str):
        if progress:
            progress("ai", {"current": i + 1, "total": total, "title": title})

    analyzed_list = run_ai_screening(filtered_list, progress_callback=_ai_progress)
    if ai_sleep_seconds and ai_sleep_seconds > 0:
        # run_ai_screening already sleeps; keep a tiny buffer to avoid bursty calls if modified later
        time.sleep(0.01)

    # Post-process: news_id + formatted_metadata
    for item in analyzed_list:
        hover_html = item.get("hover_html", "") or ""
        item["news_id"] = extract_news_id_from_html(hover_html)

        title = item.get("title", "") or ""
        hover_text = item.get("hover_text", "") or ""
        raw_meta = _extract_raw_meta_from_hover_text(title=title, hover_text=hover_text)
        item["formatted_metadata"] = parse_metadata(raw_meta)

    # Group pool by Location / Tech News
    grouped_data: Dict[str, List[Dict]] = {loc: [] for loc in LOCATION_ORDER}
    for item in analyzed_list:
        loc = (item.get("ai_analysis") or {}).get("main_location", "Others")
        if (item.get("ai_analysis") or {}).get("is_tech_news", False):
            loc = "Tech News"
        if loc not in grouped_data:
            loc = "Others"
        grouped_data[loc].append(item)

    _log("info", "Stage1: AI screening done", count=len(analyzed_list))
    return Stage1Result(analyzed_list=analyzed_list, grouped_pool=grouped_data)

