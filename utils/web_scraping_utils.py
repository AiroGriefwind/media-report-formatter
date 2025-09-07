# =============================================================================
# SHARED WEB SCRAPING FUNCTIONS
# =============================================================================

import time
import base64
import tempfile
import os
import traceback
from functools import wraps
from collections import defaultdict
import re

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from twocaptcha import TwoCaptcha
from docx import Document
from datetime import datetime

# Import shared Wisers functions
from .wisers_utils import (
    retry_step, 
    wait_for_search_results, 
    scroll_to_load_all_content, 
    wait_for_ajax_complete,
)

from .config import WISERS_URL, MEDIA_NAME_MAPPINGS, EDITORIAL_MEDIA_ORDER, EDITORIAL_MEDIA_NAMES

# -----------------------------------------------------------------
# Helper: detect the “no-article” empty-state in multiple layouts
# -----------------------------------------------------------------
NO_ARTICLE_XPATHS = [
    "//h5[contains(normalize-space(),'没有文章')]",
    "//div[contains(@class,'empty-result') and contains(.,'没有文章')]",
    "//p[contains(.,'没有文章')]",
]

def _no_article_found(driver):
    """Return True if any known ‘no article’ element is present."""
    from selenium.webdriver.common.by import By
    for xp in NO_ARTICLE_XPATHS:
        if driver.find_elements(By.XPATH, xp):
            return True
    return False


# =============================================================================
# AUTHOR SEARCH SPECIFIC FUNCTIONS
# =============================================================================

@retry_step
def perform_author_search(**kwargs):
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    author_name = kwargs.get('author')
    st = kwargs.get('st_module')
     

    toggle_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.toggle-collapse[data-toggle="collapse"]')))
    driver.execute_script("arguments[0].click();", toggle_button)
    time.sleep(3)
    my_media_dropdown_toggle = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.btn-naked.dropdown-toggle[data-toggle="dropdown"]')))
    my_media_dropdown_toggle.click()
    time.sleep(3)
    hongkong_option = wait.until(EC.element_to_be_clickable((By.XPATH, '//label[span[text()="各大香港報章"]]')))
    hongkong_option.click()
    time.sleep(3)
    author_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input.form-control[placeholder="作者"]')))
    author_input.clear()
    author_input.send_keys(author_name)
    search_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button#toggle-query-execute.btn.btn-primary')))
    search_button.click()

@retry_step
def ensure_search_results_ready(**kwargs):
    """
    Wait until either:
    • at least one clickable headline appears, or
    • the page shows any recognised ‘no-article’ empty state.
    """
    driver = kwargs['driver']
    wait   = kwargs['wait']
    st     = kwargs.get('st_module')

    # Condition: a result headline is clickable
    headline_cond = EC.element_to_be_clickable(
        (By.CSS_SELECTOR, "div.list-group .list-group-item h4 a")
    )

    try:
        WebDriverWait(driver, 8).until(
            lambda d: headline_cond(d) or _no_article_found(d)
        )
        if st:
            st.write("[ensure_search_results_ready] Search results ready.")
    except TimeoutException:
        # Optional: screenshot for debugging
        if st:
            img = driver.get_screenshot_as_png()
            st.image(img, caption="Search results timeout")
            st.download_button(
                "Download timeout screenshot",
                data=img,
                file_name="search_timeout.png",
                mime="image/png",
                key="timeout_btn"
            )
        raise



@retry_step
def click_first_result(**kwargs):
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    original_window = kwargs.get('original_window')
    st = kwargs.get('st_module')

    # Make sure the headline is still attached each time we click
    def safe_click():
        headline = wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div.list-group .list-group-item h4 a")
            )
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", headline)
        headline.click()

    safe_click()

    # Wait up to 3 s for a new tab
    try:
        WebDriverWait(driver, 3).until(EC.number_of_windows_to_be(2))
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        if st:
            st.write("[click_first_result] Opened in new tab.")
        return
    except TimeoutException:
        if st:
            st.warning("[click_first_result] New tab not detected within 3s, retrying click...")
        # Retry click once
        first_link = wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "div.list-group .list-group-item h4 a")
            )
        )
        first_link.click()
        # Then wait again for the new tab
        WebDriverWait(driver, 3).until(EC.number_of_windows_to_be(2))
        for handle in driver.window_handles:
            if handle != original_window:
                driver.switch_to.window(handle)
                break
        if st:
            st.write("[click_first_result] Opened in new tab after retry.")



