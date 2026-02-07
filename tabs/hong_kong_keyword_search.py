import streamlit as st
import tempfile
import traceback
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait

import pytz

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
from utils import international_news_utils as intl_utils
from utils.firebase_logging import ensure_logger
from utils.keyword_search_utils import (
    HK_KEYWORD_DEFAULT,
    INTERNATIONAL_KEYWORD_DEFAULT,
    GREATER_CHINA_KEYWORD_DEFAULT,
    _get_credentials,
    _get_api_key,
    _get_keyword_presets,
    _render_keyword_controls,
    _run_keyword_preview_with_driver,
    run_web_scraping_pre_task,
    run_keyword_search_task,
    _apply_search_filters,
    _is_item_in_period,
)

# Reuse UI/state helpers from saved_search_news
from tabs.saved_search_news import (
    ensure_news_session_state,
    restore_progress,
    rollback_to_ui_sorting,
    build_grouped_data,
    render_article_card,
    _article_key_from_item,
    _article_key_from_scraped,
    _normalize_title,
    ensure_trimmed_docx_in_firebase_and_session,
)

HKT = pytz.timezone("Asia/Hong_Kong")

scrape_articles_by_news_id = intl_utils.scrape_articles_by_news_id
create_international_news_report = intl_utils.create_international_news_report


