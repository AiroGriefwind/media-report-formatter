import streamlit as st
from selenium.webdriver.support.ui import WebDriverWait

from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    switch_language_to_traditional_chinese,
    robust_logout_request,
)
from utils.firebase_logging import ensure_logger
from utils.keyword_search_utils import (
    HK_KEYWORD_DEFAULT,
    INTERNATIONAL_KEYWORD_DEFAULT,
    GREATER_CHINA_KEYWORD_DEFAULT,
    _get_credentials,
    _get_api_key,
    _get_keyword_presets,
    _run_keyword_preview_with_driver,
)
from tabs.saved_search_news import ensure_news_session_state, build_grouped_data


def render_multi_keyword_search_tab():
    st.subheader("🚦 一鍵三板塊（關鍵詞直搜）")
    st.caption("按下後會依序執行：香港政治 ➜ 國際新聞 ➜ 大中華新聞（關鍵詞直搜）")

    configs = [
        {
            "tab_title": "香港政治新聞（關鍵詞直搜）",
            "header": "香港政治新聞（關鍵詞直搜）",
            "base_folder": "hong_kong_keyword_search",
            "report_title": "本地新聞摘要",
            "category_label": "本地新聞",
            "prefix": "hkkw",
            "file_prefix": "LocalNewsKeywordReport",
            "default_keyword_text": HK_KEYWORD_DEFAULT,
        },
        {
            "tab_title": "國際新聞（關鍵詞直搜）",
            "header": "國際新聞（關鍵詞直搜）",
            "base_folder": "international_keyword_search",
            "report_title": "國際新聞摘要",
            "category_label": "國際新聞",
            "prefix": "intkw",
            "file_prefix": "InternationalKeywordReport",
            "default_keyword_text": INTERNATIONAL_KEYWORD_DEFAULT,
        },
        {
            "tab_title": "大中華新聞（關鍵詞直搜）",
            "header": "大中華新聞（關鍵詞直搜）",
            "base_folder": "greater_china_keyword_search",
            "report_title": "大中華新聞摘要",
            "category_label": "大中華新聞",
            "prefix": "gckw",
            "file_prefix": "GreaterChinaKeywordReport",
            "default_keyword_text": GREATER_CHINA_KEYWORD_DEFAULT,
        },
    ]

    col4, col5, col6 = st.columns(3)
    with col4:
        run_headless = st.checkbox("Headless 模式", value=True, key="multi-kw-headless")
    with col5:
        keep_browser_open = st.checkbox("任务完成后保持浏览器打开", value=False, key="multi-kw-keep-browser")
    with col6:
        max_articles = st.number_input(
            "最多抓取篇数",
            min_value=10,
            max_value=120,
            value=60,
            step=10,
            key="multi-kw-max-articles",
        )

    st.divider()

    col7, col8 = st.columns(2)
    with col7:
        min_words = st.number_input(
            "最少字数",
            min_value=0,
            max_value=5000,
            value=200,
            step=50,
            key="multi-kw-min-words",
        )
    with col8:
        max_words = st.number_input(
            "最多字数",
            min_value=50,
            max_value=10000,
            value=1000,
            step=50,
            key="multi-kw-max-words",
        )

    if st.button("🚀 一鍵三板塊：抓取預覽", type="primary", use_container_width=True, key="multi-kw-start"):
        group_name, username, password, _bucket = _get_credentials("multi-kw")
        api_key = _get_api_key("multi-kw")

        with st.spinner("正在連續執行三個板塊的懸浮爬取..."):
            driver = setup_webdriver(headless=run_headless, st_module=st)
            if not driver:
                st.error("瀏覽器啟動失敗，請重試。")
                return

            wait = WebDriverWait(driver, 20)
            perform_login(
                driver=driver,
                wait=wait,
                group_name=group_name,
                username=username,
                password=password,
                api_key=api_key,
                st_module=st,
            )
            switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)

            fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context="multi-keyword-search")

            for index, config in enumerate(configs):
                prefix = config["prefix"]
                base_folder = config["base_folder"]
                category_label = config["category_label"]
                ensure_news_session_state(fb_logger, prefix, category_label, base_folder)

                keyword_presets = _get_keyword_presets(prefix, config)
                include_content = bool(st.session_state.get(f"{prefix}_include_content", False))
                preview_list = _run_keyword_preview_with_driver(
                    driver=driver,
                    wait=wait,
                    st_module=st,
                    keyword_presets=keyword_presets,
                    include_content=include_content,
                    max_words=max_words,
                    min_words=min_words,
                    max_articles=max_articles,
                    start_from_results=index > 0,
                )

                grouped_data = build_grouped_data(preview_list, category_label)
                st.session_state[f"{prefix}_articles_list"] = preview_list
                st.session_state[f"{prefix}_pool_dict"] = grouped_data
                st.session_state[f"{prefix}_sorted_dict"] = {category_label: []}
                st.session_state[f"{prefix}_stage"] = "await_sort_confirm"
                st.session_state[f"{prefix}_batch_mode"] = True

                fb_logger.save_json_to_date_folder(preview_list, "preview_articles.json", base_folder=base_folder)
                fb_logger.save_json_to_date_folder(
                    st.session_state[f"{prefix}_sorted_dict"],
                    "user_final_list.json",
                    base_folder=base_folder,
                )

            st.info("三個板塊懸浮預覽完成，正在登出...")
            try:
                robust_logout_request(driver, st)
            except Exception as e:
                st.warning(f"登出時出現問題: {e}")
            driver.quit()

        st.success("✅ 三個板塊已完成連續搜索的懸浮爬取。")
        st.rerun()

    st.divider()
    st.info("完成後請切換到各板塊單獨 tab 進行排序與全文爬取。")