@retry_step
def parse_media_info_for_author(**kwargs):
    subheading_text = kwargs.get('subheading_text')
    author_name = kwargs.get('author_name')
    st = kwargs.get('st_module')
     

    media_part = subheading_text.split('|')[0].strip()
    page_match = re.search(r'([A-Z]\d{2})', media_part)
    if page_match:
        page_number = page_match.group(1)
        media_name_part = media_part[:page_match.start()].strip()
        mapped_name = next((v for k, v in MEDIA_NAME_MAPPINGS.items() if k in media_name_part), media_name_part)
        return f"{mapped_name} {page_number} {author_name}："

@retry_step
def scrape_author_article_content(**kwargs):
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    author_name = kwargs.get('author_name')
    st = kwargs.get('st_module')
     

    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.article-detail')))
    time.sleep(3)
    title = driver.find_element(By.CSS_SELECTOR, 'h3').text.strip()
    subheading_text = driver.find_element(By.CSS_SELECTOR, 'div.article-subheading').text.strip()
    media_info = parse_media_info_for_author(subheading_text=subheading_text,author_name=author_name,st_module=st)
    paragraphs = [p.text.strip() for p in driver.find_elements(By.CSS_SELECTOR, 'div.description p') if p.text.strip()]
    if paragraphs:
        formatted_first_paragraph = f"{media_info}{paragraphs[0]}"
        full_content = [formatted_first_paragraph] + paragraphs[1:]
        formatted_content_body = '\n\n'.join(full_content)
        final_output = f"{title}\n\n{formatted_content_body}"
    else:
        final_output = title
    return {'title': title, 'content': final_output}

# =============================================================================
# EDITORIAL SCRAPING FUNCTIONS
# =============================================================================

@retry_step
def run_newspaper_editorial_task(**kwargs):
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')

    dropdown_toggle = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "li.dropdown-usersavedquery > a.dropdown-toggle")))
    dropdown_toggle.click()
    time.sleep(3)
    edit_saved_search_btn = wait.until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-target='#modal-saved-search-ws6']")))
    edit_saved_search_btn.click()
    wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#modal-saved-search-ws6")))
    time.sleep(3)
    editorial_item = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//ul[@class='list-group']//h5[text()='社評']/ancestor::li")))
    editorial_item.click()
    time.sleep(3)

    search_btn = None
    selectors = [(By.CSS_SELECTOR, "div.modal-footer .btn-default:last-child"),
                 (By.XPATH, "//div[@class='modal-footer']//button[text()='搜索']")]
    for selector_type, selector in selectors:
        try:
            search_btn = wait.until(EC.element_to_be_clickable((selector_type, selector)))
            break
        except TimeoutException:
            continue
    if search_btn:
        search_btn.click()
    else:
        driver.execute_script("""
            var buttons = document.querySelectorAll('div.modal-footer button');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].textContent.trim() === '搜索') {
                    buttons[i].click(); break;
                }
            }""")
    wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "#modal-saved-search-ws6")))

    if wait_for_search_results(driver=driver, wait=wait, st_module=st):
        # NEW: Scroll to load all content, then also wait for AJAX to finish
        scroll_to_load_all_content(driver=driver, st_module=st)
        wait_for_ajax_complete(driver, timeout=10)

        # Now collect all results, with retries
        articles = []
        for retry in range(3):
            results = driver.find_elements(By.CSS_SELECTOR, 'div.list-group-item.no-excerpt')
            if st:
                st.write(f"[Editorial Scrape] Attempt {retry+1}: {len(results)} items found.")
            for result in results:
                try:
                    title = result.find_element(By.CSS_SELECTOR, 'h4.list-group-item-heading a').text.strip()
                    media_name_raw = result.find_element(By.CSS_SELECTOR, 'small a').text.strip()
                    mapped_name = next((v for k, v in MEDIA_NAME_MAPPINGS.items() if k in media_name_raw), media_name_raw)
                    article = {'media': mapped_name, 'title': title}
                    if article not in articles:
                        articles.append(article)
                except Exception:
                    continue
            if len(articles) > 0:
                break
            time.sleep(2)
        return articles

    return []


