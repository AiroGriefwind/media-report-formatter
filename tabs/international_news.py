import streamlit as st
import tempfile
import time
import traceback
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By

import pytz  # ✅ 新增 TODAY 用

import re

import json, tempfile, os

HKT = pytz.timezone('Asia/Hong_Kong')
TODAY = datetime.now(HKT).strftime("%Y%m%d")  # ✅ 全域 TODAY

# 引入 AI 工具
from utils.ai_screening_utils import run_ai_screening

# 引入配置常數
from utils.config import LOCATION_ORDER

# 引入 Wisers 工具
from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    switch_language_to_traditional_chinese,
    logout,
    robust_logout_request,
)
from utils.web_scraping_utils import scrape_hover_popovers
from utils.international_news_utils import (
    run_international_news_task,
    create_hover_preview_report,
    should_scrape_article_based_on_metadata,
    scrape_specific_articles_by_indices,
    scrape_articles_by_news_id,  
    extract_news_id_from_html, 
    parse_metadata,
    create_international_news_report
)

from utils.intl_trim_utils import trim_docx

# 引入 Firebase Logger
from utils.firebase_logging import ensure_logger

# ✅ 初始化 logger（修正：確保 st 已存在）
if 'fb_logger' not in st.session_state:
    st.session_state['fb_logger'] = ensure_logger(st, run_context="international_news")
fb_logger = st.session_state['fb_logger']

# ✅ 修正：session_state 初始化移到這裡，且加 TODAY
if 'intl_articles_list' not in st.session_state:
    # 載入既有資料
    st.session_state.intl_articles_list = fb_logger.load_json_from_date_folder('preview_articles.json', [])
    st.session_state.intl_sorted_dict = fb_logger.load_json_from_date_folder('user_final_list.json', {})
    st.session_state.intl_final_articles = fb_logger.load_json_from_date_folder('full_scraped_articles.json', [])
    if st.session_state.intl_articles_list:
        st.success(f"✅ 已載入今日 {TODAY} 預覽資料，避免重爬！")
        st.info(f"📁 Firebase 路徑: international_news/{TODAY}/")

# === UI 輔助函數  ===

# 🔥 智能檢查今日進度函數
def check_today_progress():
    """檢查 Firebase 中今日三個文件的存在狀態"""
    preview_exists = bool(fb_logger.load_json_from_date_folder('preview_articles.json', []))
    user_list_exists = bool(fb_logger.load_json_from_date_folder('user_final_list.json', {}))
    final_articles_exists = bool(fb_logger.load_json_from_date_folder('full_scraped_articles.json', []))
    
    total_preview = len(fb_logger.load_json_from_date_folder('preview_articles.json', []))
    total_user_list = sum(len(v) for v in fb_logger.load_json_from_date_folder('user_final_list.json', {}).values())
    
    return {
        'preview': preview_exists,
        'user_list': user_list_exists,
        'final_articles': final_articles_exists,
        'preview_count': total_preview,
        'user_list_count': total_user_list
    }

 # 文章选择池相关函数

def article_uid(article: dict) -> str:
    """Stable uid for cross-rerun button keys and de-dup."""
    return (
        article.get("news_id")
        or article.get("newsid")
        or article.get("url")
        or str(article.get("original_index", "na"))
    )

def build_grouped_data(article_list: list, location_order: list) -> dict:
    """
    Rebuild grouped dict using the same logic you already use:
    main_location -> location; is_tech_news -> Tech News; fallback Others.
    """
    grouped = {loc: [] for loc in location_order}
    for item in article_list:
        ai = item.get("ai_analysis", {}) or {}
        loc = ai.get("main_location", "Others")
        if ai.get("is_tech_news", False):
            loc = "Tech News"
        if loc not in grouped:
            loc = "Others"
        grouped[loc].append(item)
    return grouped

def rebuild_pool_from_preview(preview_list: list, selected_dict: dict, location_order: list) -> dict:
    """pool = preview_grouped - selected (by uid)."""
    pool = build_grouped_data(preview_list, location_order)
    selected_uids = set()
    for loc, items in (selected_dict or {}).items():
        for a in items:
            selected_uids.add(article_uid(a))

    for loc in list(pool.keys()):
        pool[loc] = [a for a in pool[loc] if article_uid(a) not in selected_uids]
    return pool

