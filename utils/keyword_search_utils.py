import re
import os
import time
import streamlit as st

from utils.wisers_utils import (
    set_date_range_period,
    is_hkt_monday,
    wait_for_search_results,
    ensure_results_list_visible,
    wait_for_results_panel_ready,
    search_title_from_home,
    search_title_via_edit_search_modal,
    set_media_filters_in_panel,
    set_keyword_scope_checkboxes,
    inject_cjk_font_css,
    scroll_to_load_all_content,
    wait_for_ajax_complete,
)
from utils.web_scraping_utils import scrape_hover_popovers
from utils import international_news_utils as intl_utils
from utils.firebase_logging import get_logger

parse_metadata = intl_utils.parse_metadata
extract_news_id_from_html = intl_utils.extract_news_id_from_html

HK_KEYWORD_DEFAULT = (
    "李家超/局長/全港/司長/財政司/律政司/政務司/行政會議/公務員/申訴專員公署/廉政公署/"
    "審計署/文化體育及旅遊局/教育局/環境及生態局/醫務衛生局/旅發局/康樂及文化事務署/"
    "漁護署/食環署/衛生署/民政及青年事務局/勞工及福利局/勞工處/社會福利署/保安局/海關/"
    "警務處/入境事務/金管局/商務及經濟發展局/投資推廣署/發展局/地政總署/房屋局/"
    "創新科技及工業局/稅務局/數字辦/家族辦公室/創新科技署/運輸及物流局/運輸署/民航/"
    "路政署/海事處/機管局/金管局/條例草案/三讀/政府法案/國安/全運會/穩定幣/領展/"
    "電動車/的士/書展/聯招/新田科技城"
)

INTERNATIONAL_KEYWORD_DEFAULT = (
    "國際/特朗普/外交部/中美/歐美/中東/俄烏/中歐/北約/中俄/印巴/以色列/巴以/聯合國/"
    "美聯儲/一帶一路/東盟/日本/韓國/東南亞/美國/歐盟/俄羅斯/新加坡/石油/戰爭/峰會/"
    "國防部/伊朗/北約/柬埔寨/泰國/軍方/關稅/貿易戰/訪問/英國/法國/安全部/最高法院/"
    "印度/五眼聯盟/金磚國家/IMF"
)

GREATER_CHINA_KEYWORD_DEFAULT = (
    "習近平/李強/王毅/訪華/外交部/國台辦/港澳辦/中聯辦/抗戰/一帶一路/亞投行/中央/"
    "人民銀行/國務院/中科院/中方/外交部/國防部/兩岸/丁薛祥/南海/中紀委/省委/反腐/"
    "貪污/芯片/新能源/神舟/金磚/中證監/巴拿馬運河/經濟政策"
)

MEDIA_FILTER_CONTAINER_SELECTOR = (
    "#accordion-queryfilter > div.panel.panel-default.panel-queryfilter-scope-publisher "
    "> div.panel-collapse.collapse.in > div > div:nth-child(3)"
)
MEDIA_FILTER_KEEP_LABELS = ["報刊", "綜合新聞", "香港"]


def _get_credentials(prefix="hkkw"):
    """Helper function to get credentials from secrets or manual input"""
    try:
        group_name = st.secrets["wisers"]["group_name"]
        username = st.secrets["wisers"]["username"]
        password = st.secrets["wisers"]["password"]
        svc_dict = dict(st.secrets["firebase"]["service_account"])
        bucket = st.secrets.get("firebase", {}).get("storage_bucket") or f"{svc_dict['project_id']}.appspot.com"
        st.success("✅ Credentials loaded from secrets")
        st.info(f"Group: {group_name}\n\nUsername: {username}\n\nPassword: ****\n\nFirebase Bucket: {bucket}")
        return group_name, username, password, bucket
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ Secrets not found. Please enter credentials manually:")
        group_name = st.text_input("Group Name", value="SPRG1", key=f"{prefix}-group")
        username = st.text_input("Username", placeholder="Enter username", key=f"{prefix}-username")
        password = st.text_input("Password", type="password", placeholder="Enter password", key=f"{prefix}-password")
        bucket = None
        return group_name, username, password, bucket


