import streamlit as st
import tempfile
import time
import traceback
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait

import pytz
import re
import json
import os

HKT = pytz.timezone("Asia/Hong_Kong")

from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    switch_language_to_traditional_chinese,
    robust_logout_request,
)
from utils.web_scraping_utils import scrape_hover_popovers
from utils import international_news_utils as intl_utils

parse_metadata = intl_utils.parse_metadata
scrape_articles_by_news_id = intl_utils.scrape_articles_by_news_id
extract_news_id_from_html = intl_utils.extract_news_id_from_html
create_international_news_report = intl_utils.create_international_news_report

if hasattr(intl_utils, "run_saved_search_task"):
    run_saved_search_task = intl_utils.run_saved_search_task
else:
    def run_saved_search_task(**_kwargs):
        raise RuntimeError("Missing run_saved_search_task in utils.international_news_utils. Please update that module.")
from utils.intl_trim_utils import trim_docx
from utils.firebase_logging import ensure_logger


NEWS_TABS = {
    "greater_china": {
        "tab_title": "大中華關鍵詞",
        "header": "大中華關鍵詞",
        "saved_search_name": "大中華關鍵詞",
        "base_folder": "greater_china_news",
        "report_title": "大中華新聞摘要",
        "category_label": "大中華新聞",
        "prefix": "gc",
        "file_prefix": "GreaterChinaNewsReport",
    },
    "hong_kong_politics": {
        "tab_title": "香港政治新聞",
        "header": "香港政治新聞",
        "saved_search_name": "香港政治要闻",
        "base_folder": "local_news",
        "report_title": "本地新聞摘要",
        "category_label": "本地新聞",
        "prefix": "hkpol",
        "file_prefix": "LocalNewsReport",
    },
}


def article_uid(article: dict) -> str:
    """Stable uid for cross-rerun button keys and de-dup."""
    return (
        article.get("news_id")
        or article.get("newsid")
        or article.get("url")
        or str(article.get("original_index", "na"))
    )


def build_grouped_data(article_list: list, category_label: str) -> dict:
    if not article_list:
        return {category_label: []}
    return {category_label: list(article_list)}


def rebuild_pool_from_preview(preview_list: list, selected_dict: dict, category_label: str) -> dict:
    pool = build_grouped_data(preview_list, category_label)
    selected_uids = set()
    for _, items in (selected_dict or {}).items():
        for a in items:
            selected_uids.add(article_uid(a))
    pool[category_label] = [a for a in pool[category_label] if article_uid(a) not in selected_uids]
    return pool


def ensure_news_session_state(fb_logger, prefix: str, category_label: str, base_folder: str):
    articles_key = f"{prefix}_articles_list"
    sorted_key = f"{prefix}_sorted_dict"
    pool_key = f"{prefix}_pool_dict"

    if articles_key not in st.session_state:
        st.session_state[articles_key] = fb_logger.load_json_from_date_folder(
            "preview_articles.json", [], base_folder=base_folder
        )

    if sorted_key not in st.session_state:
        st.session_state[sorted_key] = fb_logger.load_json_from_date_folder(
            "user_final_list.json", {}, base_folder=base_folder
        )

    if not isinstance(st.session_state[sorted_key], dict):
        st.session_state[sorted_key] = {category_label: []}
    else:
        st.session_state[sorted_key].setdefault(category_label, [])

    pool = st.session_state.get(pool_key)
    if not isinstance(pool, dict):
        if st.session_state.get(articles_key):
            st.session_state[pool_key] = rebuild_pool_from_preview(
                preview_list=st.session_state[articles_key],
                selected_dict=st.session_state[sorted_key],
                category_label=category_label,
            )
        else:
            st.session_state[pool_key] = {category_label: []}
    else:
        st.session_state[pool_key].setdefault(category_label, [])


