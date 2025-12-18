# =============================================================================
# CONFIGURATION CONSTANTS
# =============================================================================

# Document correction mappings
CORRECTION_MAP = {
    "餘錦賢": "余錦賢",
    "嘉裡": "嘉里",
    "嘉裏": "嘉里"
    # Add other corrections here, e.g., "错误词": "正确词"
}

# Editorial media order
EDITORIAL_MEDIA_ORDER = [
    '商報', '大公', '文匯', '東方', '星島', '信報', '明報', '經濟', '成報', '頭條', 'am730', 'SCMP'
]

# Universal media name mappings
MEDIA_NAME_MAPPINGS = {
    '信報財經新聞': '信報', '信報': '信報', '明報': '明報', '頭條日報': '頭條', '文匯報': '文匯', '成報': '成報',
    '香港經濟日報': '經濟', '經濟日報': '經濟', '東方日報': '東方', '香港商報': '商報', '商報': '商報', '大公報': '大公',
    '星島日報': '星島', 'Am730': 'am730', 'am730': 'am730', '南華早報': 'SCMP', 'SCMP': 'SCMP'
}

# Editorial media names
EDITORIAL_MEDIA_NAMES = [
    '信報', '明報', '頭條', '文匯', '成報', '經濟', '東方', '商報', '大公', '星島', 'am730', 'SCMP'
]

# Web scraping URL
WISERS_URL = 'https://login.wisers.net/'

# Global list for title modifications (used by document processing)
TITLE_MODIFICATIONS = []

# Location order for international news
LOCATION_ORDER = [
                    "United States", "Russia", "Europe", "Middle East", 
                    "Southeast Asia", "Japan", "Korea", "China", "Others", "Tech News"
                ]