def _get_api_key(prefix="hkkw"):
    """Helper function to get API key from secrets or manual input"""
    try:
        api_key = st.secrets["wisers"]["api_key"]
        st.success(f"✅ 2Captcha API Key loaded: {api_key[:8]}...")
        return api_key
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ API key not found in secrets")
        return st.text_input("2Captcha API Key", type="password", placeholder="Enter API key", key=f"{prefix}-api-key")


def _build_default_keyword_text(config):
    presets = config.get("keyword_presets") or []
    if presets:
        keywords = []
        for preset in presets:
            if isinstance(preset, dict):
                keywords.append(preset.get("keywords") or "")
            else:
                keywords.append(str(preset))
        return "\n".join([k for k in keywords if k.strip()])
    return config.get("default_keyword_text") or HK_KEYWORD_DEFAULT


def _parse_keyword_presets(raw_text: str):
    lines = [line.strip() for line in (raw_text or "").splitlines()]
    return [line for line in lines if line]


def _get_keyword_presets(prefix: str, config):
    default_text = _build_default_keyword_text(config)
    raw_text = st.session_state.get(f"{prefix}_keyword_text") or default_text
    presets = _parse_keyword_presets(raw_text)
    if not presets and default_text:
        presets = [default_text.strip()]
    return presets


def _is_item_in_period(item, period_name: str) -> bool:
    if period_name == "today":
        return item.get("day_tag") != "周日"
    if period_name == "yesterday":
        return item.get("day_tag") == "周日"
    return True


def _build_preview_list_from_raw(rawlist):
    preview_list = []
    for i, item in enumerate(rawlist):
        item["original_index"] = i
        hover_html = item.get("hover_html", "")
        item["news_id"] = extract_news_id_from_html(hover_html)

        hover_text = item.get("hover_text", "")
        if "\n" in hover_text:
            lines = hover_text.split("\n", 2)
            if len(lines) > 1 and lines[0].strip() == item.get("title", "").strip():
                raw_meta = lines[1].strip()
            else:
                raw_meta = lines[0].strip()
        else:
            raw_meta = ""
        item["formatted_metadata"] = parse_metadata(raw_meta)
        preview_list.append(item)
    return preview_list


