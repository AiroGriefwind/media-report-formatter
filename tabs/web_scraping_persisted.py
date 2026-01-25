import streamlit as st
import tempfile
import time
import traceback
from datetime import datetime
import pytz
from selenium.webdriver.support.ui import WebDriverWait

# Firebase logging
from utils.firebase_logging import ensure_logger

# Import Wisers platform functions
from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    close_tutorial_modal_ROBUST,
    switch_language_to_traditional_chinese,
    go_back_to_search_form,
    logout,
    robust_logout_request,
)

# Import specific scraping functions
from utils.web_scraping_utils import (
    perform_author_search,
    ensure_search_results_ready,
    _dump_tab_counters,
    click_first_result,
    scrape_author_article_content,
    run_newspaper_editorial_task,
    run_scmp_editorial_task,
    create_docx_report,
)

HKT = pytz.timezone("Asia/Hong_Kong")
TODAY = datetime.now(HKT).strftime("%Y%m%d")
WS_FOLDER = "web_scraping"


def _load_ws_json(fb_logger, filename, default):
    return fb_logger.load_json_from_date_folder(filename, default, base_folder=WS_FOLDER)


def _save_ws_json(fb_logger, data, filename):
    return fb_logger.save_json_to_date_folder(data, filename, base_folder=WS_FOLDER)


def _get_credentials():
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
        group_name = st.text_input("Group Name", value="SPRG1")
        username = st.text_input("Username", placeholder="Enter username")
        password = st.text_input("Password", type="password", placeholder="Enter password")
        bucket = None
        return group_name, username, password, bucket


def _get_api_key():
    """Helper function to get API key from secrets or manual input"""
    try:
        api_key = st.secrets["wisers"]["api_key"]
        st.success(f"✅ 2Captcha API Key loaded: {api_key[:8]}...")
        return api_key
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ API key not found in secrets")
        return st.text_input("2Captcha API Key", type="password", placeholder="Enter API key")


def check_ws_progress(fb_logger):
    authors_list = _load_ws_json(fb_logger, "authors_list.json", [])
    author_articles = _load_ws_json(fb_logger, "author_articles.json", {})
    editorial_data = _load_ws_json(fb_logger, "editorial_articles.json", [])
    report_bytes = fb_logger.load_final_docx_from_date_folder("web_scraping_report.docx", base_folder=WS_FOLDER)

    authors_total = len(authors_list) if authors_list else len(author_articles or {})
    authors_found = 0
    if authors_list:
        for author in authors_list:
            article = (author_articles or {}).get(author, {})
            if article and (article.get("content") or article.get("title")) and article.get("title") != "無法找到文章":
                authors_found += 1
    else:
        for _, article in (author_articles or {}).items():
            if article and (article.get("content") or article.get("title")) and article.get("title") != "無法找到文章":
                authors_found += 1

    return {
        "authors_total": authors_total,
        "authors_found": authors_found,
        "editorials_total": len(editorial_data or []),
        "has_authors_data": bool(author_articles),
        "has_editorials_data": bool(editorial_data),
        "has_report": bool(report_bytes),
    }


def ensure_ws_session_state(fb_logger):
    if "ws_authors_list" not in st.session_state:
        st.session_state.ws_authors_list = _load_ws_json(fb_logger, "authors_list.json", [])
    if "ws_author_articles" not in st.session_state:
        st.session_state.ws_author_articles = _load_ws_json(fb_logger, "author_articles.json", {})
    if "ws_editorial_data" not in st.session_state:
        st.session_state.ws_editorial_data = _load_ws_json(fb_logger, "editorial_articles.json", [])
    if "ws_report_docx" not in st.session_state:
        st.session_state.ws_report_docx = fb_logger.load_final_docx_from_date_folder(
            "web_scraping_report.docx",
            base_folder=WS_FOLDER,
        )
    if "ws_stage" not in st.session_state:
        st.session_state.ws_stage = "smart_home"


