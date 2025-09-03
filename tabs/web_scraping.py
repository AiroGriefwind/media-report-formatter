import streamlit as st
import tempfile
import time
import traceback
from selenium.webdriver.support.ui import WebDriverWait
from datetime import datetime
from selenium.webdriver.common.by import By

# Import Wisers platform functions
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

# Import specific scraping functions
from utils.web_scraping_utils import (
    perform_author_search,
    click_first_result,
    scrape_author_article_content,
    run_newspaper_editorial_task,
    run_scmp_editorial_task,
    create_docx_report
)


def render_web_scraping_tab():
    """Render the web scraping and report generation tab"""
    st.header("Web Scraping and Report Generation")
    st.markdown("Scrape articles by specified authors and newspaper editorials, then generate a combined Word report.")
    
    with st.expander("⚙️ Scraping Configuration", expanded=True):
        col1, col2 = st.columns(2)
        
        with col1:
            group_name, username, password = _get_credentials()
            
        with col2:
            api_key = _get_api_key()
    
    authors_input = st.text_area(
        "Authors to Search (one per line)",
        value="李先知\n余錦賢\n傅流螢\n黄锦辉",
        help="Enter one author name per line. The script will search for the latest article from each."
    )
    
    # Sidebar options
    st.sidebar.header("Debugging Options")
    st.sidebar.markdown("---")
    run_headless = st.checkbox("Run in headless mode (faster, no visible browser)", value=True)
    keep_browser_open = st.sidebar.checkbox("Keep browser open after script finishes/fails")
    
    if st.button("🚀 Start Scraping and Generate Report", type="primary"):
        _handle_scraping_process(
            group_name, username, password, api_key,
            authors_input, run_headless, keep_browser_open
        )

def _get_credentials():
    """Helper function to get credentials from secrets or manual input"""
    try:
        group_name = st.secrets["wisers"]["group_name"]
        username = st.secrets["wisers"]["username"] 
        password = st.secrets["wisers"]["password"]
        st.success("✅ Credentials loaded from secrets")
        st.info(f"Group: {group_name}\n\nUsername: {username}\n\nPassword: ****")
        return group_name, username, password
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ Secrets not found. Please enter credentials manually:")
        group_name = st.text_input("Group Name", value="SPRG1")
        username = st.text_input("Username", placeholder="Enter username")
        password = st.text_input("Password", type="password", placeholder="Enter password")
        return group_name, username, password

def _get_api_key():
    """Helper function to get API key from secrets or manual input"""
    try:
        api_key = st.secrets["wisers"]["api_key"]
        st.success(f"✅ 2Captcha API Key loaded: {api_key[:8]}...")
        return api_key
    except (KeyError, AttributeError, st.errors.StreamlitAPIException):
        st.warning("⚠️ API key not found in secrets")
        return st.text_input("2Captcha API Key", type="password", placeholder="Enter API key")