# 🔥 ✅ 恢復進度函數（新增）
def restore_progress(stage):
    """一鍵恢復指定階段的進度"""
    if stage == "ui_sorting":
        # 1) restore selected
        st.session_state.intl_sorted_dict = fb_logger.load_json_from_date_folder("user_final_list.json", {})
        st.session_state.intl_stage = "ui_sorting"

        # 2) rebuild pool from preview - selected
        preview_list = fb_logger.load_json_from_date_folder("preview_articles.json", [])
        location_order = LOCATION_ORDER

        st.session_state.intl_pool_dict = rebuild_pool_from_preview(
            preview_list=preview_list,
            selected_dict=st.session_state.intl_sorted_dict,
            location_order=location_order,
        )

    elif stage == "finished":
        st.session_state.intl_final_articles = fb_logger.load_json_from_date_folder("full_scraped_articles.json", [])
        st.session_state.intl_final_docx = None
        st.session_state.intl_stage = "finished"

    st.rerun()

# # 🔥 恢復進度函數
# def restore_progress(stage):
#     """一鍵恢復指定階段的進度"""
#     if stage == "ui_sorting":
#         st.session_state.intl_sorted_dict = fb_logger.load_json_from_date_folder('user_final_list.json', {})
#         st.session_state.intl_stage = "ui_sorting"
#     elif stage == "finished":
#         st.session_state.intl_final_articles = fb_logger.load_json_from_date_folder('full_scraped_articles.json', [])
#         st.session_state.intl_final_docx = None  # 需要重新生成下載鏈接
#         st.session_state.intl_stage = "finished"
#     st.rerun()

def move_article(location, index, direction):
    """Move article up or down within its category"""
    articles = st.session_state.intl_sorted_dict[location]
    if direction == 'up' and index > 0:
        articles[index], articles[index-1] = articles[index-1], articles[index]
    elif direction == 'down' and index < len(articles) - 1:
        articles[index], articles[index+1] = articles[index+1], articles[index]
    st.session_state.intl_last_update = time.time() # Force rerun

def delete_article(location, index):
    """Remove article from list"""
    st.session_state.intl_sorted_dict[location].pop(index)
    st.session_state.intl_last_update = time.time()

def move_to_top(location, index):
    """Move article to top of its list"""
    articles = st.session_state.intl_sorted_dict[location]
    if index > 0:
        article = articles.pop(index)
        articles.insert(0, article)
        st.session_state.intl_last_update = time.time()

def add_to_selected(location: str, pool_index: int):
    """Move from pool -> selected."""
    article = st.session_state.intl_pool_dict[location].pop(pool_index)
    st.session_state.intl_sorted_dict.setdefault(location, []).append(article)
    st.session_state.intl_last_update = time.time()

def remove_to_pool(location: str, selected_index: int):
    """Move from selected -> pool."""
    article = st.session_state.intl_sorted_dict[location].pop(selected_index)
    st.session_state.intl_pool_dict.setdefault(location, []).append(article)
    st.session_state.intl_last_update = time.time()


def render_article_card(article, index, location, total_count, mode: str):
    """
    mode:
      - "selected": show up/down/top + 删除(移回候选池)
      - "pool": show 添加
    """
    score = article.get("ai_analysis", {}).get("overall_score", 0)
    color = "#ff4b4b" if score >= 20 else "#ffa500" if score >= 10 else "#21c354"

    card_style = """
    <style>
    .article-card {
        background-color: #0f172a10;
        border-radius: 10px;
        padding: 12px;
        margin-bottom: 10px;
        border-left: 5px solid %s;
    }
    .article-meta { font-size: 0.85em; opacity: 0.85; }
    </style>
    """
    st.markdown(card_style % color, unsafe_allow_html=True)

    uid = article_uid(article)
    keybase = f"{location}-{uid}-{mode}"

    with st.container():
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            prefix = f"{index + 1}. " if mode == "selected" else ""
            st.markdown(f"**{prefix}{article.get('title','(no title)')}**")
        with col2:
            st.caption(f"Score: {score}")

        meta_text = article.get("formatted_metadata") or "No metadata"
        st.markdown(f"<div class='article-meta'>{meta_text}</div>", unsafe_allow_html=True)

        with st.expander("查看摘要內容"):
            content = article.get("hover_text", "No content")
            st.markdown(content)

        if mode == "selected":
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                if index > 0:
                    st.button("↑", key=f"up-{keybase}", on_click=move_article, args=(location, index, "up"))
            with c2:
                if index < total_count - 1:
                    st.button("↓", key=f"down-{keybase}", on_click=move_article, args=(location, index, "down"))
            with c3:
                if index > 0:
                    st.button("置顶", key=f"top-{keybase}", on_click=move_to_top, args=(location, index))
            with c4:
                st.button("删除", key=f"rm-{keybase}", type="secondary", on_click=remove_to_pool, args=(location, index))
        else:
            st.button("添加", key=f"add-{keybase}", type="primary", on_click=add_to_selected, args=(location, index))

    st.markdown("---")