def restore_ws_progress(stage, should_rerun=True):
    if stage == "finished":
        fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context="tab_webscraping_firebase")
        st.session_state.ws_authors_list = _load_ws_json(fb_logger, "authors_list.json", [])
        st.session_state.ws_author_articles = _load_ws_json(fb_logger, "author_articles.json", {})
        st.session_state.ws_editorial_data = _load_ws_json(fb_logger, "editorial_articles.json", [])
        st.session_state.ws_report_docx = fb_logger.load_final_docx_from_date_folder(
            "web_scraping_report.docx",
            base_folder=WS_FOLDER,
        )
        st.session_state.ws_stage = "finished"

    if should_rerun:
        st.rerun()


def _ensure_ws_report_docx(fb_logger):
    if st.session_state.get("ws_report_docx"):
        return

    docx_bytes = fb_logger.load_final_docx_from_date_folder("web_scraping_report.docx", base_folder=WS_FOLDER)
    if docx_bytes:
        st.session_state.ws_report_docx = docx_bytes
        return

    authors_list = st.session_state.get("ws_authors_list") or _load_ws_json(fb_logger, "authors_list.json", [])
    author_articles = st.session_state.get("ws_author_articles") or _load_ws_json(fb_logger, "author_articles.json", {})
    editorial_data = st.session_state.get("ws_editorial_data") or _load_ws_json(fb_logger, "editorial_articles.json", [])

    if not authors_list and not author_articles and not editorial_data:
        st.error("❌ 無法恢復報告：找不到已保存的資料")
        st.stop()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_report:
        create_docx_report(
            author_articles_data=author_articles,
            editorial_data=editorial_data,
            author_list=authors_list,
            output_path=tmp_report.name,
            st_module=st,
        )
        with open(tmp_report.name, "rb") as f:
            docx_bytes = f.read()

    st.session_state.ws_report_docx = docx_bytes
    fb_logger.save_final_docx_bytes_to_date_folder(
        docx_bytes,
        "web_scraping_report.docx",
        base_folder=WS_FOLDER,
    )