def restore_progress(fb_logger, prefix: str, stage: str, base_folder: str, category_label: str, should_rerun=True):
    if stage == "ui_sorting":
        st.session_state[f"{prefix}_sorted_dict"] = fb_logger.load_json_from_date_folder(
            "user_final_list.json", {}, base_folder=base_folder
        )
        st.session_state[f"{prefix}_stage"] = "ui_sorting"

        preview_list = fb_logger.load_json_from_date_folder(
            "preview_articles.json", [], base_folder=base_folder
        )
        st.session_state[f"{prefix}_pool_dict"] = rebuild_pool_from_preview(
            preview_list=preview_list,
            selected_dict=st.session_state.get(f"{prefix}_sorted_dict", {}),
            category_label=category_label,
        )

    elif stage == "finished":
        st.session_state[f"{prefix}_final_articles"] = fb_logger.load_json_from_date_folder(
            "full_scraped_articles.json", [], base_folder=base_folder
        )
        st.session_state[f"{prefix}_final_docx"] = None
        st.session_state[f"{prefix}_stage"] = "finished"

    if should_rerun:
        st.rerun()


def rollback_to_ui_sorting(fb_logger, prefix: str, base_folder: str, category_label: str):
    for k in [f"{prefix}_final_articles", f"{prefix}_final_docx", f"{prefix}_final_docx_trimmed"]:
        if k in st.session_state:
            st.session_state.pop(k, None)
    restore_progress(fb_logger, prefix, "ui_sorting", base_folder, category_label, should_rerun=False)
    st.session_state[f"{prefix}_need_rerun"] = True


def move_article(prefix: str, category_label: str, index: int, direction: str):
    articles = st.session_state[f"{prefix}_sorted_dict"][category_label]
    if direction == "up" and index > 0:
        articles[index], articles[index - 1] = articles[index - 1], articles[index]
    elif direction == "down" and index < len(articles) - 1:
        articles[index], articles[index + 1] = articles[index + 1], articles[index]
    st.session_state[f"{prefix}_last_update"] = time.time()


def delete_article(prefix: str, category_label: str, index: int):
    st.session_state[f"{prefix}_sorted_dict"][category_label].pop(index)
    st.session_state[f"{prefix}_last_update"] = time.time()


def move_to_top(prefix: str, category_label: str, index: int):
    articles = st.session_state[f"{prefix}_sorted_dict"][category_label]
    if index > 0:
        article = articles.pop(index)
        articles.insert(0, article)
        st.session_state[f"{prefix}_last_update"] = time.time()


def _category_choices(prefix: str, category_label: str) -> list:
    base = [category_label]
    keys = set()
    keys.update((st.session_state.get(f"{prefix}_sorted_dict") or {}).keys())
    keys.update((st.session_state.get(f"{prefix}_pool_dict") or {}).keys())
    extras = [k for k in sorted(keys) if k not in base]
    return base + extras


def move_selected_to_category(prefix: str, from_category: str, selected_index: int, to_category: str):
    if not to_category or to_category == from_category:
        return

    src = st.session_state.get(f"{prefix}_sorted_dict") or {}
    if from_category not in src:
        return
    if selected_index < 0 or selected_index >= len(src[from_category]):
        return

    article = src[from_category].pop(selected_index)
    uid = article_uid(article)
    src.setdefault(to_category, [])
    src[to_category] = [a for a in src[to_category] if article_uid(a) != uid]
    src[to_category].append(article)

    st.session_state[f"{prefix}_sorted_dict"] = src
    st.session_state[f"{prefix}_last_update"] = time.time()


def _on_change_move_selected(widget_key: str, prefix: str, from_category: str, selected_index: int):
    to_category = st.session_state.get(widget_key)
    move_selected_to_category(prefix, from_category, selected_index, to_category)


def _set_multi_newspapers(prefix: str, category_label: str, selected_index: int, enabled: bool):
    src = st.session_state.get(f"{prefix}_sorted_dict") or {}
    if category_label not in src:
        return
    if selected_index < 0 or selected_index >= len(src[category_label]):
        return
    src[category_label][selected_index]["multi_newspapers"] = bool(enabled)
    st.session_state[f"{prefix}_sorted_dict"] = src
    st.session_state[f"{prefix}_last_update"] = time.time()


def _on_change_multi_newspapers(widget_key: str, prefix: str, category_label: str, selected_index: int):
    enabled = bool(st.session_state.get(widget_key, False))
    _set_multi_newspapers(prefix, category_label, selected_index, enabled)


