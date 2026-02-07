import streamlit as st
import tempfile
from selenium.webdriver.support.ui import WebDriverWait

from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    switch_language_to_traditional_chinese,
    robust_logout_request,
    set_date_range_period,
    is_hkt_monday,
    go_back_to_search_form,
    wait_for_search_results,
    ensure_results_list_visible,
    wait_for_results_panel_ready,
    search_title_from_home,
    search_title_via_edit_search_modal,
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
    run_keyword_search_task,
    _apply_search_filters,
    _is_item_in_period,
)
from utils import international_news_utils as intl_utils
from tabs.saved_search_news import (
    ensure_news_session_state,
    build_grouped_data,
    _article_key_from_item,
    _article_key_from_scraped,
    _normalize_title,
    trim_docx_bytes_with_userlist,
)

scrape_articles_by_news_id = intl_utils.scrape_articles_by_news_id
create_international_news_report = intl_utils.create_international_news_report


def _load_user_final_list(fb_logger, base_folder: str) -> dict:
    data = fb_logger.load_json_from_date_folder("user_final_list.json", {}, base_folder=base_folder)
    return data if isinstance(data, dict) else {}


def _flatten_user_final_list(user_final_list: dict) -> list:
    final_list = []
    if not isinstance(user_final_list, dict):
        return final_list
    for _, items in user_final_list.items():
        if isinstance(items, list):
            final_list.extend(items)
    return final_list


