import streamlit as st
import tempfile
import time
import traceback
from datetime import datetime
from selenium.webdriver.support.ui import WebDriverWait
from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    close_tutorial_modal_ROBUST,
    switch_language_to_traditional_chinese,
    go_back_to_search_form,
    logout,
    robust_logout_request,
    wait_for_search_results
)
from utils.web_scraping_utils import scrape_hover_popovers


# Import Wisers platform functions
from utils.wisers_utils import (
    setup_webdriver,
    perform_login,
    close_tutorial_modal_ROBUST,
    switch_language_to_traditional_chinese,
    logout,
    robust_logout_request
)

# Import international news specific functions
from utils.international_news_utils import (
    run_international_news_task,
    scrape_international_articles_sequentially,
    create_international_news_report,
    create_hover_preview_report,
    should_scrape_article_based_on_metadata
)

def _handle_international_hover_preview(
    group_name_intl,
    username_intl,
    password_intl,
    api_key_intl,
    run_headless_intl,
    keep_browser_open_intl,
    max_words,          
    min_words,          
):
    """
    Login, run 國際新聞 search, then scrape and display hoverbox previews
    without clicking into full articles (no 'authentic clicks').
    
    Uses st.session_state to persist preview_list across reruns.
    """
    if not all([group_name_intl, username_intl, password_intl, api_key_intl]):
        st.error("❌ Please provide all required credentials and the API key to proceed.")
        st.stop()

    # Initialize session state for hover preview
    if "intl_hover_preview_list" not in st.session_state:
        st.session_state.intl_hover_preview_list = None
    
    driver = None

    try:
        # Only run scraping if preview_list is not already in session state
        if st.session_state.intl_hover_preview_list is None:
            with st.spinner("Setting up browser and logging in for hover preview..."):
                driver = setup_webdriver(headless=run_headless_intl, st_module=st)
                if driver is None:
                    st.error("Driver setup failed, cannot continue. See logs above for details.")
                    st.stop()

                wait = WebDriverWait(driver, 20)

                # Reuse the central login handler
                perform_login(
                    driver=driver,
                    wait=wait,
                    group_name=group_name_intl,
                    username=username_intl,
                    password=password_intl,
                    api_key=api_key_intl,
                    st_module=st,
                )

                # perform_login already attempts to close the tutorial modal.
                # Just ensure language is set.
                switch_language_to_traditional_chinese(
                    driver=driver,
                    wait=wait,
                    st_module=st,
                )

                 # 1. 运行搜索
            st.info("Running 國際新聞 saved search to populate results list...")
            _ = run_international_news_task(driver=driver, wait=wait, st_module=st)
            
            # 2. 先进行悬浮爬取 (这一步比较耗时，先做了拿到所有内容)
            st.info("Scraping article hover previews (no authentic click)...")
            raw_preview_list = scrape_hover_popovers(driver=driver, wait=wait, st_module=st)
            
            # 3. 【关键修改】遍历页面元素读取 Metadata，利用现有逻辑进行过滤
            st.info("Applying word count & opinion filters based on metadata...")
            
            filtered_preview_list = []
            
            # 重新获取页面上的列表元素，用于提取 metadata
            # 注意：这里的 list-group-item 顺序必须和 scrape_hover_popovers 返回的顺序一致
            # 通常它们都是按 DOM 顺序抓取的，所以 index 是一一对应的
            article_elements = driver.find_elements(By.CSS_SELECTOR, 'div.list-group-item.no-excerpt')
            
            # 确保元素数量一致，防止越界（通常是一致的）
            limit = min(len(article_elements), len(raw_preview_list))
            
            for i in range(limit):
                element = article_elements[i]
                preview_item = raw_preview_list[i]
                
                try:
                    # 提取 metadata，逻辑参考了 scrape_international_articles_sequentially
                    metadata_text = element.find_element(By.CSS_SELECTOR, 'small').text.strip()
                    
                    # 直接调用现有的判断函数！
                    is_valid = should_scrape_article_based_on_metadata(
                        metadata_text, 
                        min_words=min_words, 
                        max_words=max_words
                    )
                    
                    if is_valid:
                        # 如果通过检查，保留这个 preview item
                        # 顺便把 metadata 也存进去，方便调试或报告展示
                        preview_item['metadata_line'] = metadata_text 
                        filtered_preview_list.append(preview_item)
                    else:
                        # 这里的 print/write 是可选的，调试用
                        # print(f"Filtered out index {i}: {metadata_text}")
                        pass
                        
                except Exception as e:
                    # 如果找不到 metadata，默认保留或者记录错误
                    # 这里选择默认保留，防止误杀
                    filtered_preview_list.append(preview_item)

            st.success(f"Found {len(raw_preview_list)} previews, {len(filtered_preview_list)} passed filters.")

            # 更新 session state 为过滤后的列表
            st.session_state.intl_hover_preview_list = filtered_preview_list
            
            if not filtered_preview_list:
                st.warning("No results passed the word count/filter criteria.")
                return
            preview_list = filtered_preview_list
            
        else:
            preview_list = st.session_state.intl_hover_preview_list
            st.info(f"📌 Using cached preview list ({len(preview_list)} articles from previous scrape)")

        # Display titles for selection
        titles = [
            item.get("title") or f"Result {i+1}"
            for i, item in enumerate(preview_list)
        ]

        selected_idx = st.multiselect(
            "Select articles that look relevant (this does NOT click into articles)",
            options=list(range(len(titles))),
            format_func=lambda i: titles[i],
        )

        # Display selected articles
        if selected_idx:
            st.subheader("Preview details of selected articles:")
            for i in selected_idx:
                item = preview_list[i]
                st.markdown(f"**{i+1}. {item.get('title', f'Result {i+1}') }**")

                # Prefer HTML preview if available
                hover_html = item.get("hover_html") or item.get("hover_text", "")
                if hover_html:
                    st.markdown(hover_html, unsafe_allow_html=True)
                else:
                    st.markdown("_No hover content captured for this item._")
                st.markdown("---")

            # Report for SELECTED items
            if st.button("📄 Generate & Download Report (selected only)", key="intl_hover_report_selected"):
                selected_previews = [preview_list[i] for i in selected_idx]
                with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_report:
                    output_path = create_hover_preview_report(
                        preview_data=selected_previews,
                        output_path=tmp_report.name,
                        st_module=st,
                    )

                with open(output_path, "rb") as f:
                    st.download_button(
                        label="📥 Download Hover Preview Report (selected)",
                        data=f.read(),
                        file_name=f"國際新聞懸停預覽_精選_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="intl_hover_dl_selected",
                    )
                st.success("✅ Report for selected items ready for download!")

        # Report for ALL items (no selection required)
        if st.button("📄 Download Report for ALL hover previews", key="intl_hover_report_all"):
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_report:
                output_path = create_hover_preview_report(
                    preview_data=preview_list,
                    output_path=tmp_report.name,
                    st_module=st,
                )

            with open(output_path, "rb") as f:
                st.download_button(
                    label="📥 Download Hover Preview Report (ALL)",
                    data=f.read(),
                    file_name=f"國際新聞懸停預覽_全部_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="intl_hover_dl_all",
                )
            st.success("✅ Report for ALL hover previews ready for download!")

        # Clear cache button
        if st.button("🔄 Clear Cache & Scrape Again", key="intl_hover_clear"):
            st.session_state.intl_hover_preview_list = None
            st.session_state["intl_hover_ui_active"] = False
            st.rerun()


    except Exception as e:
        st.error(f"❌ Hover preview failed: {str(e)}")
        st.code(traceback.format_exc())
    finally:
        # Ensure logout to avoid dangling sessions
        try:
            if driver:
                if not keep_browser_open_intl:
                    try:
                        logout(driver=driver, wait=WebDriverWait(driver, 10), st_module=st)
                    except Exception:
                        pass
                    robust_logout_request(driver, st_module=st)
                else:
                    st.warning("🤖 Browser kept open for inspection as requested.")
        except Exception as cleanup_err:
            st.error(f"Error during hover-preview cleanup: {cleanup_err}")