def _handle_keyword_search_news_logic(
    config,
    group_name,
    username,
    password,
    api_key,
    run_headless,
    keep_browser_open,
    max_words,
    min_words,
    max_articles,
    auto_start=False,
):
    prefix = config["prefix"]
    base_folder = config["base_folder"]
    category_label = config["category_label"]
    report_title = config["report_title"]

    today = datetime.now(HKT).strftime("%Y%m%d")
    fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context=config["tab_title"])
    ensure_news_session_state(fb_logger, prefix, category_label, base_folder)

    stage_key = f"{prefix}_stage"
    if stage_key not in st.session_state:
        st.session_state[stage_key] = "smart_home"

    if st.session_state.get(f"{prefix}_need_rerun", False):
        st.session_state[f"{prefix}_need_rerun"] = False
        st.rerun()

    if st.session_state[stage_key] == "smart_home":
        st.header(f"🧭 {config['header']} - 智能進度恢復")
        st.info(f"📁 Firebase: `{base_folder}/{today}/` | {datetime.now().strftime('%H:%M')}")
        _render_keyword_controls(prefix, config)

        def check_today_progress():
            preview_exists = bool(fb_logger.load_json_from_date_folder("preview_articles.json", [], base_folder=base_folder))
            user_list_exists = bool(fb_logger.load_json_from_date_folder("user_final_list.json", {}, base_folder=base_folder))
            final_articles_exists = bool(fb_logger.load_json_from_date_folder("full_scraped_articles.json", [], base_folder=base_folder))

            total_preview = len(fb_logger.load_json_from_date_folder("preview_articles.json", [], base_folder=base_folder))
            total_user_list = sum(len(v) for v in fb_logger.load_json_from_date_folder("user_final_list.json", {}, base_folder=base_folder).values())

            return {
                "preview": preview_exists,
                "user_list": user_list_exists,
                "final_articles": final_articles_exists,
                "preview_count": total_preview,
                "user_list_count": total_user_list,
            }

        progress = check_today_progress()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("📄 預覽文章", f"{progress['preview_count']} 篇", "✅" if progress["preview"] else "❌")
        with col2:
            st.metric("👤 用戶排序", f"{progress['user_list_count']} 篇", "✅" if progress["user_list"] else "❌")
        with col3:
            final_count = len(fb_logger.load_json_from_date_folder("full_scraped_articles.json", [], base_folder=base_folder))
            st.metric("✅ 最終全文", f"{final_count} 篇", "✅" if progress["final_articles"] else "❌")

        st.divider()

        if progress["final_articles"]:
            st.success("🎉 **今日任務已100%完成！立即下載最終報告**")
            col_download, col_rollback = st.columns([0.7, 0.3])
            with col_download:
                if st.button(
                    "📥 下載最終 Word 報告（100%進度）",
                    type="primary",
                    use_container_width=True,
                    key=f"{prefix}-smarthome-download-final",
                ):
                    restore_progress(fb_logger, prefix, "finished", base_folder, category_label)
            with col_rollback:
                if st.button(
                    "↩️ 回到50%调整排序",
                    type="secondary",
                    use_container_width=True,
                    key=f"{prefix}-smarthome-rollback",
                    on_click=rollback_to_ui_sorting,
                    args=(fb_logger, prefix, base_folder, category_label),
                ):
                    pass
        elif progress["user_list"]:
            st.warning("⏳ **今日已完成50%（用戶排序），繼續全文爬取**")
            if st.button(
                "👤 恢復排序界面繼續（50%進度）",
                type="primary",
                use_container_width=True,
                key=f"{prefix}-smarthome-resume-sort",
            ):
                restore_progress(fb_logger, prefix, "ui_sorting", base_folder, category_label)
        elif progress["preview"]:
            st.info(f"🧾 懸浮預覽已完成 ({progress['preview_count']} 篇文章)")
            if st.button(
                f"🎯 展示目前預覽進度 ({progress['preview_count']} 條)",
                type="secondary",
                use_container_width=True,
                key=f"{prefix}-smarthome-show-preview",
            ):
                preview_list = fb_logger.load_json_from_date_folder("preview_articles.json", [], base_folder=base_folder)
                st.session_state[f"{prefix}_articles_list"] = preview_list
                st.session_state[f"{prefix}_pool_dict"] = build_grouped_data(preview_list, category_label)
                st.session_state[f"{prefix}_sorted_dict"] = {category_label: []}
                fb_logger.save_json_to_date_folder(st.session_state[f"{prefix}_sorted_dict"], "user_final_list.json", base_folder=base_folder)
                st.success("✅ 已进入选择模式：默认未选择，点击『添加』加入已选清单。")
                st.session_state[stage_key] = "ui_sorting"
                st.rerun()
        else:
            st.success("🆕 **今日全新任務，開始抓取預覽**")
            if st.button(
                "🚀 開始新任務（0%進度）",
                type="primary",
                use_container_width=True,
                key=f"{prefix}-smarthome-start-new",
            ):
                st.session_state[stage_key] = "init"
                st.rerun()

        st.divider()

        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("🔄 忽略進度重來", type="secondary", key=f"{prefix}-smarthome-ignore"):
                for key in [
                    stage_key,
                    f"{prefix}_sorted_dict",
                    f"{prefix}_final_articles",
                    f"{prefix}_articles_list",
                    f"{prefix}_pool_dict",
                    f"{prefix}_final_docx",
                    f"{prefix}_final_docx_trimmed",
                ]:
                    if key in st.session_state:
                        del st.session_state[key]
                st.session_state[stage_key] = "init"
                st.rerun()
        with col_b:
            if st.button("📋 查看 JSON 數據", type="secondary", key=f"{prefix}-smarthome-view-json"):
                st.session_state[stage_key] = "data_viewer"
                st.rerun()

        return

    if st.session_state[stage_key] == "data_viewer":
        st.header("📋 JSON 數據檢視")
        if st.button("返回進度頁", key=f"{prefix}-data-viewer-back-top"):
            st.session_state[stage_key] = "smart_home"
            st.rerun()
        col1, col2, col3 = st.columns(3)
        with col1:
            st.json(fb_logger.load_json_from_date_folder("preview_articles.json", [], base_folder=base_folder))
        with col2:
            st.json(fb_logger.load_json_from_date_folder("user_final_list.json", {}, base_folder=base_folder))
        with col3:
            st.json(fb_logger.load_json_from_date_folder("full_scraped_articles.json", [], base_folder=base_folder))
        if st.button("返回進度頁", key=f"{prefix}-data-viewer-back-bottom"):
            st.session_state[stage_key] = "smart_home"
            st.rerun()
        return

    if st.session_state[stage_key] == "await_sort_confirm":
        st.header("✅ 預覽完成，等待確認")
        if st.session_state.get(f"{prefix}_batch_mode"):
            st.info("已完成預覽（連續搜索模式）。確認後再進入 50% 用戶排序界面。")
        else:
            st.info("已完成登出並釋放 Session。確認後再進入 50% 用戶排序界面。")
        col_left, col_right = st.columns([0.6, 0.4])
        with col_left:
            if st.button(
                "👤 進入用戶排序（50%進度）",
                type="primary",
                use_container_width=True,
                key=f"{prefix}-confirm-sort",
            ):
                if f"{prefix}_batch_mode" in st.session_state:
                    st.session_state[f"{prefix}_batch_mode"] = False
                st.session_state[stage_key] = "ui_sorting"
                st.rerun()
        with col_right:
            if st.button(
                "↩️ 返回進度頁",
                type="secondary",
                use_container_width=True,
                key=f"{prefix}-confirm-sort-back",
            ):
                st.session_state[stage_key] = "smart_home"
                st.rerun()
        return

    try:
        if st.session_state[stage_key] == "init":
            _render_keyword_controls(prefix, config)
            auto_start_key = f"{prefix}_auto_start"
            auto_start_now = bool(st.session_state.pop(auto_start_key, False)) or auto_start
            if auto_start_now or st.button("🚀 開始任務：抓取預覽", key=f"{prefix}-init-start"):
                with st.spinner("第一步：登錄 Wisers 並抓取預覽..."):
                    driver = setup_webdriver(headless=run_headless, st_module=st)
                    if not driver:
                        return

                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name, username=username, password=password, api_key=api_key, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)

                    run_web_scraping_pre_task(
                        driver=driver,
                        wait=wait,
                        st_module=st,
                        authors_list=config.get("web_scraping_authors"),
                        fb_logger=fb_logger,
                    )

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
                        start_from_results=False,
                    )

                    st.info("暫時登出以釋放 Session...")
                    try:
                        robust_logout_request(driver, st)
                    except Exception as e:
                        st.warning(f"登出時出現問題: {e}")
                    driver.quit()

                    grouped_data = build_grouped_data(preview_list, category_label)
                    st.session_state[f"{prefix}_articles_list"] = preview_list

                    fb_logger.save_json_to_date_folder(preview_list, "preview_articles.json", base_folder=base_folder)

                    st.session_state[f"{prefix}_pool_dict"] = grouped_data
                    st.session_state[f"{prefix}_sorted_dict"] = {category_label: []}
                    st.session_state[stage_key] = "await_sort_confirm"
                    st.info("✅ 預覽已完成並完成登出。請確認後進入 50% 用戶排序。")
                    return

        if st.session_state[stage_key] == "ui_sorting":
            st.header("📱 新聞排序與篩選")
            st.info(f"💾 自動保存至 Firebase: `{base_folder}/{today}/user_final_list.json`")

            col_g1, col_g2 = st.columns(2)
            with col_g1:
                if st.button("🔄 重新開始 (清除數據)", key=f"{prefix}-ui-reset"):
                    st.session_state[stage_key] = "init"
                    st.rerun()
            with col_g2:
                if st.button("💾 手動保存排序", key=f"{prefix}-ui-save"):
                    fb_logger.save_json_to_date_folder(st.session_state[f"{prefix}_sorted_dict"], "user_final_list.json", base_folder=base_folder)
                    st.success("✅ 已保存用戶排序清單！")

            st.write("---")

            total_articles = sum(len(v) for v in st.session_state[f"{prefix}_sorted_dict"].values())
            st.markdown(f"**總文章數: {total_articles}**")

            category_order = [category_label]
            for category in category_order:
                selected = st.session_state[f"{prefix}_sorted_dict"].get(category, [])
                pool_dict = st.session_state.get(f"{prefix}_pool_dict") or {}
                pool = pool_dict.get(category, [])

                if not selected and not pool:
                    continue

                with st.expander(f"{category}（已选 {len(selected)} / 候选 {len(pool)}）", expanded=True):
                    if selected:
                        st.caption("已选（可排序）")
                        for i, article in enumerate(selected):
                            render_article_card(prefix, article, i, category, len(selected), mode="selected")
                    else:
                        st.info("当前地区还没有已选文章。")

                    if pool:
                        st.caption("候选（点击添加）")
                        for j, article in enumerate(pool):
                            render_article_card(prefix, article, j, category, len(pool), mode="pool")

            st.write("---")
            if st.button(
                "✅ 確認排序並開始全文爬取",
                type="primary",
                use_container_width=True,
                key=f"{prefix}-ui-confirm",
            ):
                fb_logger.save_json_to_date_folder(st.session_state[f"{prefix}_sorted_dict"], "user_final_list.json", base_folder=base_folder)
                st.success("💾 用戶排序已自動保存至 Firebase")
                st.session_state[stage_key] = "final_scraping"
                st.rerun()

        if st.session_state[stage_key] == "final_scraping":
            st.header("⏳ 最終處理中...")

            final_list = []
            for category in [category_label]:
                if category in st.session_state[f"{prefix}_sorted_dict"]:
                    final_list.extend(st.session_state[f"{prefix}_sorted_dict"][category])

            if not final_list:
                st.warning("沒有文章被選中。")
                if st.button("返回", key=f"{prefix}-final-back"):
                    st.session_state[stage_key] = "ui_sorting"
                    st.rerun()
                return

            with st.spinner(f"正在爬取 {len(final_list)} 篇文章的全文內容..."):
                driver = None
                try:
                    driver = setup_webdriver(headless=run_headless, st_module=st)
                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name, username=username, password=password, api_key=api_key, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)

                    keyword_presets = _get_keyword_presets(prefix, config)
                    include_content = bool(st.session_state.get(f"{prefix}_include_content", False))
                    extra_presets = []
                    for item in final_list:
                        preset = item.get("keyword_preset")
                        if preset and preset not in keyword_presets and preset not in extra_presets:
                            extra_presets.append(preset)
                    if not keyword_presets:
                        keyword_presets = extra_presets[:] if extra_presets else [HK_KEYWORD_DEFAULT]
                    elif extra_presets:
                        keyword_presets = keyword_presets + extra_presets

                    default_keyword = keyword_presets[0] if keyword_presets else HK_KEYWORD_DEFAULT
                    items_by_preset = {}
                    for item in final_list:
                        preset = item.get("keyword_preset") or default_keyword
                        items_by_preset.setdefault(preset, []).append(item)

                    is_monday = is_hkt_monday()
                    per_period_max = max(1, max_articles // 2) if is_monday else max_articles
                    if is_monday:
                        full_articles_data = []
                        has_run_search = False
                        periods = [("today", None), ("yesterday", "周日")]

                        for period_name, _day_tag in periods:
                            if period_name != "today":
                                set_date_range_period(
                                    driver=driver, wait=wait, st_module=st, period_name=period_name
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
                                    st_module=st,
                                    keyword=keyword,
                                    include_content=include_content,
                                    use_edit_modal=use_edit_modal,
                                )
                                has_run_search = True
                                wait_for_results_panel_ready(driver=driver, wait=wait, st_module=st)
                                ensure_results_list_visible(driver=driver, wait=wait, st_module=st)
                                full_articles_data.extend(
                                    scrape_articles_by_news_id(driver, wait, period_items, st_module=st)
                                )
                    else:
                        full_articles_data = []
                        has_run_search = False
                        for preset_index, keyword in enumerate(keyword_presets):
                            preset_items = items_by_preset.get(keyword, [])
                            if not preset_items:
                                continue
                            use_edit_modal = has_run_search or (preset_index > 0)
                            run_keyword_search_task(
                                driver=driver,
                                wait=wait,
                                st_module=st,
                                keyword=keyword,
                                include_content=include_content,
                                use_edit_modal=use_edit_modal,
                            )
                            has_run_search = True
                            wait_for_results_panel_ready(driver=driver, wait=wait, st_module=st)
                            ensure_results_list_visible(driver=driver, wait=wait, st_module=st)
                            full_articles_data.extend(
                                scrape_articles_by_news_id(driver, wait, preset_items, st_module=st)
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
                        st.warning(f"⚠️ 第一輪最終爬取缺失 {len(missing_items)} 篇，開始二次搜索補爬...")
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
                                    logger=fb_logger,
                                )
                            else:
                                search_title_via_edit_search_modal(
                                    driver=driver,
                                    wait=wait,
                                    keyword=title,
                                    st_module=st,
                                    logger=fb_logger,
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
                    fb_logger.save_json_to_date_folder(full_articles_data, "full_scraped_articles.json", base_folder=base_folder)

                    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                        out_path = create_international_news_report(
                            articles_data=full_articles_data,
                            output_path=tmp.name,
                            st_module=st,
                            report_title=report_title,
                        )
                        with open(out_path, "rb") as f:
                            docx_bytes = f.read()

                    st.session_state[f"{prefix}_final_docx"] = docx_bytes
                    fb_logger.save_final_docx_bytes_to_date_folder(docx_bytes, "final_report.docx", base_folder=base_folder)
                    st.session_state[stage_key] = "finished"
                    st.rerun()

                except Exception as e:
                    st.error(f"❌ 最終爬取失敗: {e}")
                    st.code(traceback.format_exc())
                finally:
                    if driver:
                        try:
                            if not keep_browser_open:
                                driver.quit()
                        except Exception:
                            pass

        if st.session_state[stage_key] == "finished":
            st.header("✅ 任務完成")
            st.success("✅ 最終報告已生成並保存至 Firebase")
            ensure_trimmed_docx_in_firebase_and_session(fb_logger, prefix, base_folder)

            col1, col2 = st.columns(2)
            with col1:
                if st.session_state.get(f"{prefix}_final_docx"):
                    st.download_button(
                        "📥 下載最終報告（完整版）",
                        data=st.session_state[f"{prefix}_final_docx"],
                        file_name=f"{config['file_prefix']}_{today}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
            with col2:
                if st.session_state.get(f"{prefix}_final_docx_trimmed"):
                    st.download_button(
                        "📥 下載最終報告（摘要版）",
                        data=st.session_state[f"{prefix}_final_docx_trimmed"],
                        file_name=f"{config['file_prefix']}_{today}_trimmed.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
    except Exception as e:
        st.error(f"❌ 系统错误：{e}")
        st.code(traceback.format_exc())


def render_hong_kong_keyword_search_tab():
    st.subheader("🇭🇰 香港政治新聞（關鍵詞直搜）")

    config = {
        "tab_title": "香港政治新聞（關鍵詞直搜）",
        "header": "香港政治新聞（關鍵詞直搜）",
        "base_folder": "hong_kong_keyword_search",
        "report_title": "本地新聞摘要",
        "category_label": "本地新聞",
        "prefix": "hkkw",
        "file_prefix": "LocalNewsKeywordReport",
        "default_keyword_text": HK_KEYWORD_DEFAULT,
    }

    group_name, username, password, _bucket = _get_credentials(config["prefix"])
    api_key = _get_api_key(config["prefix"])

    col4, col5, col6 = st.columns(3)
    with col4:
        run_headless = st.checkbox("Headless 模式", value=True, key="hkkw-headless")
    with col5:
        keep_browser_open = st.checkbox("任务完成后保持浏览器打开", value=False, key="hkkw-keep-browser")
    with col6:
        max_articles = st.number_input("最多抓取篇数", min_value=10, max_value=120, value=60, step=10, key="hkkw-max-articles")

    st.divider()

    col7, col8 = st.columns(2)
    with col7:
        min_words = st.number_input("最少字数", min_value=0, max_value=5000, value=200, step=50, key="hkkw-min-words")
    with col8:
        max_words = st.number_input("最多字数", min_value=50, max_value=10000, value=1000, step=50, key="hkkw-max-words")

    _handle_keyword_search_news_logic(
        config=config,
        group_name=group_name,
        username=username,
        password=password,
        api_key=api_key,
        run_headless=run_headless,
        keep_browser_open=keep_browser_open,
        max_words=max_words,
        min_words=min_words,
        max_articles=max_articles,
    )


def render_international_keyword_search_tab():
    st.subheader("🌍 國際新聞（關鍵詞直搜）")

    config = {
        "tab_title": "國際新聞（關鍵詞直搜）",
        "header": "國際新聞（關鍵詞直搜）",
        "base_folder": "international_keyword_search",
        "report_title": "國際新聞摘要",
        "category_label": "國際新聞",
        "prefix": "intkw",
        "file_prefix": "InternationalKeywordReport",
        "default_keyword_text": INTERNATIONAL_KEYWORD_DEFAULT,
    }

    group_name, username, password, _bucket = _get_credentials(config["prefix"])
    api_key = _get_api_key(config["prefix"])

    col4, col5, col6 = st.columns(3)
    with col4:
        run_headless = st.checkbox("Headless 模式", value=True, key="intkw-headless")
    with col5:
        keep_browser_open = st.checkbox("任务完成后保持浏览器打开", value=False, key="intkw-keep-browser")
    with col6:
        max_articles = st.number_input("最多抓取篇数", min_value=10, max_value=120, value=60, step=10, key="intkw-max-articles")

    st.divider()

    col7, col8 = st.columns(2)
    with col7:
        min_words = st.number_input("最少字数", min_value=0, max_value=5000, value=200, step=50, key="intkw-min-words")
    with col8:
        max_words = st.number_input("最多字数", min_value=50, max_value=10000, value=1000, step=50, key="intkw-max-words")

    _handle_keyword_search_news_logic(
        config=config,
        group_name=group_name,
        username=username,
        password=password,
        api_key=api_key,
        run_headless=run_headless,
        keep_browser_open=keep_browser_open,
        max_words=max_words,
        min_words=min_words,
        max_articles=max_articles,
    )


def render_greater_china_keyword_search_tab():
    st.subheader("🀄 大中華新聞（關鍵詞直搜）")

    config = {
        "tab_title": "大中華新聞（關鍵詞直搜）",
        "header": "大中華新聞（關鍵詞直搜）",
        "base_folder": "greater_china_keyword_search",
        "report_title": "大中華新聞摘要",
        "category_label": "大中華新聞",
        "prefix": "gckw",
        "file_prefix": "GreaterChinaKeywordReport",
        "default_keyword_text": GREATER_CHINA_KEYWORD_DEFAULT,
    }

    group_name, username, password, _bucket = _get_credentials(config["prefix"])
    api_key = _get_api_key(config["prefix"])

    col4, col5, col6 = st.columns(3)
    with col4:
        run_headless = st.checkbox("Headless 模式", value=True, key="gckw-headless")
    with col5:
        keep_browser_open = st.checkbox("任务完成后保持浏览器打开", value=False, key="gckw-keep-browser")
    with col6:
        max_articles = st.number_input("最多抓取篇数", min_value=10, max_value=120, value=60, step=10, key="gckw-max-articles")

    st.divider()

    col7, col8 = st.columns(2)
    with col7:
        min_words = st.number_input("最少字数", min_value=0, max_value=5000, value=200, step=50, key="gckw-min-words")
    with col8:
        max_words = st.number_input("最多字数", min_value=50, max_value=10000, value=1000, step=50, key="gckw-max-words")

    _handle_keyword_search_news_logic(
        config=config,
        group_name=group_name,
        username=username,
        password=password,
        api_key=api_key,
        run_headless=run_headless,
        keep_browser_open=keep_browser_open,
        max_words=max_words,
        min_words=min_words,
        max_articles=max_articles,
    )

