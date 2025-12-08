import streamlit as st
import tempfile
import time
import traceback
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By

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

# === UI 輔助函數  ===

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
    group_name_intl, username_intl, password_intl, api_key_intl,
    run_headless_intl, keep_browser_open_intl, max_words, min_words
):
    """
    Revised flow with Mobile-First Drag & Drop UI
    """
    # 初始化 Session State
    if "intl_stage" not in st.session_state:
        st.session_state.intl_stage = "init" 
    if "intl_sorted_dict" not in st.session_state:
        st.session_state.intl_sorted_dict = {}
    if "intl_last_update" not in st.session_state:
        st.session_state.intl_last_update = 0

    # Locations Order
    LOCATION_ORDER = ['United States', 'Russia', 'Europe', 'Middle East', 
                      'Southeast Asia', 'Japan', 'Korea', 'China', 'Others', 'Tech News']

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
                    
                    run_international_news_task(driver=driver, wait=wait, st_module=st)
                    
                    # Scrape Popovers
                    raw_list = scrape_hover_popovers(driver=driver, wait=wait, st_module=st)
                    driver.quit() # 暫時關閉，節省資源
                    
                    # Filter
                    filtered_list = []
                    for i, item in enumerate(raw_list):
                        item['original_index'] = i
                        meta = item.get('hover_html', '') # 這裡簡化，直接用內容判斷或後續處理
                        # Note: 簡單過濾邏輯，這裡假設 scrape_hover_popovers 已經盡力抓了
                        filtered_list.append(item)
                
                with st.spinner(f"第二步：AI 正在分析 {len(filtered_list)} 篇文章..."):
                    # AI Analysis
                    analyzed_list = run_ai_screening(
                        filtered_list,
                        progress_callback=lambda i, n, t: st.text(f"分析中 ({i+1}/{n}): {t}...")
                    )
                    
                    # Group by Location for UI
                    grouped_data = {loc: [] for loc in LOCATION_ORDER}
                    for item in analyzed_list:
                        loc = item.get('ai_analysis', {}).get('main_location', 'Others')
                        # Tech news override
                        if item.get('ai_analysis', {}).get('is_tech_news', False):
                            loc = 'Tech News'
                        
                        # Normalize to known keys
                        if loc not in grouped_data: loc = 'Others'
                        
                        grouped_data[loc].append(item)
                    
                    st.session_state.intl_sorted_dict = grouped_data
                    st.session_state.intl_stage = "ui_sorting"
                    st.rerun()

        # === Stage 2: UI Sorting (Mobile Friendly) ===
        if st.session_state.intl_stage == "ui_sorting":
            st.header("📱 新聞排序與篩選")
            st.info("請點擊分類標籤展開，使用按鈕調整順序。完成後點擊底部的確認按鈕。")
            
            # Global Actions
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                if st.button("🔄 重新開始 (清除數據)"):
                    st.session_state.intl_stage = "init"
                    st.rerun()
            
            st.write("---")
            
            # Calculate counts
            total_articles = sum(len(v) for v in st.session_state.intl_sorted_dict.values())
            st.markdown(f"**總文章數: {total_articles}**")

            # Render Categories
            for location in LOCATION_ORDER:
                articles = st.session_state.intl_sorted_dict.get(location, [])
                if not articles:
                    continue
                
                with st.expander(f"{location} ({len(articles)})", expanded=True):
                    for i, article in enumerate(articles):
                        render_article_card(article, i, location, len(articles))

            st.write("---")
            
            # Final Confirm Button
            if st.button("✅ 確認排序並開始全文爬取", type="primary", use_container_width=True):
                st.session_state.intl_stage = "final_scraping"
                st.rerun()

        # === Stage 3: Final Scrape & Download ===
        if st.session_state.intl_stage == "final_scraping":
            st.header("⏳ 最終處理中...")
            
            # Flatten the dictionary back to a list based on UI order
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

            # Execute Scrape
            with st.spinner(f"正在爬取 {len(final_list)} 篇文章的全文內容..."):
                try:
                    driver = setup_webdriver(headless=run_headless_intl, st_module=st)
                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name_intl, username=username_intl, password=password_intl, api_key=api_key_intl, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)
                    run_international_news_task(driver=driver, wait=wait, st_module=st)
                    
                    # Scrape specific articles
                    full_articles_data = scrape_specific_articles_by_indices(driver, wait, final_list, st_module=st)
                    
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
                    st.session_state.intl_stage = "finished"
                    
                    robust_logout_request(driver, st)
                    driver.quit()
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"爬取失敗: {e}")
                    if st.button("重試"):
                        st.rerun()

        # === Stage 4: Download ===
        if st.session_state.intl_stage == "finished":
            st.success("🎉 任務全部完成！")
            
            st.download_button(
                label="📥 下載最終 Word 報告",
                data=st.session_state.intl_final_docx,
                file_name=f"Intl_News_Report_{datetime.now().strftime('%Y%m%d')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                use_container_width=True
            )
            
            if st.button("🔄 開始新任務"):
                st.session_state.intl_stage = "init"
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
