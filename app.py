import streamlit as st

# Import tab render functions
from tabs.document_formatting import render_document_formatting_tab
from tabs.web_scraping import render_web_scraping_tab
from tabs.international_news import render_international_news_tab

def main():
    """Main application entry point"""
    # Page configuration
    st.set_page_config(
        page_title="AsiaNet Document Processing Tool", 
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # Check secrets configuration and show warnings if needed
    _check_secrets_configuration()
    
    # Main app header
    st.title("AsiaNet Document Processing Tool")
    st.markdown("Choose between document formatting or web scraping functionality")
    
    # Create tabs
    tab1, tab2, tab3 = st.tabs([
        "📄 Document Formatting", 
        "🌐 Web Scraping & Reporting", 
        "🌍 International News"
    ])
    
    # Render each tab
    with tab1:
        render_document_formatting_tab()
    
    with tab2:
        render_web_scraping_tab()
    
    with tab3:
        render_international_news_tab()

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