def add_to_selected(prefix: str, category_label: str, pool_index: int):
    article = st.session_state[f"{prefix}_pool_dict"][category_label].pop(pool_index)
    st.session_state[f"{prefix}_sorted_dict"].setdefault(category_label, []).append(article)
    st.session_state[f"{prefix}_last_update"] = time.time()


def remove_to_pool(prefix: str, category_label: str, selected_index: int):
    article = st.session_state[f"{prefix}_sorted_dict"][category_label].pop(selected_index)
    st.session_state[f"{prefix}_pool_dict"].setdefault(category_label, []).append(article)
    st.session_state[f"{prefix}_last_update"] = time.time()


def render_article_card(prefix: str, article: dict, index: int, category_label: str, total_count: int, mode: str):
    score = article.get("ai_analysis", {}).get("overall_score") if isinstance(article, dict) else None
    if isinstance(score, (int, float)):
        color = "#ff4b4b" if score >= 20 else "#ffa500" if score >= 10 else "#21c354"
        score_text = f"{score}"
    else:
        color = "#64748b"
        score_text = "-"

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
    keybase = f"{prefix}-{category_label}-{uid}-{mode}"

    with st.container():
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            prefix_text = f"{index + 1}. " if mode == "selected" else ""
            st.markdown(f"**{prefix_text}{article.get('title', '(no title)')}**")
        with col2:
            st.caption(f"Score: {score_text}")

        meta_text = article.get("formatted_metadata") or "No metadata"
        st.markdown(f"<div class='article-meta'>{meta_text}</div>", unsafe_allow_html=True)

        with st.expander("查看摘要內容"):
            content = article.get("hover_text", "No content")
            st.markdown(content)

        if mode == "selected":
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                if index > 0:
                    st.button("↑", key=f"up-{keybase}", on_click=move_article, args=(prefix, category_label, index, "up"))
            with c2:
                if index < total_count - 1:
                    st.button("↓", key=f"down-{keybase}", on_click=move_article, args=(prefix, category_label, index, "down"))
            with c3:
                if index > 0:
                    st.button("置顶", key=f"top-{keybase}", on_click=move_to_top, args=(prefix, category_label, index))
            with c4:
                choices = _category_choices(prefix, category_label)
                move_key = f"move-to-{keybase}"
                multi_key = f"multi-news-{keybase}"

                if hasattr(st, "popover"):
                    with st.popover("调整"):
                        st.checkbox(
                            "及多份报章",
                            value=bool(article.get("multi_newspapers", False)),
                            key=multi_key,
                            on_change=_on_change_multi_newspapers,
                            args=(multi_key, prefix, category_label, index),
                        )
                        if len(choices) > 1:
                            current_idx = choices.index(category_label) if category_label in choices else 0
                            st.selectbox(
                                "移动到分区",
                                options=choices,
                                index=current_idx,
                                key=move_key,
                                on_change=_on_change_move_selected,
                                args=(move_key, prefix, category_label, index),
                            )
                else:
                    toggle_key = f"show-adj-{keybase}"
                    if st.button("调整", key=f"adj-btn-{keybase}"):
                        st.session_state[toggle_key] = not st.session_state.get(toggle_key, False)
                    if st.session_state.get(toggle_key):
                        st.checkbox(
                            "及多份报章",
                            value=bool(article.get("multi_newspapers", False)),
                            key=multi_key,
                            on_change=_on_change_multi_newspapers,
                            args=(multi_key, prefix, category_label, index),
                        )
                        if len(choices) > 1:
                            current_idx = choices.index(category_label) if category_label in choices else 0
                            st.selectbox(
                                "移动到分区",
                                options=choices,
                                index=current_idx,
                                key=move_key,
                                on_change=_on_change_move_selected,
                                args=(move_key, prefix, category_label, index),
                            )
            with c5:
                st.button("删除", key=f"rm-{keybase}", type="secondary", on_click=remove_to_pool, args=(prefix, category_label, index))
        else:
            st.button("添加", key=f"add-{keybase}", type="primary", on_click=add_to_selected, args=(prefix, category_label, index))

    st.markdown("---")


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
            except Exception:
                pass