def _handle_scraping_process_with_firebase(
    group_name, username, password, api_key, authors_input, run_headless, keep_browser_open
):
    authors_list = [author.strip() for author in authors_input.split("\n") if author.strip()]

    if not all([group_name, username, password, api_key]):
        st.error("Please provide all required credentials and the API key to proceed.")
        st.stop()

    if not authors_list:
        st.error("Please enter at least one author to search.")
        st.stop()

    progress_bar = st.progress(0)
    status_text = st.empty()
    driver = None
    fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context="tab_webscraping_firebase")

    try:
        status_text.text("Setting up the web driver...")
        driver = setup_webdriver(headless=run_headless, st_module=st)
        if driver is None:
            st.error("Driver setup failed, cannot continue. See logs above for details.")
            st.stop()

        wait = WebDriverWait(driver, 20)
        progress_bar.progress(5, text="Driver ready. Logging in...")

        perform_login(
            driver=driver,
            wait=wait,
            group_name=group_name,
            username=username,
            password=password,
            api_key=api_key,
            st_module=st,
        )

        progress_bar.progress(10, text="Login successful. Finalizing setup...")
        time.sleep(5)

        close_tutorial_modal_ROBUST(driver=driver, wait=wait, status_text=status_text, st_module=st)
        switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)

        progress_bar.progress(15, text="Language set. Starting author search...")

        original_window = driver.current_window_handle
        author_articles_data = {}
        total_steps = len(authors_list) + 3
        progress_increment = 70 / max(total_steps, 1)

        for i, author in enumerate(authors_list):
            current_progress = 15 + (i * progress_increment)
            status_text.text(f"({i+1}/{len(authors_list)}) Searching for author: {author}...")
            progress_bar.progress(int(current_progress), text=f"Searching for {author}")

            perform_author_search(driver=driver, wait=wait, author=author, st_module=st)

            has_results = ensure_search_results_ready(driver=driver, wait=wait, st_module=st)
            _dump_tab_counters(driver, st)

            if not has_results:
                st.info(f"No articles found for {author}, skipping.")
                author_articles_data[author] = {"title": "無法找到文章", "content": ""}
                go_back_to_search_form(driver=driver, wait=wait, st_module=st)
                continue

            click_first_result(driver=driver, wait=wait, original_window=original_window, st_module=st)

            scraped_data = scrape_author_article_content(
                driver=driver, wait=wait, author_name=author, st_module=st
            )
            author_articles_data[author] = scraped_data

            driver.close()
            driver.switch_to.window(original_window)
            go_back_to_search_form(driver=driver, wait=wait, st_module=st)

        final_author_progress = 15 + (len(authors_list) * progress_increment)
        progress_bar.progress(int(final_author_progress), text="Scraping newspaper editorials...")
        status_text.text("Scraping newspaper editorials (from saved search)...")

        editorial_data = run_newspaper_editorial_task(driver=driver, wait=wait, st_module=st) or []

        st.write("Returning to main search form for SCMP task...")
        go_back_to_search_form(driver=driver, wait=wait, st_module=st)

        progress_bar.progress(int(final_author_progress + progress_increment), text="Scraping SCMP editorials...")
        status_text.text("Scraping SCMP editorials (manual search)...")

        scmp_editorial_data = run_scmp_editorial_task(driver=driver, wait=wait, st_module=st)
        if scmp_editorial_data:
            editorial_data.extend(scmp_editorial_data)

        progress_bar.progress(int(final_author_progress + 2 * progress_increment), text="Generating Word document...")
        status_text.text("Creating final Word report...")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_report:
            create_docx_report(
                author_articles_data=author_articles_data,
                editorial_data=editorial_data,
                author_list=authors_list,
                output_path=tmp_report.name,
                st_module=st,
            )
            with open(tmp_report.name, "rb") as f:
                docx_bytes = f.read()

        progress_bar.progress(95, text="Saving to Firebase...")
        _save_ws_json(fb_logger, authors_list, "authors_list.json")
        _save_ws_json(fb_logger, author_articles_data, "author_articles.json")
        _save_ws_json(fb_logger, editorial_data, "editorial_articles.json")
        fb_logger.save_final_docx_bytes_to_date_folder(
            docx_bytes, "web_scraping_report.docx", base_folder=WS_FOLDER
        )

        progress_bar.progress(98, text="Logging out...")
        logout(driver=driver, wait=wait, st_module=st)
        robust_logout_request(driver, st_module=st)

        st.session_state.ws_authors_list = authors_list
        st.session_state.ws_author_articles = author_articles_data
        st.session_state.ws_editorial_data = editorial_data
        st.session_state.ws_report_docx = docx_bytes

        progress_bar.progress(100, text="✅ Process complete!")
        status_text.success("✅ Scraping and report generation completed successfully!")

        st.session_state.ws_stage = "finished"
        st.rerun()

    except Exception as e:
        st.error(f"❌ A critical error stopped the script: {str(e)}")
        st.code(traceback.format_exc())
    finally:
        try:
            if driver:
                if not keep_browser_open:
                    robust_logout_request(driver, st_module=st)
                else:
                    st.warning("🤖 As requested, the browser window has been left open for inspection.")
        except Exception as cleanup_err:
            st.error(f"Error in cleanup: {cleanup_err}")


