
# =============================================================================
# INTERNATIONAL NEWS SPECIFIC FUNCTIONS
# =============================================================================

import re
import time

import tempfile
from datetime import datetime
from docx import Document

from .web_scraping_utils import retry_step, wait_for_search_results, scroll_to_load_all_content, wait_for_ajax_complete
from .document_utils import setup_document_fonts, add_end_marker
from .config import MEDIA_NAME_MAPPINGS

# Web scraping imports
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from .wisers_utils import (
    retry_step, 
    wait_for_search_results, 
    scroll_to_load_all_content, 
    wait_for_ajax_complete,
)

@retry_step
def run_international_news_task(**kwargs):
    """Search for international news articles with fallback mechanisms"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    st = kwargs.get('st_module')
    
    try:
        # Try the saved search approach first
        dropdown_toggle = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "li.dropdown-usersavedquery > a.dropdown-toggle")))
        dropdown_toggle.click()
        time.sleep(3)

        edit_saved_search_btn = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button[data-target='#modal-saved-search-ws6']")))
        edit_saved_search_btn.click()
        wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "#modal-saved-search-ws6")))
        time.sleep(3)

        # Look for "國際新聞" in the saved searches
        try:
            international_item = wait.until(
                EC.element_to_be_clickable((By.XPATH, "//ul[@class='list-group']//h5[text()='國際新聞']/ancestor::li")))
            international_item.click()
            time.sleep(3)
            
            if st:
                st.write("✅ Found '國際新聞' saved search")
        
        except TimeoutException:
            if st:
                st.warning("⚠️ '國際新聞' saved search not found. Checking available searches...")
            
            # List all available saved searches for debugging
            try:
                search_items = driver.find_elements(By.XPATH, "//ul[@class='list-group']//h5")
                available_searches = [item.text.strip() for item in search_items if item.text.strip()]
                if st and available_searches:
                    st.info(f"Available saved searches: {', '.join(available_searches)}")
            except:
                pass
            
            # Close modal and return empty result
            try:
                close_btn = driver.find_element(By.CSS_SELECTOR, "#modal-saved-search-ws6 .close")
                close_btn.click()
                time.sleep(2)
            except:
                pass
            
            return []

        # Click search button
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

        # Wait for modal to close
        wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "#modal-saved-search-ws6")))
        
        # Wait 15 seconds for search results to fully load
        if st:
            st.write("⏳ Waiting 15 seconds for search results to fully load...")
        time.sleep(15)

        if wait_for_search_results(driver=driver, wait=wait, st_module=st):
            # Scroll to load all content and wait for AJAX
            scroll_to_load_all_content(driver=driver, st_module=st)
            wait_for_ajax_complete(driver, timeout=10)
            
            # Get all article links for scraping
            articles_data = []
            
            for retry in range(3):
                results = driver.find_elements(By.CSS_SELECTOR, 'div.list-group-item.no-excerpt')
                if st:
                    st.write(f"[International News Scrape] Attempt {retry+1}: {len(results)} items found.")
                
                # Limit to around 80-100 articles as requested
                results = results[:100]
                
                for i, result in enumerate(results):
                    try:
                        title_element = result.find_element(By.CSS_SELECTOR, 'h4.list-group-item-heading a')
                        title = title_element.text.strip()
                        article_url = title_element.get_attribute('href')
                        
                        # Get media name
                        try:
                            media_name_raw = result.find_element(By.CSS_SELECTOR, 'small a').text.strip()
                            mapped_name = next((v for k, v in MEDIA_NAME_MAPPINGS.items() if k in media_name_raw), media_name_raw)
                        except:
                            mapped_name = "Unknown"
                        
                        articles_data.append({
                            'title': title,
                            'url': article_url,
                            'media': mapped_name,
                            'index': i
                        })
                        
                    except Exception as e:
                        if st:
                            st.warning(f"Error extracting article {i}: {e}")
                        continue
                
                if len(articles_data) > 0:
                    break
                time.sleep(2)
            
            return articles_data
        
        return []
        
    except Exception as e:
        if st:
            st.error(f"Error in international news search: {e}")
        return []

def should_scrape_article_based_on_metadata(metadata_text, min_words=200, max_words=1000):
    """Determine if article should be scraped based on metadata - filter out opinion pieces and articles outside word range"""
    if not metadata_text:
        return True  # If no metadata, let it pass to avoid missing articles
    
    # Filter out known opinion/editorial keywords
    opinion_keywords = [
        '社評', '評論', '觀點', '專欄', '分析', '時評', '評論員', '觀察', 
        '社論', '筆記', '隨筆', '札記', '感言', '思考', '反思', '見解',
        'editorial', 'opinion', 'commentary', 'analysis'
    ]
    
    for keyword in opinion_keywords:
        if keyword in metadata_text:
            return False
    
    # Extract word count from metadata
    import re
    word_count_matches = re.findall(r'\|\s*(\d+)\s*字\s*\|', metadata_text)
    if not word_count_matches:
        match = re.search(r'(\d+)\s*字', metadata_text)
        if match:
            word_count = int(match.group(1))
        else:
            return True  # If no word count found, accept by default
    else:
        word_count = int(word_count_matches[0])
    
    # ✅ UPDATED: Filter articles outside the word count range
    if word_count < min_words or word_count > max_words:
        return False
    
    return True

def create_hover_preview_report(**kwargs):
    """
    Create a Word document report from hover preview list.
    Expected kwargs:
    - preview_data: list of dict with 'title', 'hover_html', 'hover_text', 'metadata_line'
    - output_path: path to save docx file
    - st_module: optional Streamlit module for logging
    """
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
    import re
    from .config import MEDIA_NAME_MAPPINGS 

    preview_data = kwargs.get('preview_data', [])
    output_path = kwargs.get('output_path')
    st = kwargs.get('st_module')

    doc = Document()

    # Add title
    title = doc.add_heading('國際新聞 - 懸停預覽報告', level=1)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER

    # Add date
    doc.add_paragraph(f"生成日期: {datetime.now().strftime('%Y年%m月%d日')}")\
        .alignment = WD_PARAGRAPH_ALIGNMENT.CENTER

    doc.add_paragraph(f"總共找到 {len(preview_data)} 篇文章")
    doc.add_paragraph()

    for item in preview_data:
        # 1. Title (No numbering, just title)
        article_title = item.get('title', 'Unknown')
        doc.add_heading(article_title, level=2)

        # 2. Metadata Line (Date | Media | Words)
        raw_meta = item.get('metadata_line', '')
        formatted_meta = raw_meta  # Default fallback

        # Try to parse and format metadata
        # Pattern looks for: Date ... Media ... WordCount
        date_match = re.search(r'(\d{4}[-.]\d{2}[-.]\d{2})', raw_meta)
        word_match = re.search(r'(\d+)\s*字', raw_meta)

        if date_match:
            date_str = date_match.group(1)
            word_str = f"{word_match.group(1)} 字" if word_match else ""
            
            # Extract Media Name by removing date and words
            # Also remove pipe symbols if they exist in raw
            media_part = raw_meta
            media_part = media_part.replace(date_str, '')
            if word_match:
                media_part = media_part.replace(word_match.group(0), '')
            media_part = media_part.replace('|', '').strip()
            
            # Map Media Name (Long -> Short)
            mapped_media = media_part
            # Check exact matches first
            if media_part in MEDIA_NAME_MAPPINGS:
                mapped_media = MEDIA_NAME_MAPPINGS[media_part]
            else:
                # Check substring matches (e.g. '信報財經' -> '信報')
                for k, v in MEDIA_NAME_MAPPINGS.items():
                    if k in media_part:
                        mapped_media = v
                        break
            
            # Construct new format: Date | Media | Words
            parts = [date_str, mapped_media, word_str]
            formatted_meta = " | ".join([p for p in parts if p])

        # Add Metadata Paragraph
        meta_para = doc.add_paragraph(formatted_meta)
        # Optional: Make it look slightly different (e.g. smaller text)
        # meta_para.style = doc.styles['No Spacing'] 

        # 3. Content Body (Cleaned)
        # Get text from hover_text or strip HTML from hover_html
        content_text = item.get('hover_text', '')
        if not content_text:
            import re
            clean_html = re.sub('<[^<]+?>', '\n', item.get('hover_html', ''))
            content_text = clean_html

        # Clean up content: remove Title and Metadata if they appear at the start
        lines = content_text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            if not line: continue
            
            # Remove if line is identical to title
            if line == article_title.strip():
                continue
                
            # Remove if line looks like metadata (contains date and '字')
            if re.search(r'\d{4}[-.]\d{2}[-.]\d{2}', line) and '字' in line:
                continue
                
            cleaned_lines.append(line)
            
        final_content = '\n'.join(cleaned_lines)
        doc.add_paragraph(final_content)

        # Separator
        doc.add_paragraph()

    doc.save(output_path)

    if st:
        st.write(f"✅ Report saved to {output_path}")
    
    return output_path



@retry_step
def scrape_international_articles_sequentially(**kwargs):
    """Scrape international articles with pre-filtering for news only and word count limit"""
    driver = kwargs.get('driver')
    wait = kwargs.get('wait')
    max_articles = kwargs.get('max_articles', 100)
    max_words = kwargs.get('max_words', 1000)
    st = kwargs.get('st_module')

    scraped_articles = []
    skipped_articles = []
    original_window = driver.current_window_handle

    # Get all article elements from current search results page
    results = driver.find_elements(By.CSS_SELECTOR, 'div.list-group-item.no-excerpt')
    results = results[:max_articles]  # Limit to max articles

    if st:
        st.write(f"Found {len(results)} articles to filter and scrape sequentially")

    scraped_count = 0
    for idx in range(len(results)):
        try:
            # IMPORTANT: Re-find elements each iteration because DOM may change
            current_results = driver.find_elements(By.CSS_SELECTOR, 'div.list-group-item.no-excerpt')
            if idx >= len(current_results):
                break

            result = current_results[idx]

            # ✅ NEW: Pre-filter using metadata from search results page
            try:
                metadata_preview = result.find_element(By.CSS_SELECTOR, 'small').text.strip()
                title_element = result.find_element(By.CSS_SELECTOR, 'h4.list-group-item-heading a')
                title_preview = title_element.text.strip()
            except:
                metadata_preview = None
                title_preview = f"Article {idx+1}"

            # Apply filter before scraping
            if metadata_preview and not should_scrape_article_based_on_metadata(metadata_preview, max_words=max_words):
                if st:
                    # Extract word count for logging
                    word_match = re.search(r'(\d+)\s*字', metadata_preview)
                    word_count = word_match.group(1) if word_match else "unknown"
                    st.write(f"⏭️ Skipping article {idx+1}/{len(results)} (Word count: {word_count}, Title: {title_preview[:30]}...)")
                
                skipped_articles.append({
                    'title': title_preview,
                    'metadata': metadata_preview,
                    'reason': 'Opinion piece or over word limit'
                })
                continue

            if st:
                st.write(f"✅ Scraping article {scraped_count+1} (Index {idx+1}/{len(results)}): {title_preview[:50]}...")

            # Click the article link (same as author search)
            article_link = result.find_element(By.CSS_SELECTOR, 'h4.list-group-item-heading a')
            article_link.click()

            # Wait for new window and switch to it (same as author search)
            wait.until(EC.number_of_windows_to_be(2))
            for window_handle in driver.window_handles:
                if window_handle != original_window:
                    driver.switch_to.window(window_handle)
                    break

            # Scrape content from article detail page
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.article-detail')))
            time.sleep(2)

            # Extract title from article page
            title = driver.find_element(By.CSS_SELECTOR, 'h3').text.strip()

            # Extract full metadata from article page
            try:
                full_metadata_element = driver.find_element(By.CSS_SELECTOR, 'div.article-subheading')
                full_metadata_line = full_metadata_element.text.strip()
            except:
                full_metadata_line = metadata_preview or "Unknown Source"

            # Extract article content
            paragraphs = []
            content_elements = driver.find_elements(By.CSS_SELECTOR, 'div.description p')
            for p in content_elements:
                text = p.text.strip()
                if text:
                    paragraphs.append(text)

            content_body = '\n\n'.join(paragraphs) if paragraphs else ""

            article_data = {
                'title': title,
                'metadata_line': full_metadata_line,
                'content': content_body,
                'full_text': f"{title}\n\n{full_metadata_line}\n\n{content_body}" if content_body else f"{title}\n\n{full_metadata_line}"
            }

            scraped_articles.append(article_data)
            scraped_count += 1

            # Close current tab and return to original window (same as author search)
            driver.close()
            driver.switch_to.window(original_window)

            time.sleep(1)  # Brief pause between articles

        except Exception as e:
            if st:
                st.warning(f"Failed to scrape article {idx+1}: {e}")

            # Ensure we're back on the original window
            try:
                driver.switch_to.window(original_window)
            except:
                pass
            continue

    # Log filtering results
    if st:
        st.write(f"📊 Filtering Summary:")
        st.write(f"• Total articles found: {len(results)}")
        st.write(f"• Articles scraped: {len(scraped_articles)}")
        st.write(f"• Articles skipped: {len(skipped_articles)}")

    return scraped_articles, skipped_articles


@retry_step
def create_international_news_report(**kwargs):
    """Create Word document report for international news"""
    articles_data = kwargs.get('articles_data')
    output_path = kwargs.get('output_path')
    st = kwargs.get('st_module')

    from docx import Document

    doc = Document()
    setup_document_fonts(doc)

    # Add title
    doc.add_heading('國際新聞摘要', level=1)
    doc.add_paragraph()

    # Add date
    today_str = datetime.now().strftime("%Y年%m月%d日")
    date_para = doc.add_paragraph(f"日期：{today_str}")
    date_para.add_run().add_break()

    # Add articles
    for i, article in enumerate(articles_data, 1):
        if article and article.get('full_text'):
            # Add article number and title
            title_para = doc.add_paragraph()
            title_run = title_para.add_run(f"{i}. {article['title']}")
            title_run.bold = True

            # ✅ CHANGED: Add full metadata line from article page
            if article.get('metadata_line'):
                metadata_para = doc.add_paragraph(article['metadata_line'])
                metadata_para.style = doc.styles['Normal']

            # Add content
            if article.get('content'):
                for paragraph_text in article['content'].split('\n\n'):
                    if paragraph_text.strip():
                        doc.add_paragraph(paragraph_text.strip())

            # Add spacing between articles
            doc.add_paragraph()

    # Add end marker
    add_end_marker(doc)

    doc.save(output_path)
    return output_path