def ensure_trimmed_docx_in_firebase_and_session(fb_logger, prefix: str, base_folder: str):
    if st.session_state.get(f"{prefix}_final_docx_trimmed"):
        return

    trimmed_bytes = fb_logger.load_final_docx_from_date_folder("final_report_trimmed.docx", base_folder=base_folder)
    if trimmed_bytes:
        st.session_state[f"{prefix}_final_docx_trimmed"] = trimmed_bytes
        return

    base_docx = st.session_state.get(f"{prefix}_final_docx") or fb_logger.load_final_docx_from_date_folder(
        "final_report.docx", base_folder=base_folder
    )
    if not base_docx:
        raise RuntimeError("Cannot load final_report.docx from session or Firebase")

    user_final_list = fb_logger.load_json_from_date_folder("user_final_list.json", {}, base_folder=base_folder)
    if not user_final_list:
        raise RuntimeError("Cannot load user_final_list.json from Firebase")

    trimmed_bytes = trim_docx_bytes_with_userlist(base_docx, user_final_list, keep_body_paras=3)
    fb_logger.save_final_docx_bytes_to_date_folder(trimmed_bytes, "final_report_trimmed.docx", base_folder=base_folder)
    st.session_state[f"{prefix}_final_docx_trimmed"] = trimmed_bytes


