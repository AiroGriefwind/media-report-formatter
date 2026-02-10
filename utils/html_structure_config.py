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
    "edit_search": {
        "modal_title": [
            {"by": "css", "value": "div.modal-header > h4.modal-title"},
            {"by": "css", "value": "div.modal-header h4.modal-title"},
            {"by": "xpath", "value": "//div[contains(@class,'modal-header')]//h4[contains(@class,'modal-title') and normalize-space()='编辑搜索']"},
            {"by": "xpath", "value": "//div[contains(@class,'modal-header')]//h4[contains(@class,'modal-title') and contains(normalize-space(),'编辑搜索')]"},
            {"by": "xpath", "value": "//div[contains(@class,'modal-header')]//h4[contains(@class,'modal-title') and contains(normalize-space(),'編輯搜索')]"},
        ],
        "tag_editor": [
            {"by": "css", "value": "ul.tag-editor"},
        ],
        "close_button": [
            {"by": "css", "value": "button.close[data-dismiss='modal']"},
            {"by": "xpath", "value": "//button[@data-dismiss='modal' and contains(@class,'close')]"},
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
            ],
            "column": [
                {"by": "css", "value": "input.form-control[data-placeholder='true'][placeholder='栏目']"},
                {"by": "css", "value": "input.form-control[placeholder='栏目']"},
                {"by": "css", "value": "input.form-control[placeholder='欄目']"},
            ],
            "page": [
                {"by": "css", "value": "input.form-control[data-placeholder='true'][placeholder='版面']"},
                {"by": "css", "value": "input.form-control[placeholder='版面']"},
            ],
        },
    },
    "timeout": {
        "url": "https://wisesearch6.wisers.net/wevo/timeout",
        "title": [
            {"by": "css", "value": "h4"},
        ],
        "logout_button": [
            {"by": "css", "value": "button.btn.btn-primary.btn-block"},
            {"by": "xpath", "value": "//button[contains(@class,'btn-primary') and contains(@class,'btn-block') and contains(.,'登出')]"},
        ],
    },
}
