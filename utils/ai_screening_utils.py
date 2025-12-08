import json
import time
import re
from openai import OpenAI
import streamlit as st

# 硬編碼配置
KIMI_API_KEY = "sk-Chcr24Sj5v69WmlQ614vxqzwkn13vA3czyGkaX4J5wspFZkA"
KIMI_BASE_URL = "https://api.moonshot.cn/v1"

# 地點排序權重
LOCATION_ORDER = {
    'United States': 0,
    'Russia': 1,
    'Europe': 2,
    'Middle East': 3,
    'Southeast Asia': 4,
    'Japan': 5,
    'Korea': 6,
    'China': 7,
    'Others': 8,
    'Tech': 9
}

def get_ai_client():
    return OpenAI(api_key=KIMI_API_KEY, base_url=KIMI_BASE_URL)

def normalize_location(location):
    """Normalize location string to standard categories"""
    loc_lower = location.lower()
    if 'united states' in loc_lower or 'us' in loc_lower or 'usa' in loc_lower or 'america' in loc_lower:
        return 'United States'
    elif 'russia' in loc_lower or 'ukraine' in loc_lower:
        return 'Russia'
    elif 'europe' in loc_lower or 'eu' in loc_lower or 'uk' in loc_lower or 'germany' in loc_lower or 'france' in loc_lower:
        return 'Europe'
    elif 'middle east' in loc_lower or 'israel' in loc_lower or 'iran' in loc_lower or 'gaza' in loc_lower:
        return 'Middle East'
    elif 'southeast asia' in loc_lower or 'asean' in loc_lower or 'philippines' in loc_lower or 'vietnam' in loc_lower or 'indonesia' in loc_lower or 'singapore' in loc_lower or 'malaysia' in loc_lower or 'thailand' in loc_lower or 'myanmar' in loc_lower:
        return 'Southeast Asia'
    elif 'japan' in loc_lower:
        return 'Japan'
    elif 'korea' in loc_lower:
        return 'Korea'
    elif 'china' in loc_lower:
        return 'China'
    else:
        return 'Others'

def analyze_article_with_ai(client, article_data):
    """
    Send single article to AI for analysis.
    article_data expects: {'title': str, 'content': str}
    """
    title = article_data.get('title', 'No Title')
    # 使用 hover_text 作為內容，如果沒有則使用 title
    content = article_data.get('hover_text') or article_data.get('title')
    
    # 截取內容以防過長
    content_snippet = content[:2000]

    system_prompt = """You are a professional news editor for "Asia Net". Your goal is to select and categorize "Hard News" from international sources.
    
    Guidelines:
    1. Score the article from 0-30 based on importance and relevance (Hard News = High Score).
    2. Identify the Main Location: United States, Russia, Europe, Middle East, Southeast Asia, Japan, Korea, China, Others.
    3. Summarize the topic in 2-4 words (Topic Key).
    4. Flag if it is "Tech News" (AI, Chips, Satellites, etc.).
    
    Output JSON format only:
    {
        "overall_score": <int 0-30>,
        "is_hard_news": <bool>,
        "main_location": <str>,
        "topic_key": <str>,
        "is_tech_news": <bool>,
        "reason": <str>
    }
    """

    user_prompt = f"""Please analyze this news article:
    Title: {title}
    Content: {content_snippet}
    
    Return only the JSON object.
    """

    try:
        completion = client.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        result = completion.choices[0].message.content
        return json.loads(result)
    except Exception as e:
        print(f"AI Error: {e}")
        # Return fallback default
        return {
            "overall_score": 0,
            "is_hard_news": False,
            "main_location": "Others",
            "topic_key": "Error",
            "is_tech_news": False,
            "reason": str(e)
        }

def run_ai_screening(articles_list, progress_callback=None):
    """
    Main function to screen a list of articles.
    articles_list: List of dicts [{'title':..., 'hover_text':..., 'original_index':...}]
    """
    client = get_ai_client()
    analyzed_results = []
    
    total = len(articles_list)
    
    for i, article in enumerate(articles_list):
        if progress_callback:
            progress_callback(i, total, article['title'])
            
        analysis = analyze_article_with_ai(client, article)
        
        # Merge analysis into the article dict
        enhanced_article = article.copy()
        enhanced_article['ai_analysis'] = analysis
        
        # Normalize location for sorting
        loc = normalize_location(analysis.get('main_location', 'Others'))
        enhanced_article['ai_analysis']['normalized_location'] = loc
        
        # Only keep Hard News with score > 15 (Adjustable threshold)
        # Or keep everything but mark them for the UI to filter
        analyzed_results.append(enhanced_article)
        
        time.sleep(1.0) # Rate limiting
        
    # Sort Logic
    def get_sort_key(item):
        analysis = item['ai_analysis']
        
        # Tech news goes to bottom (order 9)
        if analysis.get('is_tech_news', False):
            return (9, -analysis.get('overall_score', 0))
            
        loc = analysis.get('normalized_location', 'Others')
        order = LOCATION_ORDER.get(loc, 8)
        
        # Sort by: Location Order ASC, then Score DESC
        return (order, -analysis.get('overall_score', 0))

    sorted_results = sorted(analyzed_results, key=get_sort_key)
    
    return sorted_results