@retry_step
def run_scmp_editorial_task(**kwargs):
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')

    toggle_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'div.toggle-collapse[data-toggle="collapse"]')))
    driver.execute_script("arguments[0].click();", toggle_button)
    time.sleep(2)
    my_media_dropdown_toggle = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.btn-naked.dropdown-toggle[data-toggle="dropdown"]')))
    my_media_dropdown_toggle.click()
    time.sleep(1)
    hongkong_option = wait.until(EC.element_to_be_clickable((By.XPATH, '//label[span[text()="各大香港報章"]]')))
    hongkong_option.click()
    time.sleep(1)
    author_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, 'input.form-control[placeholder="欄目"]')))
    author_input.clear()
    author_input.send_keys("editorial")
    search_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button#toggle-query-execute.btn.btn-primary')))
    search_button.click()

    # Scroll and wait for AJAX after search to maximize completeness
    scroll_to_load_all_content(driver=driver, st_module=st)
    wait_for_ajax_complete(driver, timeout=10)

    if wait_for_search_results(driver=driver, wait=wait, st_module=st):
        articles = []
        for retry in range(3):
            results = driver.find_elements(By.CSS_SELECTOR, 'div.list-group-item.no-excerpt')
            if st:
                st.write(f"[SCMP Editorial Scrape] Attempt {retry+1}: {len(results)} items found.")
            for result in results:
                try:
                    title = result.find_element(By.CSS_SELECTOR, 'h4.list-group-item-heading a').text.strip()
                    media_name_raw = result.find_element(By.CSS_SELECTOR, 'small a').text.strip()
                    mapped_name = next((v for k, v in MEDIA_NAME_MAPPINGS.items() if k in media_name_raw), None)
                    if mapped_name == 'SCMP':
                        article = {'media': 'SCMP', 'title': title}
                        if article not in articles:
                            articles.append(article)
                except Exception:
                    continue
            if len(articles) > 0:
                break
            time.sleep(2)
        return articles
    return []



@retry_step
def create_docx_report(**kwargs):
    author_articles_data = kwargs.get('author_articles_data')
    editorial_data = kwargs.get('editorial_data')
    author_list = kwargs.get('author_list')
    output_path = kwargs.get('output_path')
    st = kwargs.get('st_module')
     
    from docx import Document

    doc = Document()
    doc.add_heading('指定作者社評', level=1)
    doc.add_paragraph()
    for author in author_list:
        article = author_articles_data.get(author)
        title = article['title'] if article else ""
        doc.add_paragraph(f"{author}：{title}")
    doc.add_paragraph()
    is_first_article = True
    for author in author_list:
        article = author_articles_data.get(author)
        if article and article.get('content'):
            if not is_first_article:
                doc.add_paragraph()
            for paragraph_text in article['content'].split('\n\n'):
                doc.add_paragraph(paragraph_text)
            is_first_article = False

    if editorial_data:
        doc.add_page_break()
        doc.add_heading('報章社評', level=1)
        doc.add_paragraph()
        grouped_editorials = defaultdict(list)
        for article in editorial_data:
            grouped_editorials[article['media']].append(article['title'])

        for media, titles in grouped_editorials.items():
            if len(titles) == 1:
                doc.add_paragraph(f"{media}：{titles[0]}")
            else:
                doc.add_paragraph(f"{media}：1. {titles[0]}")
                for i, title in enumerate(titles[1:], start=2):
                    p = doc.add_paragraph()
                    p.add_run(f"\t{i}. {title}")
    
    doc.save(output_path)
    return output_path