def _handle_saved_search_news_logic(config, group_name, username, password, api_key, run_headless, keep_browser_open, max_words, min_words, max_articles):
    prefix = config["prefix"]
    base_folder = config["base_folder"]
    category_label = config["category_label"]
    saved_search_name = config["saved_search_name"]
    report_title = config["report_title"]

    today = datetime.now(HKT).strftime("%Y%m%d")

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

    fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context=config["saved_search_name"])
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

    try:
        if st.session_state[stage_key] == "init":
            if st.button("🚀 開始任務：抓取預覽", key=f"{prefix}-init-start"):
                with st.spinner("第一步：登錄 Wisers 並抓取預覽..."):
                    driver = setup_webdriver(headless=run_headless, st_module=st)
                    if not driver:
                        return

                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name, username=username, password=password, api_key=api_key, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)

                    _, search_meta = run_saved_search_task(
                        driver=driver,
                        wait=wait,
                        st_module=st,
                        max_articles=max_articles,
                        saved_search_name=saved_search_name,
                        return_meta=True,
                    )

                    rawlist = scrape_hover_popovers(driver=driver, wait=wait, st_module=st, max_articles=max_articles) or []
                    raw_count = len(rawlist)
                    if st:
                        st.info(f"✅ 抓取了 {raw_count} 篇懸停預覽")

                    st.info("暫時登出以釋放 Session...")
                    try:
                        robust_logout_request(driver, st)
                    except Exception as e:
                        st.warning(f"登出時出現問題: {e}")
                    driver.quit()

                    filtered_rawlist = []
                    for item in rawlist:
                        hover_text = item.get("hover_text", "")
                        word_matches = re.findall(r"(\\d+)\\s*字", hover_text)
                        if word_matches:
                            word_count = int(word_matches[0])
                            if min_words <= word_count <= max_words:
                                filtered_rawlist.append(item)
                            else:
                                if st:
                                    st.write(f"已過濾: {item.get('title', 'Unknown')} ({word_count} 字)")
                        else:
                            filtered_rawlist.append(item)

                    rawlist = filtered_rawlist
                    filtered_count = len(rawlist)
                    if st:
                        st.info(f"📊 字數過濾後剩餘: {filtered_count} 篇")

                    if not search_meta.get("saved_search_found", True):
                        st.error(f"❌ 未找到已保存搜索：{saved_search_name}")
                        return

                    if search_meta.get("no_results", False):
                        st.warning("⚠️ 已保存搜索有执行，但搜索结果为 0 篇。")
                    elif raw_count == 0:
                        st.warning("⚠️ 搜索有结果，但懸浮爬取為 0 篇。")
                    elif raw_count > 0 and filtered_count == 0:
                        st.warning("⚠️ 搜索有結果，但全部被字數過濾條件篩掉。")

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

                    grouped_data = build_grouped_data(preview_list, category_label)
                    st.session_state[f"{prefix}_articles_list"] = preview_list

                    fb_logger.save_json_to_date_folder(preview_list, "preview_articles.json", base_folder=base_folder)

                    st.session_state[f"{prefix}_pool_dict"] = grouped_data
                    st.session_state[f"{prefix}_sorted_dict"] = {category_label: []}
                    st.session_state[stage_key] = "ui_sorting"
                    st.rerun()

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
                try:
                    driver = setup_webdriver(headless=run_headless, st_module=st)
                    wait = WebDriverWait(driver, 20)
                    perform_login(driver=driver, wait=wait, group_name=group_name, username=username, password=password, api_key=api_key, st_module=st)
                    switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)

                    run_saved_search_task(driver=driver, wait=wait, st_module=st, max_articles=max_articles, saved_search_name=saved_search_name)

                    full_articles_data = scrape_articles_by_news_id(driver, wait, final_list, st_module=st)

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
                            file_data = f.read()

                    st.session_state[f"{prefix}_final_docx"] = file_data
                    fb_logger.save_final_docx_bytes_to_date_folder(file_data, "final_report.docx", base_folder=base_folder)

                    user_final_list = fb_logger.load_json_from_date_folder("user_final_list.json", {}, base_folder=base_folder)
                    trimmed_bytes = trim_docx_bytes_with_userlist(file_data, user_final_list, keep_body_paras=3)
                    fb_logger.save_final_docx_bytes_to_date_folder(trimmed_bytes, "final_report_trimmed.docx", base_folder=base_folder)
                    st.session_state[f"{prefix}_final_docx_trimmed"] = trimmed_bytes

                    st.session_state[stage_key] = "finished"
                    robust_logout_request(driver, st)
                    driver.quit()
                    st.rerun()

                except Exception as e:
                    st.error(f"爬取失敗: {e}")
                    if st.button("重試", key=f"{prefix}-final-retry"):
                        st.rerun()

        if st.session_state[stage_key] == "finished":
            st.header("🎉 任務全部完成！")

            if not st.session_state.get(f"{prefix}_final_docx"):
                with st.spinner("🔄 從 Firebase 重新生成下載文件..."):
                    docx_bytes = fb_logger.load_final_docx_from_date_folder("final_report.docx", base_folder=base_folder)
                    if not docx_bytes:
                        final_articles = st.session_state.get(
                            f"{prefix}_final_articles",
                            fb_logger.load_json_from_date_folder("full_scraped_articles.json", [], base_folder=base_folder),
                        )
                        if final_articles:
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                                out_path = create_international_news_report(
                                    articles_data=final_articles,
                                    output_path=tmp.name,
                                    st_module=st,
                                    report_title=report_title,
                                )
                                with open(out_path, "rb") as f:
                                    docx_bytes = f.read()
                    if docx_bytes:
                        st.session_state[f"{prefix}_final_docx"] = docx_bytes
                    else:
                        st.error("❌ 無法恢復最終報告，請重新執行爬取")
                        return

            ensure_trimmed_docx_in_firebase_and_session(fb_logger, prefix, base_folder)

            colA, colB, colC = st.columns([0.38, 0.38, 0.24])
            with colA:
                st.download_button(
                    label="下载 Word（完整）",
                    data=st.session_state[f"{prefix}_final_docx"],
                    file_name=f"{config['file_prefix']}{today}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="primary",
                    use_container_width=True,
                    key=f"{prefix}-download-full",
                )
            with colB:
                st.download_button(
                    label="下载 Word（trim）",
                    data=st.session_state[f"{prefix}_final_docx_trimmed"],
                    file_name=f"{config['file_prefix']}{today}_trimmed.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    type="secondary",
                    use_container_width=True,
                    key=f"{prefix}-download-trim",
                )
            with colC:
                st.button(
                    "回到50%调整排序",
                    type="secondary",
                    use_container_width=True,
                    on_click=rollback_to_ui_sorting,
                    args=(fb_logger, prefix, base_folder, category_label),
                    key=f"{prefix}-finished-rollback",
                )

            col1, col2 = st.columns(2)
            with col1:
                st.metric("總文章數", len(st.session_state.get(f"{prefix}_final_articles", [])))
            with col2:
                st.metric("Firebase 狀態", "✅ 完整備份")

            st.success(f"💾 完整備份: `{base_folder}/{today}/`")

            if st.button("🔄 開始新任務", key=f"{prefix}-finished-new"):
                st.session_state[stage_key] = "smart_home"
                st.rerun()

    except Exception as e:
        st.error(f"發生未預期的錯誤: {e}")
        st.code(traceback.format_exc())


