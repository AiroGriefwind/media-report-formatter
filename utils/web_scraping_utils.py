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
TAB_BAR_ZEROS_XPATH = (
    "//ul[contains(@class,'nav-tabs') and contains(@class,'navbar-nav-pub')]"
    "/li//span[normalize-space(text())='(0)']"
)

def _detect_no_article_banner(driver):
    """
    Return True if a 'no article' or 'no data' banner exists.
    """
    try:
        els = driver.find_elements(
            By.XPATH,
            "//h5[contains(text(),'没有文章') or contains(text(),'沒有文章')] | //div[contains(@class,'empty-result')] | //div[contains(@class,'no-results')]"
        )
        if els:
            print("Detected empty result banner:", [el.text for el in els])
        return len(els) > 0
    except Exception as e:
        print("Exception while detecting no-article banner:", e)
        return False


def _results_are_empty(driver) -> bool:
    """
    Return True if all main (top-level, non-dropdown) tab counters show "(0)".
    Logs each tab/counter for diagnostics.
    """
    try:
        bar = driver.find_element(
            By.XPATH,
            "//ul[contains(@class,'nav-tabs') and contains(@class,'navbar-nav-pub')]"
        )
        # Only non-dropdown tabs
        items = bar.find_elements(By.XPATH, "./li[not(contains(@class,'dropdown'))]")

        total = 0
        zeros = 0
        debug_lines = []
        for idx, li in enumerate(items):
            # Find the first <span>(n)</span> under <a>
            s_list = li.find_elements(By.XPATH, "./a/span")
            txts = [s.text for s in s_list]
            found = False
            for s in s_list:
                txt = s.text.strip()
                debug_lines.append(f"Tab idx={idx}, label={li.text!r}, span_text={txt!r}")
                if txt.startswith("(") and txt.endswith(")"):
                    total += 1
                    if txt == "(0)":
                        zeros += 1
                    found = True
                    break
            if not found:
                debug_lines.append(f"Tab idx={idx} had no <a><span> matching (n)!")
        print("\n".join(debug_lines))
        print(f"Tab counter summary: {zeros} of {total} tabs are (0)")
        return total > 0 and total == zeros
    except Exception as e:
        print("Error in _results_are_empty:", e)
        return False



    
def _dump_tab_counters(driver, st):
    try:
        bar = driver.find_element(
            By.XPATH,
            "//ul[contains(@class,'nav-tabs') and contains(@class,'navbar-nav-pub')]"
        )
        items = bar.find_elements(By.XPATH, "./li[not(contains(@class,'dropdown'))]")
        counts = []
        for li in items:
            spans = li.find_elements(By.TAG_NAME, "span")
            for s in spans:
                txt = s.text.strip()
                if txt.startswith("(") and txt.endswith(")"):
                    label = li.text.split("\n")[0].strip()
                    counts.append(f"{label} {txt}")
        st.write("▶️ Top tab counters: " + " | ".join(counts))
    except Exception as e:
        st.warning(f"Could not read tab counters: {e}")


def _debug_tab_bar(driver, st):
    """
    Write the raw outerHTML of the results tab-bar and each child count.
    """
    try:
        bar = driver.find_element(
            By.XPATH,
            "//ul[contains(@class,'nav-tabs') and contains(@class,'navbar-nav-pub')]"
        )
        st.write("🔍 Raw tab-bar HTML:")
        st.code(bar.get_attribute("outerHTML"))
        items = bar.find_elements(By.TAG_NAME, "li")
        counts = []
        for li in items:
            # get the visible number inside parentheses
            txt = li.text.replace("\n", " ").strip()
            counts.append(txt)
        st.write(f"🔢 Parsed tabs: {counts}")
    except Exception as e:
        st.warning(f"Could not debug tab-bar: {e}")


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
    Wait up to 12 s for either:
      • a clickable headline, **or**
      • the tab-bar to render with all-zero counters.
    Returns True if headlines exist, False if counters are all zero.
    """
    try:
        driver = kwargs["driver"]
        st     = kwargs.get("st_module")

        headline_cond = EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "div.list-group .list-group-item h4 a")
        )

        def _ready(d):
            ready_headline = headline_cond(d)
            ready_empty = _results_are_empty(d)
            ready_no_article = _detect_no_article_banner(d)
            print(f"_ready: headline={ready_headline} zeroed_tabs={ready_empty} no_article_banner={ready_no_article}")
            return ready_headline or ready_empty or ready_no_article


        WebDriverWait(driver, 12).until(_ready)

        

        empty = _results_are_empty(driver)
        noarticle = _detect_no_article_banner(driver)

        if st:
            if empty:
                st.info("ℹ️ All tab counters are 0 – no articles.")
            elif noarticle:
                st.info("ℹ️ No-article banner detected – no articles.")
            else:
                st.write("✅ Headlines present – results found.")
        return not (empty or noarticle)

    except TimeoutException:
        print("TimeoutException! Dumping tab bar state for diagnostics:")
        if st:
            st.warning("TimeoutException! Dumping tab bar state for diagnostics:")
        try:
            bar = driver.find_element(
                By.XPATH,
                "//ul[contains(@class,'nav-tabs') and contains(@class,'navbar-nav-pub')]"
            )
            tab_html = bar.get_attribute("outerHTML")
            print("Tab bar HTML:\n", tab_html)
            if st:
                st.code(tab_html, language='html')
        except Exception as ex:
            print("Could not locate tab bar for dumping HTML:", ex)
            if st:
                st.error(f"Could not locate tab bar for dumping HTML: {ex}")
        try:
            main_panel = driver.find_element(By.CSS_SELECTOR, "body")
            html = main_panel.get_attribute("outerHTML")
            print("BODY outerHTML (truncated):\n", html[:4000])
            if st:
                st.code("BODY\n" + html[:4000], language='html')
        except Exception as ex:
            print("Could not get body outerHTML:", ex)
            if st:
                st.error(f"Could not get body outerHTML: {ex}")
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