# === Docx Trimming Function ===
def trim_docx_bytes_with_userlist(docx_bytes: bytes, user_final_list_dict: dict, keep_body_paras: int = 3) -> bytes:
    if not docx_bytes:
        raise ValueError("docx_bytes is empty")
    if not isinstance(user_final_list_dict, dict):
        raise ValueError("user_final_list_dict must be dict")

    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
    tmp_js = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    try:
        tmp_in.write(docx_bytes)
        tmp_in.close()

        tmp_js.write(json.dumps(user_final_list_dict, ensure_ascii=False).encode("utf-8"))
        tmp_js.close()

        tmp_out_path = tmp_in.name.replace(".docx", "_trimmed.docx")
        trim_docx(tmp_in.name, tmp_js.name, tmp_out_path, keep_body_paras=keep_body_paras)

        with open(tmp_out_path, "rb") as f:
            return f.read()
    finally:
        for p in [tmp_in.name, tmp_js.name, tmp_in.name.replace(".docx", "_trimmed.docx")]:
            try:
                os.remove(p)
            except:
                pass


def ensure_trimmed_docx_in_firebase_and_session(fb_logger):
    import streamlit as st

    if st.session_state.get("intl_final_docx_trimmed"):
        return

    # 1) 先从 Firebase 直接拿 trimmed
    trimmed_bytes = fb_logger.load_final_docx_from_date_folder("final_report_trimmed.docx")
    if trimmed_bytes:
        st.session_state.intl_final_docx_trimmed = trimmed_bytes
        return

    # 2) 没有 trimmed，就用 final_report + user_final_list 现场生成
    base_docx = st.session_state.get("intl_final_docx") or fb_logger.load_final_docx_from_date_folder("final_report.docx")
    if not base_docx:
        raise RuntimeError("Cannot load final_report.docx from session or Firebase")

    user_final_list = fb_logger.load_json_from_date_folder("user_final_list.json", {})
    if not user_final_list:
        raise RuntimeError("Cannot load user_final_list.json from Firebase")

    trimmed_bytes = trim_docx_bytes_with_userlist(base_docx, user_final_list, keep_body_paras=3)

    # 3) 回存 Firebase + 写 session
    fb_logger.save_final_docx_bytes_to_date_folder(trimmed_bytes, "final_report_trimmed.docx")
    st.session_state.intl_final_docx_trimmed = trimmed_bytes


# === 主流程函數 ===

