import streamlit as st
import tempfile
import time
import traceback
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By

import pytz  # ✅ 新增 TODAY 用

HKT = pytz.timezone('Asia/Hong_Kong')
TODAY = datetime.now(HKT).strftime("%Y%m%d")  # ✅ 全域 TODAY

# 引入 AI 工具
from utils.ai_screening_utils import run_ai_screening

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
    create_international_news_report
)

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

# 🔥 恢復進度函數
def restore_progress(stage):
    """一鍵恢復指定階段的進度"""
    if stage == "ui_sorting":
        st.session_state.intl_sorted_dict = fb_logger.load_json_from_date_folder('user_final_list.json', {})
        st.session_state.intl_stage = "ui_sorting"
    elif stage == "finished":
        st.session_state.intl_final_articles = fb_logger.load_json_from_date_folder('full_scraped_articles.json', [])
        st.session_state.intl_final_docx = None  # 需要重新生成下載鏈接
        st.session_state.intl_stage = "finished"
    st.rerun()

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

def render_article_card(article, index, location, total_count):
    """Render a single article card with controls"""
    # 樣式定義
    card_style = """
        <style>
        .article-card {
            background-color: #f0f2f6;
            border-radius: 10px;
            padding: 15px;
            margin-bottom: 10px;
            border-left: 5px solid %s;
        }
        .article-meta {
            color: #666;
            font-size: 0.8em;
            margin-bottom: 5px;
        }
        .article-content {
            font-size: 0.9em;
            color: #333;
        }
        </style>
    """
    
    # Color coding based on score
    score = article.get('ai_analysis', {}).get('overall_score', 0)
    color = "#ff4b4b" if score >= 20 else "#ffa500" if score >= 10 else "#21c354"
    st.markdown(card_style % color, unsafe_allow_html=True)
    
    with st.container():
        # Title and Badge
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            st.markdown(f"**{index + 1}. {article['title']}**")
        with col2:
            st.caption(f"Score: {score}")
            
        # Metadata
        meta_text = article.get('metadata_line', 'No metadata')
        st.markdown(f"<div class='article-meta'>{meta_text}</div>", unsafe_allow_html=True)
        
        # Content Preview (Collapsible)
        with st.expander("查看摘要內容"):
            content = article.get('hover_text', 'No content')
            st.markdown(f"<div class='article-content'>{content}</div>", unsafe_allow_html=True)
            
        # Control Buttons
        c1, c2, c3, c4 = st.columns(4)
        
        # Unique key generation to avoid conflicts
        key_base = f"{location}_{index}_{article.get('original_index', 0)}"
        
        with c1:
            if index > 0:
                st.button("⬆️ 上移", key=f"up_{key_base}", 
                         on_click=move_article, args=(location, index, 'up'))
        with c2:
            if index < total_count - 1:
                st.button("⬇️ 下移", key=f"down_{key_base}", 
                         on_click=move_article, args=(location, index, 'down'))
        with c3:
            if index > 0:
                st.button("🔝 置頂", key=f"top_{key_base}",
                         on_click=move_to_top, args=(location, index))
        with c4:
            st.button("🗑️ 刪除", key=f"del_{key_base}", type="secondary",
                     on_click=delete_article, args=(location, index))
        
        st.markdown("---")

# === 主流程函數 ===