def _get_credentials():
    try:
        group_name = st.secrets["wisers"]["group_name"]
        username = st.secrets["wisers"]["username"]
        password = st.secrets["wisers"]["password"]
        return group_name, username, password
    except Exception:
        return None, None, None


def _get_api_key():
    try:
        return st.secrets["wisers"]["api_key"]
    except Exception:
        return None


def render_greater_china_keywords_tab():
    config = NEWS_TABS["greater_china"]
    st.header(config["tab_title"])

    with st.sidebar:
        st.subheader(f"{config['tab_title']} Settings")
        max_words = st.slider("Max Words", 200, 2000, 1000, step=50, key=f"{config['prefix']}-max-words")
        min_words = st.slider("Min Words", 50, 500, 200, step=50, key=f"{config['prefix']}-min-words")
        max_articles = st.slider("Max Articles", 10, 100, 30, key=f"{config['prefix']}-max-articles")

        group, user, pwd = _get_credentials()
        api_key = _get_api_key()
        if not all([group, user, pwd, api_key]):
            st.warning("請在 secrets.toml 配置憑證，或在此輸入：")
            group = st.text_input("Group", value=group or "", key=f"{config['prefix']}-group")
            user = st.text_input("User", value=user or "", key=f"{config['prefix']}-user")
            pwd = st.text_input("Password", type="password", value=pwd or "", key=f"{config['prefix']}-pwd")
            api_key = st.text_input("2Captcha Key", type="password", value=api_key or "", key=f"{config['prefix']}-api")

    if all([group, user, pwd, api_key]):
        _handle_saved_search_news_logic(
            config,
            group, user, pwd, api_key,
            run_headless=True, keep_browser_open=False,
            max_words=max_words, min_words=min_words, max_articles=max_articles,
        )
    else:
        st.error("請提供完整的 Wisers 帳號密碼及 API Key 才能開始。")


def render_hong_kong_politics_news_tab():
    config = NEWS_TABS["hong_kong_politics"]
    st.header(config["tab_title"])

    with st.sidebar:
        st.subheader(f"{config['tab_title']} Settings")
        max_words = st.slider("Max Words", 200, 2000, 1000, step=50, key=f"{config['prefix']}-max-words")
        min_words = st.slider("Min Words", 50, 500, 200, step=50, key=f"{config['prefix']}-min-words")
        max_articles = st.slider("Max Articles", 10, 100, 30, key=f"{config['prefix']}-max-articles")

        group, user, pwd = _get_credentials()
        api_key = _get_api_key()
        if not all([group, user, pwd, api_key]):
            st.warning("請在 secrets.toml 配置憑證，或在此輸入：")
            group = st.text_input("Group", value=group or "", key=f"{config['prefix']}-group")
            user = st.text_input("User", value=user or "", key=f"{config['prefix']}-user")
            pwd = st.text_input("Password", type="password", value=pwd or "", key=f"{config['prefix']}-pwd")
            api_key = st.text_input("2Captcha Key", type="password", value=api_key or "", key=f"{config['prefix']}-api")

    if all([group, user, pwd, api_key]):
        _handle_saved_search_news_logic(
            config,
            group, user, pwd, api_key,
            run_headless=True, keep_browser_open=False,
            max_words=max_words, min_words=min_words, max_articles=max_articles,
        )
    else:
        st.error("請提供完整的 Wisers 帳號密碼及 API Key 才能開始。")