def _handle_international_news_logic(
    group_name_intl, username_intl, password_intl, api_key_intl,
    run_headless_intl, keep_browser_open_intl, max_words, min_words, max_articles
):
    """
    Revised flow with Firebase persistence + Mobile-First UI
    """
    
    global TODAY
    TODAY = datetime.now(HKT).strftime("%Y%m%d")  # 更新 TODAY

    # 🔥 ✅ 智能檢查今日進度函數（新增）
    def check_today_progress():
        """檢查 Firebase 中今日三個文件的存在狀態"""
        preview_exists = bool(fb_logger.load_json_from_date_folder('preview_articles.json', []))
        user_list_exists = bool(fb_logger.load_json_from_date_folder('user_final_list.json', {}))
        final_articles_exists = bool(fb_logger.load_json_from_date_folder('full_scraped_articles.json', []))
        
        total_preview = len(fb_logger.load_json_from_date_folder('preview_articles.json', []))
        total_user_list = sum(len(v) for v in fb_logger.load_json_from_date_folder('user_final_list.json', {}).values())
        
        return {
            'preview': preview_exists,
            'user_list': user_list_exists,
            'final_articles': final_articles_exists,
            'preview_count': total_preview,
            'user_list_count': total_user_list
        }



    # ✅ 確保 fb_logger 可用（保留原有的）
    fb_logger = st.session_state.get('fb_logger') or ensure_logger(st, run_context="international_news")

    # # Locations Order（保留原有的）
    # LOCATION_ORDER = ['United States', 'Russia', 'Europe', 'Middle East', 
    #                   'Southeast Asia', 'Japan', 'Korea', 'China', 'Others', 'Tech News']

    # 🔥 ✅ 智能首頁邏輯（新增，完全替換原開頭初始化）
    if "intl_stage" not in st.session_state:
        st.session_state.intl_stage = "smart_home"
    
    if st.session_state.intl_stage == "smart_home":
        st.header("🌐 國際新聞 - 智能進度恢復")
        st.info(f"📁 Firebase: `international_news/{TODAY}/` | {datetime.now().strftime('%H:%M')}")
        
        # 🔥 檢查進度
        progress = check_today_progress()
        
        # 🔥 美化進度儀表板
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📄 預覽文章", f"{progress['preview_count']} 篇", 
                    "✅" if progress['preview'] else "❌")
        with col2:
            st.metric("👤 用戶排序", f"{progress['user_list_count']} 篇", 
                    "✅" if progress['user_list'] else "❌")
        with col3:
            st.metric("✅ 最終全文", f"{len(fb_logger.load_json_from_date_folder('full_scraped_articles.json', []))} 篇", 
                    "✅" if progress['final_articles'] else "❌")
        
        st.divider()
        
        # 🔥 三選一按鈕（依優先順序）
        if progress['final_articles']:  # 100% 完成
            st.success("🎉 **今日任務已100%完成！立即下載最終報告**")
            if st.button("📥 下載最終 Word 報告（100%進度）", type="primary", use_container_width=True):
                restore_progress("finished")
        elif progress['user_list']:     # 50% 排序完成
            st.warning("⏳ **今日已完成50%（用戶排序），繼續全文爬取**")
            if st.button("👤 恢復排序界面繼續（50%進度）", type="primary", use_container_width=True):
                restore_progress("ui_sorting")
        elif progress['preview']:       # 25% 預覽完成
            st.info(f"🧠 AI 懸浮預覽已完成 ({progress['preview_count']} 篇文章)")
            if st.button(f"🎯 展示目前預覽進度 ({progress['preview_count']} 條)", type="secondary", use_container_width=True):
                # ✅ 載入預覽 JSON（已含 AI 分析）
                preview_list = fb_logger.load_json_from_date_folder('preview_articles.json', [])
                st.session_state.intl_articles_list = preview_list
                
                # ✅ 複製 init 階段的分組邏輯，直接從 preview_list 生成 sorted_dict
                # LOCATION_ORDER = [
                #     "United States", "Russia", "Europe", "Middle East", 
                #     "Southeast Asia", "Japan", "Korea", "China", "Others", "Tech News"
                # ]
                grouped_data = {loc: [] for loc in LOCATION_ORDER}
                for item in preview_list:
                    loc = item.get('ai_analysis', {}).get('main_location', 'Others')
                    if item.get('ai_analysis', {}).get('is_tech_news', False):
                        loc = 'Tech News'
                    grouped_data.setdefault(loc, []).append(item)
                
                # 1) Pool = 所有候选
                st.session_state.intlpooldict = grouped_data

                # 2) Selected = 默认全空（但 key 要齐全，避免 bool({}) == False）
                st.session_state.intlsorteddict = {loc: [] for loc in LOCATION_ORDER}

                # 3) 仍然写 userfinallist.json（存“已选清单”）
                fb_logger.savejsontodatefolder(st.session_state.intlsorteddict, "userfinallist.json")

                st.success("✅ 已进入选择模式：默认未选择，点击『添加』加入已选清单。")
                st.session_state.intlstage = "uisorting"
                st.rerun()
        else:                           # 0% 全新開始
            st.success("🆕 **今日全新任務，開始抓取預覽**")
            if st.button("🚀 開始新任務（0%進度）", type="primary", use_container_width=True):
                st.session_state.intl_stage = "init"
                st.rerun()
        
        st.divider()
        
        # 🔥 備用選項（小按鈕）
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("🔄 忽略進度重來", type="secondary"):
                for key in ['intl_stage', 'intl_sorted_dict', 'intl_final_articles', 'intl_articles_list']:
                    if key in st.session_state: del st.session_state[key]
                st.session_state.intl_stage = "init"
                st.rerun()
        with col_b:
            if st.button("📋 查看 JSON 數據", type="secondary"):
                st.session_state.intl_stage = "data_viewer"
                st.rerun()
        
        st.stop()
    
    elif st.session_state.intl_stage == "data_viewer":
        st.header("📋 JSON 數據檢視")
        if st.button("返回進度頁"):
            st.session_state.intl_stage = "smart_home"
            st.rerun()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.json(fb_logger.load_json_from_date_folder('preview_articles.json', []))
        with col2:
            st.json(fb_logger.load_json_from_date_folder('user_final_list.json', {}))
        with col3:
            st.json(fb_logger.load_json_from_date_folder('full_scraped_articles.json', []))
        if st.button("返回進度頁"):
            st.session_state.intl_stage = "smart_home"
            st.rerun()
        st.stop()

    try:
        # === Stage 1: Login, Search, Preview, AI Analysis ===
        if st.session_state.intl_stage == "init":
            if st.button("🚀 開始任務：抓取預覽 + AI 分析"):
                with st.spinner("第一步：登錄 Wisers 並抓取預覽..."):
                    driver = setup_webdriver(headless=run_headless_intl, st_module=st)
                    if not driver: st.stop()
                    
                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name_intl, username=username_intl, password=password_intl, api_key=api_key_intl, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)
                    
                    run_international_news_task(driver=driver, wait=wait, st_module=st, max_articles=max_articles)
                    
                    

                    # Scrape hover popovers
                    rawlist = []  # 初始化
                    rawlist = scrape_hover_popovers(driver=driver, wait=wait, st_module=st) or []
                    if st: st.info(f"✅ 抓取了 {len(rawlist)} 篇懸停預覽")

                    # Logout before filter
                    st.info("暫時登出以釋放 Session...")
                    try:
                        robust_logout_request(driver, st)
                    except Exception as e:
                        st.warning(f"登出時出現問題: {e}")
                    driver.quit()

                    # Filter by word count from hover_text
                    filtered_rawlist = []
                    for item in rawlist:  # 現在保證 rawlist 存在
                        hover_text = item.get("hover_text", "")
                        word_matches = re.findall(r'(\d+)\s*字', hover_text)
                        if word_matches:
                            word_count = int(word_matches[0])
                            if min_words <= word_count <= max_words:
                                filtered_rawlist.append(item)
                            else:
                                if st: st.write(f"已過濾: {item.get('title', 'Unknown')} ({word_count} 字)")
                        else:
                            # 無字數 metadata，保留
                            filtered_rawlist.append(item)

                    rawlist = filtered_rawlist
                    if st: st.info(f"📊 字數過濾後剩餘: {len(rawlist)} 篇")


                    # Filter & AI Analysis
                    filtered_list = []
                    for i, item in enumerate(rawlist):
                        item['original_index'] = i
                        filtered_list.append(item)
                    
                    with st.spinner(f"第二步：AI 正在分析 {len(filtered_list)} 篇文章..."):
                        analyzed_list = run_ai_screening(
                            filtered_list,
                            progress_callback=lambda i, n, t: st.text(f"分析中 ({i+1}/{n}): {t}...")
                        )
                    
                    for item in analyzed_list:
                        hover_text = item.get("hover_text", "")

                        # ✅ 新增：提取 news_id
                        hover_html = item.get('hover_html', '')
                        news_id = extract_news_id_from_html(hover_html)
                        item['news_id'] = news_id

                        if "\n" in hover_text:
                            lines = hover_text.split("\n", 2)
                            if len(lines) > 1 and lines[0].strip() == item.get("title", "").strip():
                                raw_meta = lines[1].strip()
                            else:
                                raw_meta = lines[0].strip()
                        else:
                            raw_meta = ""
                        
                        item["formatted_metadata"] = parse_metadata(raw_meta)

                    # Group by Location
                    grouped_data = {loc: [] for loc in LOCATION_ORDER}
                    for item in analyzed_list:
                        loc = item.get('ai_analysis', {}).get('main_location', 'Others')
                        if item.get('ai_analysis', {}).get('is_tech_news', False):
                            loc = 'Tech News'
                        if loc not in grouped_data: loc = 'Others'
                        grouped_data[loc].append(item)
                    
                    # ✅ 把最终带有 hover 摘要 + AI 分析的数据，写回 session
                    st.session_state.intl_articles_list = analyzed_list

                    # ✅ 悬浮预览 + AI 完成后再保存 preview_articles.json
                    fb_logger.save_json_to_date_folder(
                        st.session_state.intl_articles_list,
                        'preview_articles.json'
                    )

                    st.session_state.intl_sorted_dict = grouped_data
                    st.session_state.intl_stage = "ui_sorting"
                    st.rerun()

        # === Stage 2: UI Sorting（自動保存） ===
        if st.session_state.intl_stage == "ui_sorting":
            st.header("📱 新聞排序與篩選")
            st.info(f"💾 自動保存至 Firebase: `international_news/{TODAY}/user_final_list.json`")
            
            # Global Actions
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                if st.button("🔄 重新開始 (清除數據)"):
                    st.session_state.intl_stage = "init"
                    st.rerun()
            with col_g2:
                if st.button("💾 手動保存排序"):
                    fb_logger.save_json_to_date_folder(st.session_state.intl_sorted_dict, 'user_final_list.json')
                    st.success("✅ 已保存用戶排序清單！")

            st.write("---")
            
            # Calculate counts
            total_articles = sum(len(v) for v in st.session_state.intl_sorted_dict.values())
            st.markdown(f"**總文章數: {total_articles}**")

            # Render Categories
            for location in LOCATION_ORDER:
                articles = st.session_state.intl_sorted_dict.get(location, [])
                if not articles: continue
                
                with st.expander(f"{location} ({len(articles)})", expanded=True):
                    for i, article in enumerate(articles):
                        render_article_card(article, i, location, len(articles))

            st.write("---")
            
            # ✅ 關鍵：確認前自動保存
            if st.button("✅ 確認排序並開始全文爬取", type="primary", use_container_width=True):
                fb_logger.save_json_to_date_folder(st.session_state.intl_sorted_dict, 'user_final_list.json')
                st.success("💾 用戶排序已自動保存至 Firebase")
                st.session_state.intl_stage = "final_scraping"
                st.rerun()

        # === Stage 3: Final Scrape ===
        if st.session_state.intl_stage == "final_scraping":
            st.header("⏳ 最終處理中...")
            
            # Flatten list
            final_list = []
            for loc in LOCATION_ORDER:
                if loc in st.session_state.intl_sorted_dict:
                    final_list.extend(st.session_state.intl_sorted_dict[loc])
            
            if not final_list:
                st.warning("沒有文章被選中。")
                if st.button("返回"):
                    st.session_state.intl_stage = "ui_sorting"
                    st.rerun()
                st.stop()

            with st.spinner(f"正在爬取 {len(final_list)} 篇文章的全文內容..."):
                try:
                    driver = setup_webdriver(headless=run_headless_intl, st_module=st)
                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name_intl, username=username_intl, password=password_intl, api_key=api_key_intl, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)
                    
                    
                    # ✅ 重新搜索以显示结果页（但不再依赖索引）
                    run_international_news_task(driver=driver, wait=wait, st_module=st)
                    
                    # ✅ 使用新函数：按 news_id/标题定位而非索引
                    full_articles_data = scrape_articles_by_news_id(driver, wait, final_list, st_module=st)
                    
                    # ✅ 保存最終爬取結果
                    st.session_state.intl_final_articles = full_articles_data
                    fb_logger.save_json_to_date_folder(full_articles_data, 'full_scraped_articles.json')
                    
                    # Generate Docx
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                        out_path = create_international_news_report(
                            articles_data=full_articles_data,
                            output_path=tmp.name,
                            st_module=st
                        )
                        with open(out_path, "rb") as f:
                            file_data = f.read()

                    st.session_state.intl_final_docx = file_data

                    # ✅ 這裡保存 final_report 到 Firebase
                    fb_logger.save_final_docx_to_date_folder(full_articles_data, 'final_report.docx')

                    # ✅ 生成 trimmed + 保存到 Firebase + 放进 session
                    user_final_list = fb_logger.load_json_from_date_folder("user_final_list.json", {})
                    trimmed_bytes = trim_docx_bytes_with_userlist(st.session_state.intl_final_docx, user_final_list, keep_body_paras=3)

                    fb_logger.save_final_docx_bytes_to_date_folder(trimmed_bytes, "final_report_trimmed.docx")
                    st.session_state.intl_final_docx_trimmed = trimmed_bytes

                    st.session_state.intl_stage = "finished"
                    robust_logout_request(driver, st)
                    driver.quit()
                    st.rerun()

                    
                    
                except Exception as e:
                    st.error(f"爬取失敗: {e}")
                    if st.button("重試"):
                        st.rerun()

        # === Stage 4: Download（完全替換） ===
        if st.session_state.intl_stage == "finished":
            st.header("🎉 任務全部完成！")
            
            # 🔥 智能重新生成/載入 DOCX
            if 'intl_final_docx' not in st.session_state or not st.session_state.intl_final_docx:
                with st.spinner("🔄 從 Firebase 重新生成下載文件..."):
                    # 優先從已保存的 DOCX 文件載入
                    docx_bytes = fb_logger.load_final_docx_from_date_folder('final_report.docx')
                    if not docx_bytes:
                        # 備用方案：從文章數據重新生成
                        final_articles = st.session_state.get('intl_final_articles', fb_logger.load_json_from_date_folder('full_scraped_articles.json', []))
                        if final_articles:
                            docx_bytes = fb_logger.save_final_docx_to_date_folder(final_articles, 'final_report.docx')
                            docx_bytes = fb_logger.load_final_docx_from_date_folder('final_report.docx')
                    
                    if docx_bytes:
                        st.session_state.intl_final_docx = docx_bytes
                    else:
                        st.error("❌ 無法恢復最終報告，請重新執行爬取")
                        st.stop()
            
            # --- 确保 trimmed 已恢复/已生成 ---
            ensure_trimmed_docx_in_firebase_and_session(fb_logger)

            # 🔥 下載按鈕
            colA, colB = st.columns(2)

            with colA:
                st.download_button(
                    label="📥 下載最終 Word 報告",
                    data=st.session_state.intl_final_docx,
                    file_name=f"Intl_News_Report_{TODAY}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="primary",
                    use_container_width=True,
                    help="包含今日最終排序的完整新聞報告"
                )

            with colB:
                st.download_button(
                    label="📥 下載（三段版）Word 報告",
                    data=st.session_state.intl_final_docx_trimmed,
                    file_name=f"Intl_News_Report_{TODAY}_trimmed.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="secondary",
                    use_container_width=True,
                    help="每篇：標題 + metadata + 正文三段（副標題不佔段數）"
                )


            # 🔥 進度摘要
            col1, col2 = st.columns(2)
            with col1:
                st.metric("總文章數", len(st.session_state.get('intl_final_articles', [])))
            with col2:
                st.metric("Firebase 狀態", "✅ 完整備份")
            
            st.success(f"💾 完整備份: `international_news/{TODAY}/`")
            
            if st.button("🔄 開始新任務"):
                st.session_state.intl_stage = "smart_home"
                st.rerun()

    except Exception as e:
        st.error(f"發生未預期的錯誤: {e}")
        st.code(traceback.format_exc())


