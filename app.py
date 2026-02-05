import streamlit as st
import os  # Add this import

from utils.firebase_logging import patch_streamlit_logging, ensure_logger

patch_streamlit_logging(st)  # mirrors st.* to Firebase

# Import tab render functions
from tabs.document_formatting import render_document_formatting_tab
from tabs.web_scraping import render_web_scraping_tab
from tabs.web_scraping_persisted import render_web_scraping_persisted_tab
from tabs.international_news import render_international_news_tab
from tabs.saved_search_news import render_greater_china_keywords_tab, render_hong_kong_politics_news_tab
from tabs.hong_kong_keyword_search import (
    render_hong_kong_keyword_search_tab,
    render_international_keyword_search_tab,
    render_greater_china_keyword_search_tab,
    render_multi_keyword_search_tab,
)

def get_app_title():
    """Get the appropriate app title based on environment"""
    version = os.getenv('APP_VERSION', 'stable')  # defaults to stable
    
    if version == 'beta':
        return "AsiaNet Document Processing Tool (Beta)"
    else:
        return "AsiaNet Document Processing Tool"

def main():
    """Main application entry point"""
    
    # Single page configuration - REMOVE DUPLICATES
    st.set_page_config(
        page_title=get_app_title(), 
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Initialize Firebase logger AFTER page config
    ensure_logger(st, run_context={"app": "asianet-tool", "session": st.session_state.get("session_id")})
    
    # Check secrets configuration and show warnings if needed
    _check_secrets_configuration()
    
    # Main app header - use dynamic title
    st.title(get_app_title())
    st.markdown("Choose between document formatting or web scraping functionality")
    
    # Create tabs
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs([
        "📄 Document Formatting", 
        "🌐 Web Scraping & Reporting", 
        "🌍 International News",
        "🇭🇰 香港政治新聞",
        "🀄 大中華關鍵詞",
        "🧭 Web Scraping (Firebase)",
        "🇭🇰 香港政治（關鍵詞直搜）",
        "🌍 國際新聞（關鍵詞直搜）",
        "🀄 大中華新聞（關鍵詞直搜）",
        "🚦 一鍵三板塊（關鍵詞直搜）",
    ])
    
    # Render each tab
    with tab1:
        render_document_formatting_tab()
    
    with tab2:
        render_web_scraping_tab()
    
    with tab3:
        render_international_news_tab()

    with tab4:
        render_hong_kong_politics_news_tab()

    with tab5:
        render_greater_china_keywords_tab()

    with tab6:
        render_web_scraping_persisted_tab()

    with tab7:
        render_hong_kong_keyword_search_tab()

    with tab8:
        render_international_keyword_search_tab()

    with tab9:
        render_greater_china_keyword_search_tab()

    with tab10:
        render_multi_keyword_search_tab()

def _check_secrets_configuration():
    """Check if secrets are configured and show appropriate warnings"""
    try:
        if not st.secrets.get("wisers", {}).get("api_key"):
            st.warning("⚠️ Secrets not configured. Manual input will be required for web scraping.")
    except Exception as e:
        if isinstance(e, st.errors.StreamlitAPIException):
            st.warning("⚠️ Secrets not configured locally. Manual input required.")
        else:
            st.warning(f"Error checking secrets: {e}")

if __name__ == "__main__":
    main()