def _handle_international_news_logic(
    groupname_intl,
    username_intl,
    password_intl,
    apikey_intl,
    run_headless_intl,
    keep_browser_open_intl,
    max_words,
    min_words,
):
    """
    Revised flow with Firebase persistence:
    0%  -> smarthome
    25% -> preview_articles.json (含悬浮预览 + AI 结果)
    50% -> user_final_list.json
    100% -> full_scraped_articles.json + finalreport.docx
    """

    fb_logger = st.session_state.get("fb_logger")  # 已在文件顶部初始化过 [file:2]
    progress = check_today_progress()              # 使用你现有的进度函数 [file:2]

    LOCATION_ORDER = [
        "United States", "Russia", "Europe", "Middle East",
        "Southeast Asia", "Japan", "Korea", "China",
        "Others", "Tech News",
    ]

    # ---------- Smart Home：显示今日进度 ----------
    if "intl_stage" not in st.session_state:
        st.session_state.intl_stage = "smarthome"

    if st.session_state.intl_stage == "smarthome":
        st.header("🌏 International News")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("预览数量", f"{progress['preview_count']}" if progress["preview"] else "0")
        with col2:
            st.metric("用户筛选后数量", f"{progress['user_list_count']}" if progress["user_list"] else "0")
        with col3:
            st.metric("全文抓取数量", f"{len(fb_logger.load_json_from_date_folder('full_scraped_articles.json', []))}"
                      if progress["final_articles"] else "0")

        st.divider()

        # 依据进度提供一键恢复入口
        if progress["final_articles"]:
            st.success("✅ 今日已完成 100%（全文与 Word 报告存在）")
            if st.button("恢复到 100% 阶段（下载报告）", type="primary", use_container_width=True):
                restore_progress("finished")
        elif progress["user_list"]:
            st.warning("🔶 已完成 50%：有用户筛选结果，可以继续做全文爬取。")
            if st.button("恢复到 50% 阶段（UI 排序完成）", type="primary", use_container_width=True):
                restore_progress("ui_sorting")
        elif progress["preview"]:
            st.info("🟦 已完成 25%：有预览与 AI 打分记录，可以继续做 UI 排序。")
            if st.button("恢复到 25% 阶段（只做过 AI 预览）", type="secondary", use_container_width=True):
                st.session_state.intl_articles_list = fb_logger.load_json_from_date_folder(
                    "preview_articles.json", []
                )
                st.session_state.intl_stage = "init"
                st.rerun()
        else:
            st.success("🆕 今日尚未开始，可以从 0% 开始执行。")
            if st.button("从 0% 开始（AI 预览）", type="primary", use_container_width=True):
                st.session_state.intl_stage = "init"
                st.rerun()

        st.divider()
        return  # smarthome 阶段到此结束

    # ---------- Stage 1：登录 + 搜索 + 悬浮预览 + AI ----------
    if st.session_state.intl_stage == "init":
        st.header("Stage 1 – 搜索 + 悬浮预览 + AI 评分")

        if st.button("▶️ 一键执行（生成预览 + 悬浮摘要 + AI 评分）",
                     type="primary", use_container_width=True):
            try:
                with st.spinner("登录 Wisers、执行搜索并生成预览…"):
                    driver = setup_webdriver(headless=run_headless_intl, st_module=st)
                    if not driver:
                        st.stop()

                    wait = WebDriverWait(driver, 20)
                    perform_login(
                        driver=driver,
                        wait=wait,
                        groupname=groupname_intl,
                        username=username_intl,
                        password=password_intl,
                        apikey=apikey_intl,
                        st_module=st,
                    )

                    switch_language_to_traditional_chinese(
                        driver=driver, wait=wait, st_module=st
                    )

                    # 1) 搜索并生成初步结果（标题、链接等）
                    run_international_news_task(driver=driver, wait=wait, st_module=st)

                    # 2) 爬取悬浮预览内容
                    raw_list = scrape_hover_popovers(driver=driver, wait=wait, st_module=st)

                    st.info("🔁 结束浏览器 session，准备进行 AI 分析…")
                    try:
                        robust_logout_request(driver, st)
                    except Exception as e:
                        st.warning(f"注销时出现问题：{e}")
                    driver.quit()

                # 3) 给每条记录补上 original_index，并做 AI 评分
                filtered_list = []
                for i, item in enumerate(raw_list):
                    item["original_index"] = i
                    filtered_list.append(item)

                with st.spinner(f"🤖 AI 评分中，共 {len(filtered_list)} 条…"):
                    analyzed_list = run_ai_screening(
                        filtered_list,
                        progress_callback=lambda i, n, t: st.text(f"{i + 1}/{n} {t}")
                    )

                # ✅ 关键：现在才更新 session_state 并保存到 Firebase
                st.session_state.intl_articles_list = analyzed_list
                fb_logger.save_json_to_date_folder(
                    st.session_state.intl_articles_list,
                    "preview_articles.json",
                )

                # 4) 依地区分组，进入 UI 排序阶段
                grouped_data = {}
                for item in analyzed_list:
                    loc = item.get("ai_analysis", {}).get("main_location", "Others")
                    if item.get("ai_analysis", {}).get("is_tech_news", False):
                        loc = "Tech News"
                    if loc not in LOCATION_ORDER:
                        loc = "Others"
                    grouped_data.setdefault(loc, []).append(item)

                st.session_state.intl_sorted_dict = grouped_data
                st.session_state.intl_stage = "ui_sorting"
                st.rerun()

            except Exception as e:
                st.error(f"Stage 1 发生错误：{e}")
                st.code(traceback.format_exc())
                return

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
                    perform_login(driver=driver, wait=wait, group_name=groupname_intl, username=username_intl, password=password_intl, api_key=api_key_intl, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)
                    run_international_news_task(driver=driver, wait=wait, st_module=st)
                    
                    full_articles_data = scrape_specific_articles_by_indices(driver, wait, final_list, st_module=st)
                    
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

                    st.session_state.intl_stage = "finished"
                    robust_logout_request(driver, st)
                    driver.quit()
                    st.rerun()

                    # 在 final_scraping 階段，生成 DOCX 後新增：
                    fb_logger.save_final_docx_to_date_folder(full_articles_data, 'final_report.docx')

                    
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
            
            # ✅ 下載按鈕
            st.download_button(
                label="📥 下載最終 Word 報告",
                data=st.session_state.intl_final_docx,
                file_name=f"Intl_News_Report_{TODAY}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                use_container_width=True,
                help="包含今日最終排序的完整新聞報告"
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
            run_headless, keep_open, max_words, min_words
        )
    else:
        st.error("請提供完整的 Wisers 帳號密碼及 API Key 才能開始。")