def render_international_news_tab():
    """
    Render the International News tab content
    """
    st.header("International News")
    
    # 1. 獲取憑證 (這部分邏輯從原來的 international_news.py 搬過來)
    # Helper to get credentials
    def _get_credentials_intl():
        try:
            group_name = st.secrets["wisers"]["group_name"]
            username = st.secrets["wisers"]["username"]
            password = st.secrets["wisers"]["password"]
            return group_name, username, password
        except:
            return None, None, None

    def _get_api_key_intl():
        try:
            return st.secrets["wisers"]["api_key"]
        except:
            return None

    # Sidebar Options
    with st.sidebar:
        st.subheader("International News Settings")
        max_words = st.slider("Max Words", 200, 2000, 1000)
        min_words = st.slider("Min Words", 50, 500, 200)
        max_articles = st.slider("Max Articles", 10, 100, 30)
        run_headless = st.checkbox("Headless Mode", value=True)
        keep_open = st.checkbox("Keep Browser Open", value=False)
        
        # Credentials Input (Fallback)
        group, user, pwd = _get_credentials_intl()
        api_key = _get_api_key_intl()
        
        if not all([group, user, pwd, api_key]):
            st.warning("請在 secrets.toml 配置憑證，或在此輸入：")
            group = st.text_input("Group", value=group or "")
            user = st.text_input("User", value=user or "")
            pwd = st.text_input("Password", type="password", value=pwd or "")
            api_key = st.text_input("2Captcha Key", type="password", value=api_key or "")

    # 2. 執行主邏輯
    if all([group, user, pwd, api_key]):
        _handle_international_news_logic(
            group, user, pwd, api_key,
            run_headless, keep_open, max_words, min_words, max_articles
        )
    else:
        st.error("請提供完整的 Wisers 帳號密碼及 API Key 才能開始。")