def render_international_news_tab():
    """Render the international news scraping tab"""
    st.header("International News Scraping")
    st.markdown("Scrape 80-100 pieces of international news articles and generate a Word report.")

    # -------------------------
    # Credentials & basic config
    # -------------------------
    with st.expander("⚙️ International News Configuration", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            group_name_intl, username_intl, password_intl = _get_credentials_intl()

        with col2:
            api_key_intl = _get_api_key_intl()

    # International news specific settings
    max_articles = st.slider(
        "Maximum articles to scrape",
        min_value=50,
        max_value=150,
        value=100,
        help="Limit the number of international news articles to scrape",
    )
    max_words = st.slider(
        "Maximum word count per article",
        min_value=200,
        max_value=2000,
        value=1000,
        help="Skip articles longer than this word count",
    )
    min_words = st.slider(
        "Minimum word count per article",
        min_value=50,
        max_value=500,
        value=200,
        help="Skip articles shorter than this word count",
    )

    # Sidebar options
    st.sidebar.header("International News Options")
    st.sidebar.markdown("---")
    run_headless_intl = st.sidebar.checkbox(
        "Run in headless mode (faster, no visible browser)",
        value=True,
        key="intl_headless",
    )
    keep_browser_open_intl = st.sidebar.checkbox(
        "Keep browser open after script finishes/fails",
        key="intl_keep_open",
    )

    # -------------------------
    # New: Hover preview flow (no authentic click)
    # -------------------------

    # Flag to remember that the hover UI should be shown
    if "intl_hover_ui_active" not in st.session_state:
        st.session_state["intl_hover_ui_active"] = False

    preview_clicked = st.button("🔍 Preview Hover Results (no authentic click)")

    if preview_clicked:
        # Turn on the hover UI and rerun so widgets render properly
        st.session_state["intl_hover_ui_active"] = True
        st.rerun()

    if st.session_state["intl_hover_ui_active"]:
        _handle_international_hover_preview(
            group_name_intl=group_name_intl,
            username_intl=username_intl,
            password_intl=password_intl,
            api_key_intl=api_key_intl,
            run_headless_intl=run_headless_intl,
            keep_browser_open_intl=keep_browser_open_intl,
            max_words=max_words,      # 新增
            min_words=min_words,      # 新增
        )


    st.markdown("---")

    # -------------------------
    # Existing: Full scraping flow
    # -------------------------
    if st.button("🌍 Start International News Scraping", type="primary"):
        _handle_international_news_scraping(
            group_name_intl,
            username_intl,
            password_intl,
            api_key_intl,
            max_articles,
            max_words,
            min_words,
            run_headless_intl,
            keep_browser_open_intl,
        )

def _get_credentials_intl():
    """Helper function to get credentials for international news"""
    try:
        group_name_intl = st.secrets["wisers"]["group_name"]
        username_intl = st.secrets["wisers"]["username"]
        password_intl = st.secrets["wisers"]["password"]
        st.success("✅ Credentials loaded from secrets")
        st.info(f"Group: {group_name_intl}\n\nUsername: {username_intl}\n\nPassword: ****")
        return group_name_intl, username_intl, password_intl
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ Secrets not found. Please enter credentials manually:")
        group_name_intl = st.text_input("Group Name", value="SPRG1", key="intl_group")
        username_intl = st.text_input("Username", placeholder="Enter username", key="intl_username")
        password_intl = st.text_input("Password", type="password", placeholder="Enter password", key="intl_password")
        return group_name_intl, username_intl, password_intl

def _get_api_key_intl():
    """Helper function to get API key for international news"""
    try:
        api_key_intl = st.secrets["wisers"]["api_key"]
        st.success(f"✅ 2Captcha API Key loaded: {api_key_intl[:8]}...")
        return api_key_intl
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ API key not found in secrets")
        return st.text_input("2Captcha API Key", type="password", placeholder="Enter API key", key="intl_api")

def _handle_international_news_scraping(group_name_intl, username_intl, password_intl, api_key_intl, 
                                       max_articles, max_words, min_words, run_headless_intl, keep_browser_open_intl):
    """Handle the international news scraping process"""
    if not all([group_name_intl, username_intl, password_intl, api_key_intl]):
        st.error("❌ Please provide all required credentials and the API key to proceed.")
        st.stop()

    progress_bar = st.progress(0)
    status_text = st.empty()
    driver = None

    try:
        # Setup WebDriver
        status_text.text("Setting up web driver for international news...")
        driver = setup_webdriver(headless=run_headless_intl, st_module=st)
        if driver is None:
            st.error("Driver setup failed, cannot continue. See logs above for details.")
            st.stop()

        wait = WebDriverWait(driver, 20)
        progress_bar.progress(5, text="Driver ready. Logging in...")

        # Login
        perform_login(
            driver=driver, wait=wait, group_name=group_name_intl,
            username=username_intl, password=password_intl,
            api_key=api_key_intl, st_module=st
        )
        
        progress_bar.progress(10, text="Login successful. Finalizing setup...")
        time.sleep(5)

        # Setup environment  
        close_tutorial_modal_ROBUST(driver=driver, wait=wait, status_text=status_text, st_module=st)
        switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)
        
        progress_bar.progress(20, text="Language set. Searching for international news...")

        # Get international news article list
        status_text.text("Searching for international news articles...")
        articles_list = []
        
        try:
            articles_list = run_international_news_task(driver=driver, wait=wait, st_module=st)
        except Exception as search_error:
            st.error(f"Search for international news failed: {search_error}")
            articles_list = []

        # Handle no articles case
        if not articles_list:
            st.warning("⚠️ No international news articles found. This could mean:")
            st.info("• The '國際新聞' saved search doesn't exist")
            st.info("• The search returned no results") 
            st.info("• There was an error accessing the search")
            
            progress_bar.progress(90, text="No articles found. Logging out...")
            status_text.text("No articles to scrape. Proceeding to logout...")

            # Create empty report
            with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_report:
                output_path = create_international_news_report(
                    articles_data=[],
                    output_path=tmp_report.name,
                    st_module=st
                )

            with open(output_path, 'rb') as f:
                st.download_button(
                    label="📥 Download Empty Report",
                    data=f.read(),
                    file_name=f"國際新聞報告_空_{datetime.now().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
        else:
            # Limit articles and scrape
            articles_list = articles_list[:max_articles]
            st.info(f"Found {len(articles_list)} articles to scrape.")
            
            progress_bar.progress(30, text=f"Found {len(articles_list)} articles. Starting detailed scraping...")
            st.info(f"Starting sequential article scraping...")

            # Scrape articles sequentially with filtering
            scraped_articles, skipped_articles = scrape_international_articles_sequentially(
                driver=driver,
                wait=wait,
                max_articles=max_articles,
                max_words=max_words,
                min_words=min_words,
                st_module=st
            )

            progress_bar.progress(80, text="Creating Word document report...")
            status_text.text("Generating international news report...")

            # Generate report
            with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_report:
                output_path = create_international_news_report(
                    articles_data=scraped_articles,
                    output_path=tmp_report.name,
                    st_module=st
                )

            # Provide download
            with open(output_path, 'rb') as f:
                st.download_button(
                    label="📥 Download International News Report",
                    data=f.read(),
                    file_name=f"國際新聞報告_{datetime.now().strftime('%Y%m%d')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )

            # Summary
            st.subheader("📊 International News Summary")
            st.write(f"**Total articles scraped**: {len(scraped_articles)}")

            # Extract media names for summary
            media_count = {}
            for article in scraped_articles:
                metadata_line = article.get('metadata_line', 'Unknown')
                media_name = "Unknown"
                
                if '|' in metadata_line:
                    first_part = metadata_line.split('|')[0].strip()
                    parts = first_part.split()
                    if parts:
                        media_name = parts
                else:
                    parts = metadata_line.split()
                    if parts:
                        media_name = parts
                        
                media_count[media_name] = media_count.get(media_name, 0) + 1

            for media, count in sorted(media_count.items()):
                st.write(f"**{media}**: {count} articles")

        # ALWAYS logout regardless of success or failure
        progress_bar.progress(90, text="Report completed. Logging out...")
        status_text.text("Logging out...")
        
        try:
            logout(driver=driver, wait=wait, st_module=st)
            robust_logout_request(driver, st_module=st)
            st.success("✅ Successfully logged out.")
        except Exception as logout_error:
            st.error(f"Logout failed: {logout_error}")
            try:
                robust_logout_request(driver, st_module=st)
                st.info("✅ Robust logout completed.")
            except Exception as robust_error:
                st.error(f"Robust logout also failed: {robust_error}")

        progress_bar.progress(100, text="✅ International news process complete!")
        status_text.success("✅ International news processing completed!")

    except Exception as e:
        st.error(f"❌ A critical error occurred: {str(e)}")
        st.code(traceback.format_exc())
    finally:
        # CRITICAL: Always attempt logout in finally block
        try:
            if driver:
                st.write("🔄 Ensuring logout in finally block...")
                if not keep_browser_open_intl:
                    try:
                        logout(driver=driver, wait=wait, st_module=st)
                    except:
                        pass  # Continue to robust logout even if normal logout fails
                    robust_logout_request(driver, st_module=st)
                    st.write("✅ Final logout completed.")
                else:
                    st.warning("🤖 Browser kept open for inspection as requested.")
        except Exception as cleanup_err:
            st.error(f"Error in final cleanup: {cleanup_err}")