def render_web_scraping_persisted_tab():
    """Render the web scraping tab with Firebase persistence"""
    try:
        ensure_logger(st, run_context="tab_webscraping_firebase")
        fb_logger = st.session_state.get("fb_logger") or ensure_logger(st, run_context="tab_webscraping_firebase")

        st.header("Web Scraping (Firebase)")
        st.markdown("Scrape editorials and specified authors, with progress restored from Firebase.")

        ensure_ws_session_state(fb_logger)

        if st.session_state.get("ws_need_rerun", False):
            st.session_state.ws_need_rerun = False
            st.rerun()

        if st.session_state.ws_stage == "smart_home":
            st.header("🧭 進度恢復")
            st.info(f"📁 Firebase: `{WS_FOLDER}/{TODAY}/` | {datetime.now().strftime('%H:%M')}")

            progress = check_ws_progress(fb_logger)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("👤 作者社評", f"{progress['authors_found']} / {progress['authors_total']} 篇")
            with col2:
                st.metric("📰 報章社評", f"{progress['editorials_total']} 篇")
            with col3:
                st.metric("📄 報告檔案", "✅" if progress["has_report"] else "❌")

            st.divider()

            if progress["has_report"]:
                st.success("🎉 已完成資料與報告，可直接下載")
                if st.button("📥 進入下載頁", type="primary", use_container_width=True, key="ws_fb_go_download"):
                    restore_ws_progress("finished")
            elif progress["has_authors_data"] or progress["has_editorials_data"]:
                st.warning("⏳ 已有爬取資料，尚未生成/恢復報告")
                if st.button("♻️ 恢復資料並生成報告", type="primary", use_container_width=True, key="ws_fb_restore_report"):
                    restore_ws_progress("finished")
            else:
                st.success("🆕 今日尚無資料，開始新的爬取")
                if st.button("🚀 開始爬取", type="primary", use_container_width=True, key="ws_fb_start_scraping"):
                    st.session_state.ws_stage = "scraping"
                    st.rerun()

            st.divider()

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("🔄 忽略進度重來", type="secondary", use_container_width=True, key="ws_fb_restart"):
                    for key in [
                        "ws_stage",
                        "ws_authors_list",
                        "ws_author_articles",
                        "ws_editorial_data",
                        "ws_report_docx",
                    ]:
                        if key in st.session_state:
                            del st.session_state[key]
                    st.session_state.ws_stage = "scraping"
                    st.rerun()
            with col_b:
                if st.button("📋 查看 JSON 數據", type="secondary", use_container_width=True, key="ws_fb_view_json"):
                    st.session_state.ws_stage = "data_viewer"
                    st.rerun()

            return

        if st.session_state.ws_stage == "data_viewer":
            st.header("📋 JSON 數據檢視")
            if st.button("返回進度頁", key="ws_fb_back_home_top"):
                st.session_state.ws_stage = "smart_home"
                st.rerun()
            col1, col2, col3 = st.columns(3)
            with col1:
                st.json(_load_ws_json(fb_logger, "authors_list.json", []))
            with col2:
                st.json(_load_ws_json(fb_logger, "author_articles.json", {}))
            with col3:
                st.json(_load_ws_json(fb_logger, "editorial_articles.json", []))
            if st.button("返回進度頁", key="ws_fb_back_home_bottom"):
                st.session_state.ws_stage = "smart_home"
                st.rerun()
            return

        if st.session_state.ws_stage == "scraping":
            st.subheader("Web Scraping and Report Generation")
            st.markdown("Scrape articles by specified authors and newspaper editorials, then generate a Word report.")

            with st.expander("⚙️ Scraping Configuration", expanded=True):
                col1, col2 = st.columns(2)
                with col1:
                    group_name, username, password, _bucket = _get_credentials()
                with col2:
                    api_key = _get_api_key()

            authors_input = st.text_area(
                "Authors to Search (one per line)",
                value="李先知\n余錦賢\n傅流螢\n黄锦辉",
                help="Enter one author name per line. The script will search for the latest article from each.",
                key="ws_firebase_authors_input",
            )

        if st.button("🚀 Start Scraping and Generate Report", type="primary", key="ws_fb_run_scrape"):
                _handle_scraping_process_with_firebase(
                    group_name,
                    username,
                    password,
                    api_key,
                    authors_input,
                    run_headless=True,
                    keep_browser_open=False,
                )

        if st.session_state.ws_stage == "finished":
            st.header("🎉 任務完成，可下載報告")
            _ensure_ws_report_docx(fb_logger)

            st.download_button(
                label="📥 Download Combined Report",
                data=st.session_state.ws_report_docx,
                file_name=f"香港社評報告_{TODAY}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                use_container_width=True,
            )

            st.subheader("📊 Scraped Content Summary")
            author_articles = st.session_state.get("ws_author_articles", {})
            authors_list = st.session_state.get("ws_authors_list", []) or list(author_articles.keys())
            for author in authors_list:
                data = author_articles.get(author)
                if data and (data.get("content") or data.get("title")) and data["title"] != "無法找到文章":
                    st.write(f"**{author}**: Article found")
                else:
                    st.write(f"**{author}**: No article found")
            st.write(f"**Editorials**: Found {len(st.session_state.get('ws_editorial_data', []))} total editorial articles.")

        if st.button("🔄 開始新任務", type="secondary", key="ws_fb_new_task"):
                st.session_state.ws_stage = "smart_home"
                st.rerun()
    except Exception as e:
        st.error(f"❌ Web Scraping (Firebase) 渲染失败: {e}")
        st.code(traceback.format_exc())