def _run_keyword_final_with_driver(
    driver,
    wait,
    st_module,
    config,
    final_list: list,
    max_articles: int,
    start_from_results: bool,
):
    prefix = config["prefix"]
    keyword_presets = _get_keyword_presets(prefix, config)
    include_content = bool(st.session_state.get(f"{prefix}_include_content", False))

    extra_presets = []
    for item in final_list:
        preset = item.get("keyword_preset")
        if preset and preset not in keyword_presets and preset not in extra_presets:
            extra_presets.append(preset)
    if not keyword_presets:
        default_keyword = (config.get("default_keyword_text") or HK_KEYWORD_DEFAULT).strip()
        keyword_presets = extra_presets[:] if extra_presets else [default_keyword]
    elif extra_presets:
        keyword_presets = keyword_presets + extra_presets

    default_keyword = keyword_presets[0] if keyword_presets else HK_KEYWORD_DEFAULT
    items_by_preset = {}
    for item in final_list:
        preset = item.get("keyword_preset") or default_keyword
        items_by_preset.setdefault(preset, []).append(item)

    is_monday = is_hkt_monday()
    per_period_max = max(1, max_articles // 2) if is_monday else max_articles
    full_articles_data = []

    if is_monday:
        periods = [("today", None), ("yesterday", "周日")]
        has_run_search = bool(start_from_results)
        for period_name, _day_tag in periods:
            if period_name != "today":
                set_date_range_period(
                    driver=driver, wait=wait, st_module=st_module, period_name=period_name
                )
            for preset_index, keyword in enumerate(keyword_presets):
                period_items = [
                    item
                    for item in items_by_preset.get(keyword, [])
                    if _is_item_in_period(item, period_name)
                ]
                if not period_items:
                    continue
                use_edit_modal = has_run_search or (period_name != "today") or (preset_index > 0)
                run_keyword_search_task(
                    driver=driver,
                    wait=wait,
                    st_module=st_module,
                    keyword=keyword,
                    include_content=include_content,
                    use_edit_modal=use_edit_modal,
                )
                has_run_search = True
                wait_for_results_panel_ready(driver=driver, wait=wait, st_module=st_module)
                ensure_results_list_visible(driver=driver, wait=wait, st_module=st_module)
                full_articles_data.extend(
                    scrape_articles_by_news_id(driver, wait, period_items, st_module=st_module)
                )
    else:
        has_run_search = bool(start_from_results)
        for preset_index, keyword in enumerate(keyword_presets):
            preset_items = items_by_preset.get(keyword, [])
            if not preset_items:
                continue
            use_edit_modal = has_run_search or (preset_index > 0)
            run_keyword_search_task(
                driver=driver,
                wait=wait,
                st_module=st_module,
                keyword=keyword,
                include_content=include_content,
                use_edit_modal=use_edit_modal,
            )
            has_run_search = True
            wait_for_results_panel_ready(driver=driver, wait=wait, st_module=st_module)
            ensure_results_list_visible(driver=driver, wait=wait, st_module=st_module)
            full_articles_data.extend(
                scrape_articles_by_news_id(driver, wait, preset_items, st_module=st_module)
            )

    scraped_keys = {_article_key_from_scraped(a) for a in (full_articles_data or [])}
    scraped_titles = {
        _normalize_title(a.get("title") or a.get("source_title") or "")
        for a in (full_articles_data or [])
    }
    missing_items = []
    for item in final_list:
        key = _article_key_from_item(item)
        if key in scraped_keys:
            continue
        title_norm = _normalize_title(item.get("title"))
        if title_norm and title_norm in scraped_titles:
            continue
        missing_items.append(item)

    if missing_items:
        st_module.warning(f"⚠️ 第一輪最終爬取缺失 {len(missing_items)} 篇，開始二次搜索補爬...")
        return full_articles_data, missing_items

    return full_articles_data, []


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

    if st.button("🚀 一鍵三板塊：最終爬取", type="secondary", use_container_width=True, key="multi-kw-final"):
        group_name, username, password, _bucket = _get_credentials("multi-kw-final")
        api_key = _get_api_key("multi-kw-final")

        with st.spinner("正在連續執行三個板塊的最終爬取..."):
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

            fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context="multi-keyword-final")

            did_logout = False
            try:
                start_from_results = False
                for config in configs:
                    prefix = config["prefix"]
                    base_folder = config["base_folder"]
                    category_label = config["category_label"]
                    ensure_news_session_state(fb_logger, prefix, category_label, base_folder)

                    user_final_list = _load_user_final_list(fb_logger, base_folder)
                    final_list = _flatten_user_final_list(user_final_list)
                    if not final_list:
                        st.warning(f"⚠️ {config['tab_title']} 尚未完成用戶排序，跳過最終爬取。")
                        start_from_results = True
                        continue

                    st.info(f"📌 {config['tab_title']}：開始最終爬取（{len(final_list)} 篇）")
                    full_articles_data, missing_items = _run_keyword_final_with_driver(
                        driver=driver,
                        wait=wait,
                        st_module=st,
                        config=config,
                        final_list=final_list,
                        max_articles=max_articles,
                        start_from_results=start_from_results,
                    )

                    if missing_items:
                        fb_logger.save_json_to_date_folder(
                            missing_items,
                            "missing_articles_round1.json",
                            base_folder=base_folder,
                        )

                        missing_round2 = []
                        try:
                            go_back_to_search_form(driver=driver, wait=wait, st_module=st)
                        except Exception:
                            pass

                        include_content = bool(st.session_state.get(f"{prefix}_include_content", False))
                        for idx, item in enumerate(missing_items):
                            title = item.get("title", "")
                            st.write(f"🔁 二次搜索 ({idx+1}/{len(missing_items)}): {title[:50]}...")

                            if idx == 0:
                                _apply_search_filters(driver, wait, st, include_content)
                                search_title_from_home(
                                    driver=driver,
                                    wait=wait,
                                    keyword=title,
                                    st_module=st,
                                )
                            else:
                                search_title_via_edit_search_modal(
                                    driver=driver,
                                    wait=wait,
                                    keyword=title,
                                    st_module=st,
                                )

                            has_results = wait_for_search_results(driver=driver, wait=wait, st_module=st)
                            if not has_results:
                                missing_round2.append(item)
                                continue
                            wait_for_results_panel_ready(driver=driver, wait=wait, st_module=st)
                            ensure_results_list_visible(driver=driver, wait=wait, st_module=st)

                            retry_scraped = scrape_articles_by_news_id(driver, wait, [item], st_module=st)
                            if retry_scraped:
                                full_articles_data.extend(retry_scraped)
                            else:
                                missing_round2.append(item)

                        if missing_round2:
                            st.warning(f"⚠️ 二次搜索仍缺失 {len(missing_round2)} 篇，已記錄清單。")
                            fb_logger.save_json_to_date_folder(
                                missing_round2,
                                "missing_articles_round2.json",
                                base_folder=base_folder,
                            )

                    st.session_state[f"{prefix}_final_articles"] = full_articles_data
                    fb_logger.save_json_to_date_folder(
                        full_articles_data, "full_scraped_articles.json", base_folder=base_folder
                    )

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                        out_path = create_international_news_report(
                            articles_data=full_articles_data,
                            output_path=tmp.name,
                            st_module=st,
                            report_title=config["report_title"],
                        )
                        with open(out_path, "rb") as f:
                            docx_bytes = f.read()

                    st.session_state[f"{prefix}_final_docx"] = docx_bytes
                    fb_logger.save_final_docx_bytes_to_date_folder(
                        docx_bytes, "final_report.docx", base_folder=base_folder
                    )

                    trimmed_bytes = trim_docx_bytes_with_userlist(docx_bytes, user_final_list, keep_body_paras=3)
                    fb_logger.save_final_docx_bytes_to_date_folder(
                        trimmed_bytes, "final_report_trimmed.docx", base_folder=base_folder
                    )
                    st.session_state[f"{prefix}_final_docx_trimmed"] = trimmed_bytes
                    st.session_state[f"{prefix}_stage"] = "finished"

                    start_from_results = True

                st.info("三個板塊最終爬取完成，正在登出...")
                try:
                    robust_logout_request(driver, st)
                except Exception as e:
                    st.warning(f"登出時出現問題: {e}")
                did_logout = True
                if not keep_browser_open:
                    driver.quit()

            finally:
                if driver and not keep_browser_open:
                    try:
                        if not did_logout:
                            robust_logout_request(driver, st)
                    except Exception:
                        pass
                    try:
                        driver.quit()
                    except Exception:
                        pass

        st.success("✅ 一鍵三板塊最終爬取完成。")
        st.rerun()