def _handle_scraping_process(group_name, username, password, api_key, authors_input, run_headless, keep_browser_open):
    """Handle the main scraping process"""
    if not all([group_name, username, password, api_key]):
        st.error("❌ Please provide all required credentials and the API key to proceed.")
        st.stop()

    authors_list = [author.strip() for author in authors_input.split('\n') if author.strip()]
    if not authors_list:
        st.error("❌ Please enter at least one author to search.")
        st.stop()

    progress_bar = st.progress(0)
    status_text = st.empty()
    driver = None

    try:
        # Setup WebDriver
        status_text.text("Setting up web driver...")
        
        driver = setup_webdriver(headless=run_headless, st_module=st)
        if driver is None:
            st.error("Driver setup failed, cannot continue. See logs above for details.")
            st.stop()

        wait = WebDriverWait(driver, 20)
        progress_bar.progress(5, text="Driver ready. Logging in...")

        # Login
        perform_login(
            driver=driver, wait=wait, group_name=group_name,
            username=username, password=password, api_key=api_key, st_module=st
        )
        
        progress_bar.progress(10, text="Login successful. Finalizing setup...")
        time.sleep(5)

        # Setup environment
        close_tutorial_modal_ROBUST(driver=driver, wait=wait, status_text=status_text, st_module=st)
        switch_language_to_traditional_chinese(driver=driver, wait=wait, st_module=st)
        
        progress_bar.progress(15, text="Language set. Starting author search...")

        # Author search loop
        original_window = driver.current_window_handle
        author_articles_data = {}
        total_steps = len(authors_list) + 3
        progress_increment = 70 / total_steps

        for i, author in enumerate(authors_list):
            current_progress = 15 + (i * progress_increment)
            status_text.text(f"({i+1}/{len(authors_list)}) Searching for author: {author}...")
            progress_bar.progress(int(current_progress), text=f"Searching for {author}")

            perform_author_search(driver=driver, wait=wait, author=author, st_module=st)
            
            # --- NEW: wait for search results container to stabilize ---
            time.sleep(10)  # allow the list-group to fully render
            
            # NEW: Check explicitly for 'no article' message in the search results page
            no_article_elements = driver.find_elements(By.XPATH, '//div[@id="article-tab-1-view-1"]//h5[contains(text(),"没有文章，请修改关键词后重新进行搜索")]')
            
            if no_article_elements and len(no_article_elements) > 0:
                # Found no article message, skip this author with no retries, mark as not found
                st.info(f"No articles found for {author}, skipping.")
                author_articles_data[author] = {'title': '無法找到文章', 'content': ''}
                go_back_to_search_form(driver=driver, wait=wait, st_module=st)
                continue
            
            if wait_for_search_results(driver=driver, wait=wait, st_module=st):
                click_first_result(driver=driver, wait=wait, original_window=original_window, st_module=st)
                scraped_data = scrape_author_article_content(driver=driver, wait=wait, author_name=author, st_module=st)
                author_articles_data[author] = scraped_data
                st.write("Closing article tab and returning to search results...")
                driver.close()
                driver.switch_to.window(original_window)
            else:
                author_articles_data[author] = {'title': '無法找到文章', 'content': ''}
                st.info(f"No results found for {author}.")
                go_back_to_search_form(driver=driver, wait=wait, st_module=st)

        # Editorial scraping
        final_author_progress = 15 + (len(authors_list) * progress_increment)
        progress_bar.progress(int(final_author_progress), text="Scraping newspaper editorials...")
        status_text.text("Scraping newspaper editorials (from saved search)...")
        
        editorial_data = run_newspaper_editorial_task(driver=driver, wait=wait, st_module=st)
        if editorial_data is None:
            editorial_data = []

        st.write("Returning to main search form for SCMP task...")
        go_back_to_search_form(driver=driver, wait=wait, st_module=st)
        
        progress_bar.progress(int(final_author_progress + progress_increment), text="Scraping SCMP editorials...")
        status_text.text("Scraping SCMP editorials (manual search)...")
        
        scmp_editorial_data = run_scmp_editorial_task(driver=driver, wait=wait, st_module=st)
        if scmp_editorial_data:
            editorial_data.extend(scmp_editorial_data)

        # Generate report
        progress_bar.progress(int(final_author_progress + 2 * progress_increment), text="Generating Word document...")
        status_text.text("Creating final Word report...")
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_report:
            output_path = create_docx_report(
                author_articles_data=author_articles_data,
                editorial_data=editorial_data,
                author_list=authors_list,
                output_path=tmp_report.name,
                st_module=st
            )

        progress_bar.progress(95, text="Report generated. Logging out...")
        status_text.text("Logging out...")
        
        logout(driver=driver, wait=wait, st_module=st)
        robust_logout_request(driver, st_module=st)

        # Download link
        with open(output_path, 'rb') as f:
            st.download_button(
                label="📥 Download Combined Report",
                data=f.read(),
                file_name=f"香港社評報告_{datetime.now().strftime('%Y%m%d')}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )

        progress_bar.progress(100, text="✅ Process complete!")
        status_text.success("✅ Scraping and report generation completed successfully!")

        # Summary
        st.subheader("📊 Scraped Content Summary")
        for author, data in author_articles_data.items():
            st.write(f"**{author}**: {'Article found' if data else 'No article found'}")
        st.write(f"**Editorials**: Found {len(editorial_data)} total editorial articles.")
        st.success("✅ Scraping process completed successfully!")

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