def _run_keyword_preview_with_driver(
    driver,
    wait,
    st_module,
    keyword_presets,
    include_content,
    max_words,
    min_words,
    max_articles,
    start_from_results=False,
):
    is_monday = is_hkt_monday()
    per_period_max = max(1, max_articles // 2) if is_monday else max_articles
    periods = [("today", None)]
    if is_monday:
        periods.append(("yesterday", "周日"))

    combined_filtered = []
    has_run_search = bool(start_from_results)
    logger = get_logger(st_module) if st_module else None
    screenshot_dir = os.getenv("WISERS_SCREENSHOT_DIR") or os.path.join(".", "artifacts", "screenshots")

    for period_name, day_tag in periods:
        if period_name != "today":
            set_date_range_period(
                driver=driver,
                wait=wait,
                st_module=st_module,
                period_name=period_name,
            )

        for preset_index, keyword in enumerate(keyword_presets):
            use_edit_modal = has_run_search or (period_name != "today") or (preset_index > 0)
            search_meta = run_keyword_search_task(
                driver=driver,
                wait=wait,
                st_module=st_module,
                keyword=keyword,
                include_content=include_content,
                use_edit_modal=use_edit_modal,
                logger=logger,
                screenshot_dir=screenshot_dir,
            )
            has_run_search = True

            rawlist = scrape_hover_popovers(
                driver=driver,
                wait=wait,
                st_module=st_module,
                max_articles=per_period_max,
                logger=logger,
                screenshot_dir=screenshot_dir,
            ) or []
            raw_count = len(rawlist)
            for item in rawlist:
                item["keyword_preset"] = keyword
                item["keyword_preset_index"] = preset_index
                if day_tag:
                    item["day_tag"] = day_tag

            if st_module:
                st_module.info(f"✅ {period_name} 預設 {preset_index + 1} 抓取了 {raw_count} 篇懸停預覽")

            filtered_rawlist = []
            for item in rawlist:
                hover_text = item.get("hover_text", "")
                word_matches = re.findall(r"(\\d+)\\s*字", hover_text)
                if word_matches:
                    word_count = int(word_matches[0])
                    if min_words <= word_count <= max_words:
                        filtered_rawlist.append(item)
                    else:
                        if st_module:
                            st_module.write(f"已過濾: {item.get('title', 'Unknown')} ({word_count} 字)")
                else:
                    filtered_rawlist.append(item)

            filtered_count = len(filtered_rawlist)
            if st_module:
                st_module.info(f"📊 {period_name} 預設 {preset_index + 1} 字數過濾後剩餘: {filtered_count} 篇")

            if search_meta.get("no_results", False):
                st_module.warning(f"⚠️ {period_name} 預設 {preset_index + 1} 搜索结果为 0 篇。")
            elif raw_count == 0:
                st_module.warning(f"⚠️ {period_name} 預設 {preset_index + 1} 搜索有结果，但懸浮爬取為 0 篇。")
            elif raw_count > 0 and filtered_count == 0:
                st_module.warning(f"⚠️ {period_name} 預設 {preset_index + 1} 搜索有結果，但全部被字數過濾條件篩掉。")

            combined_filtered.extend(filtered_rawlist)

    return _build_preview_list_from_raw(combined_filtered)


def _ensure_keyword_state(prefix: str, config):
    keyword_key = f"{prefix}_keyword_text"
    content_key = f"{prefix}_include_content"
    if keyword_key not in st.session_state:
        st.session_state[keyword_key] = _build_default_keyword_text(config)
    if content_key not in st.session_state:
        st.session_state[content_key] = False


def _render_keyword_controls(prefix: str, config):
    _ensure_keyword_state(prefix, config)
    keyword_key = f"{prefix}_keyword_text"
    content_key = f"{prefix}_include_content"

    st.subheader("🔎 搜索設定（關鍵詞直搜）")
    st.checkbox(
        "包含內文（預設只搜標題）",
        key=content_key,
        value=st.session_state.get(content_key, False),
    )
    st.text_area(
        "關鍵詞（每行一組，組內用 / 分隔）",
        key=keyword_key,
        height=150,
    )


def _apply_search_filters(driver, wait, st_module, include_content: bool):
    set_media_filters_in_panel(
        driver=driver,
        wait=wait,
        st_module=st_module,
        keep_labels=MEDIA_FILTER_KEEP_LABELS,
        container_selector=MEDIA_FILTER_CONTAINER_SELECTOR,
    )
    set_keyword_scope_checkboxes(
        driver=driver,
        st_module=st_module,
        title_checked=True,
        content_checked=include_content,
    )


def run_keyword_search_task(
    driver,
    wait,
    st_module,
    keyword: str,
    include_content: bool,
    use_edit_modal: bool = False,
    logger=None,
    screenshot_dir=None,
):
    _apply_search_filters(driver, wait, st_module, include_content)
    inject_cjk_font_css(driver, st_module=st_module)
    if st_module:
        try:
            img_bytes = driver.get_screenshot_as_png()
            st_module.image(
                img_bytes,
                caption="🔎 已完成搜索设置（媒體來源 + 標題/內文）",
            )
            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                fname = f"{ts}_filters_ready.png"
                if screenshot_dir:
                    os.makedirs(screenshot_dir, exist_ok=True)
                    local_fp = os.path.join(screenshot_dir, fname)
                    with open(local_fp, "wb") as f:
                        f.write(img_bytes)
                if logger and hasattr(logger, "upload_screenshot_bytes"):
                    logger.upload_screenshot_bytes(img_bytes, filename=fname)
            except Exception:
                pass
        except Exception as e:
            st_module.warning(f"截图失败：{e}")
    if use_edit_modal:
        search_title_via_edit_search_modal(
            driver=driver,
            wait=wait,
            st_module=st_module,
            keyword=keyword,
            logger=logger,
            screenshot_dir=screenshot_dir,
        )
    else:
        search_title_from_home(
            driver=driver,
            wait=wait,
            st_module=st_module,
            keyword=keyword,
            logger=logger,
            screenshot_dir=screenshot_dir,
        )

    if wait_for_search_results(
        driver=driver,
        wait=wait,
        st_module=st_module,
        logger=logger,
        screenshot_dir=screenshot_dir,
        loading_grace_seconds=25,
        verify_no_results_wait=6,
    ):
        wait_for_results_panel_ready(driver=driver, wait=wait, st_module=st_module)
        ensure_results_list_visible(driver=driver, wait=wait, st_module=st_module)
        scroll_to_load_all_content(driver=driver, st_module=st_module)
        wait_for_ajax_complete(driver, timeout=10)
        return {"no_results": False}
    return {"no_results": True}
