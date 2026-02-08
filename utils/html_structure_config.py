"""
Centralized HTML structure hints for Wisers UI.
Group selectors by page type for easier maintenance.
"""

HTML_STRUCTURE = {
    "home": {
        "media_filter_container_selector": (
            "#accordion-queryfilter > div.panel.panel-default.panel-queryfilter-scope-publisher "
            "> div.panel-collapse.collapse.in > div > div:nth-child(3)"
        ),
        "media_filter_keep_labels": ["報刊", "綜合新聞", "香港"],
        "media_author_panel_toggles": [
            {"by": "xpath", "value": "//div[contains(@class,'toggle-collapse') and .//span[contains(normalize-space(),'媒體/作者')]]"},
            {"by": "css", "value": "div.toggle-collapse[data-toggle='collapse']"},
        ],
        "media_author_panel_states": {
            "collapsed": [
                {"by": "css", "value": "div.toggle-collapse.collapsed[data-toggle='collapse']"},
                {"by": "xpath", "value": "//div[@role='button' and contains(@class,'toggle-collapse') and contains(@class,'collapsed')]"},
            ],
            "expanded": [
                {"by": "css", "value": "div.toggle-collapse[data-toggle='collapse']:not(.collapsed)"},
                {"by": "xpath", "value": "//div[@role='button' and contains(@class,'toggle-collapse') and not(contains(@class,'collapsed'))]"},
            ],
        },
        "inputs": {
            "author": [
                {"by": "css", "value": "input.form-control[data-placeholder='true'][placeholder='作者']"},
                {"by": "css", "value": "input.form-control[placeholder='作者']"},
                {"by": "css", "value": "input[placeholder*='作者']"},
                {"by": "xpath", "value": "//label[contains(normalize-space(.),'作者')]//input"},
            ],
            "column": [
                {"by": "css", "value": "input.form-control[data-placeholder='true'][placeholder='作者']"},
                {"by": "css", "value": "input.form-control[placeholder='作者']"},
                {"by": "css", "value": "input[placeholder*='作者']"},
                {"by": "xpath", "value": "//label[contains(normalize-space(.),'欄目') or contains(normalize-space(.),'栏目')]//input"},
            ],
            "page": [
                {"by": "css", "value": "input.form-control[data-placeholder='true'][placeholder='作者']"},
                {"by": "css", "value": "input.form-control[placeholder='作者']"},
                {"by": "css", "value": "input[placeholder*='作者']"},
                {"by": "xpath", "value": "//label[contains(normalize-space(.),'版面')]//input"},
            ],
            "media_author": [
                {"by": "css", "value": "#label-search-media2 input[placeholder*='媒体']"},
                {"by": "css", "value": "#label-search-media2 input[placeholder*='媒體']"},
                {"by": "css", "value": "#label-search-media2 input"},
            ],
        },
    },
    "search_results": {},
    "edit_search": {},
}
