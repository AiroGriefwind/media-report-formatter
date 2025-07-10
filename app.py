import streamlit as st
import tempfile
import os
from io import BytesIO

## Import all existing functions from format.py
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import re
import json

# imported for handling header/footer and alignment
from docx.shared import Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER

# Input/Output Folder Management
import os
import glob
from pathlib import Path

# For date handling
from datetime import datetime

# For Simplified Chinese to Traditional Chinese conversion
from opencc import OpenCC

## Pasting the existing functions from format.py 
# Editorial media order
EDITORIAL_MEDIA_ORDER = [
    '商報', '文匯', '大公', '東方', '星島', '明報', '頭條', '經濟', '成報', 'am730', 'SCMP'
]
# Hardcoded media name mappings
MEDIA_NAME_MAPPINGS = {
    '信報': '信報', '明報': '明報', '頭條日報': '頭條', '文匯報': '文匯', '成報': '成報',
    '經濟日報': '經濟', '東方日報': '東方', '商報': '商報', '大公報': '大公',
    '星島日報': '星島', 'Am730': 'am730', 'SCMP': 'SCMP'
}
# Hardcoded list of editorial media names (short versions as they appear in documents)
EDITORIAL_MEDIA_NAMES = [
    '信報', '明報', '頭條', '文匯', '成報', '經濟', '東方', '商報', '大公', '星島', 'am730', 'SCMP'
]

# Global list to store title format modifications for logging
TITLE_MODIFICATIONS = []

def is_source_citation(text):
    if not text: return False
    if ']' in text and text.index(']') < 30: return False
    if re.match(r'^.{1,20}[:：]', text): return True
    common_media_prefixes = "|".join(re.escape(k) for k in MEDIA_NAME_MAPPINGS.keys())
    if re.match(rf'^({common_media_prefixes})\s*[:：]', text): return True
    return False

def is_valid_headline(text):
    """
    Validates if a line of text could be a headline with stricter rules.
    - Must have minimum length.
    - NEW: Cannot contain commas (，,) or a Chinese period (。) anywhere.
    - Cannot end with general sentence-ending punctuation (English period, ?, !).
    - Cannot contain brackets.
    """
    if not text or len(text.strip()) < 5:
        return False

    # --- NEW GATEKEEPER LOGIC ---
    # A true title should not contain commas or a Chinese period.
    # This acts as a strong filter against full sentences being misidentified as titles.
    if re.search(r'[，,。]', text):
        return False

    # --- EXISTING LOGIC (Slightly Refined) ---
    # Rejects if it ENDS in sentence punctuation (English period, ?, !).
    # We don't need to check for Chinese period/commas here anymore, as the rule above is stricter.
    # It correctly allows an English period if it's not at the very end (e.g., in "1.2 million USD").
    if re.search(r'[.?!]$', text.strip()):
        return False

    # Rejects if it contains brackets (often part of citations or side notes)
    if ']' in text:
        return False

    return True


def add_first_page_header(doc, logo_path):
    """
    Add header only on the first page with left text and right-aligned logo.
    Font size 18, Chinese font 標楷體.
    """
    section = doc.sections[0]
    section.different_first_page_header_footer = True  # Enable different first page header/footer
    
    header = section.first_page_header
    header_para = header.paragraphs[0]
    
    # Clear any existing content
    header_para.clear()
    
    # Add left text
    left_run = header_para.add_run("亞聯每日報章摘要")
    left_run.font.name = 'Calibri'  # English font fallback
    left_run._element.rPr.rFonts.set(qn('w:eastAsia'), '標楷體')  # Chinese font
    left_run.font.size = Pt(18)
    
    # Set tab stop at right margin (e.g., 16 cm from left)
    tab_stops = header_para.paragraph_format.tab_stops
    tab_stops.clear_all()
    tab_stops.add_tab_stop(Cm(16), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.SPACES)
    
    # Add tab character to jump to right tab stop
    header_para.add_run("\t\t")  # Two tabs to ensure alignment
    
    # Add logo picture aligned right
    logo_run = header_para.add_run()
    logo_run.add_picture(logo_path, width=Cm(5.95), height=Cm(2.04))
    
    # Optional: set paragraph style to Header for consistency
    header_para.style = doc.styles['Header']

    print("First page header added with font size 18 and logo.")

def add_first_page_footer(doc):
    """
    Add footer only on the first page with left-aligned text, font size 12
    """
    section = doc.sections[0]
    section.different_first_page_header_footer = True  # Enable different first page header/footer
    
    footer = section.first_page_footer
    footer_para = footer.paragraphs[0]
    
    # Clear any existing content
    footer_para.clear()
    
    # Footer text content
    footer_lines = [
        "香港金鐘夏愨道18號海富中心24樓  電話: 2114 4960  傳真: 3544 2933",
        "電郵: info@asianet-sprg.com.hk", 
        "網頁: http://www.asianet-sprg.com.hk"
    ]
    
    # Add each line with proper formatting
    for i, line in enumerate(footer_lines):
        run = footer_para.add_run(line)
        run.font.name = 'Calibri'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), '標楷體')
        run.font.size = Pt(12)
        
        # Add line break after each line except the last one
        if i < len(footer_lines) - 1:
            footer_para.add_run('\n')
    
    # Set left alignment
    footer_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    
    print("First page footer added with left-aligned text and font size 12.")

def add_subsequent_pages_header(doc):
    """
    Add header to pages 2 onwards with left-aligned text, font size 12
    """
    section = doc.sections[0]
    # different_first_page_header_footer should already be True from first page setup
    
    # Access the regular header (for pages 2 onwards)
    header = section.header
    header_para = header.paragraphs[0]
    
    # Clear any existing content
    header_para.clear()
    
    # Add the text
    header_run = header_para.add_run("AsiaNet亞聯政經顧問")
    header_run.font.name = 'Calibri'
    header_run._element.rPr.rFonts.set(qn('w:eastAsia'), '標楷體')
    header_run.font.size = Pt(12)
    
    # Set left alignment
    header_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    
    print("Subsequent pages header added with font size 12 and left alignment.")

def add_subsequent_pages_footer(doc):
    """
    Add footer to pages 2 onwards with center-aligned page numbers, font size 12
    """
    section = doc.sections[0]
    # different_first_page_header_footer should already be True from first page setup
    
    # Access the regular footer (for pages 2 onwards)
    footer = section.footer
    footer_para = footer.paragraphs[0]
    
    # Clear any existing content
    footer_para.clear()
    
    # Add page number field
    footer_run = footer_para.add_run()
    footer_run.font.name = 'Calibri'
    footer_run.font.size = Pt(12)
    
    # Add page number field using XML
    fldChar1 = OxmlElement('w:fldChar')
    fldChar1.set(qn('w:fldCharType'), 'begin')
    footer_run._element.append(fldChar1)
    
    instrText = OxmlElement('w:instrText')
    instrText.text = 'PAGE'
    footer_run._element.append(instrText)
    
    fldChar2 = OxmlElement('w:fldChar')
    fldChar2.set(qn('w:fldCharType'), 'end')
    footer_run._element.append(fldChar2)
    
    # Set center alignment
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    print("Subsequent pages footer added with center-aligned page numbers and font size 12.")

def add_date_line_if_needed(doc, date_str):
    # Check if first non-empty paragraph is already the date line
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:  # Non-empty
            if re.match(r'^\d{8}$', text):
                return  # Date line already present
            break
    # Insert date line at the top
    doc._body.clear_content()  # Remove all content temporarily
    date_para = doc.add_paragraph(date_str)
    date_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    date_para.style = doc.styles['Normal']

def add_end_marker(doc):
    blank_para = doc.add_paragraph("")
    blank_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    end_para = doc.add_paragraph("（完）")
    end_para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    end_para.style = doc.styles['Normal']

def convert_to_traditional_chinese(text):
    """
    Convert simplified Chinese to traditional Chinese (Hong Kong variant).
    This function handles the conversion for Hong Kong customers.
    """
    if not text or not text.strip():
        return text
    
    try:
        # Use s2hk mode for Hong Kong variant of traditional Chinese
        cc = OpenCC('s2hk')  # Simplified -> Traditional (Hong Kong)
        converted_text = cc.convert(text)
        return converted_text
    except Exception as e:
        print(f"Warning: Chinese conversion failed for text: {text[:50]}... Error: {str(e)}")
        return text  # Return original text if conversion fails

def extract_document_structure(doc_path, json_output_path=None):
    """
    Extracts structure using state-based logic with Chinese conversion.
    """
    global TITLE_MODIFICATIONS
    TITLE_MODIFICATIONS = []

    if json_output_path is None:
        json_output_path = doc_path.replace('.docx', '_structure.json')
    
    doc = Document(doc_path)
    structure = {'total_paragraphs': len(doc.paragraphs), 'editorial_media_groups': [], 'sections': {}, 'other_content': []}
    
    current_section = None
    in_editorial = False
    section_counters = {}
    
    is_expecting_title = False
    title_cooldown_counter = 0

    paragraphs = doc.paragraphs
    num_paragraphs = len(paragraphs)
    
    for i, paragraph in enumerate(paragraphs):
        # Apply Chinese conversion to paragraph text
        original_text = paragraph.text.strip()
        text = convert_to_traditional_chinese(original_text)
        
        section_type = detect_section_type(text)
        if section_type:
            current_section = section_type
            in_editorial = (section_type == 'editorial')
            is_expecting_title = not in_editorial
            title_cooldown_counter = 0
            if section_type not in structure['sections']:
                structure['sections'][section_type] = []
            structure['other_content'].append({'index': i, 'text': text, 'type': 'section_header', 'section': section_type})
            continue
        
        if not text: continue
        
        if in_editorial:
            media_info = detect_editorial_media_line(text)
            if media_info:
                # Apply conversion to media content as well
                converted_content = convert_to_traditional_chinese(media_info['content'])
                media_info['content'] = converted_content
                current_media_group = {'clean_name': media_info['clean_name'], 'original_name': media_info['full_name'], 'start_index': i, 'first_item': converted_content, 'additional_items': []}
                structure['editorial_media_groups'].append(current_media_group)
            elif 'current_media_group' in locals() and current_media_group and is_editorial_continuation(text):
                current_media_group['additional_items'].append({'index': i, 'text': text})
        else:
            # Rest of the existing logic remains the same, but with converted text
            if title_cooldown_counter > 0:
                structure['other_content'].append({'index': i, 'text': text, 'type': 'content', 'section': current_section})
                title_cooldown_counter -= 1
                continue

            is_title = False
            prospective_title_text = text

            if is_expecting_title:
                is_title = True
                is_expecting_title = False
            else:
                if i + 1 < num_paragraphs:
                    next_paragraph_text = convert_to_traditional_chinese(paragraphs[i+1].text.strip())
                    if is_source_citation(next_paragraph_text) and is_valid_headline(text):
                        is_title = True

            if current_section and is_title:
                match_existing_index = re.match(r'^(\d+)\.\s*(.*)', text)
                if match_existing_index:
                    original_title_text, stripped_title_text = text, match_existing_index.group(2).strip()
                    TITLE_MODIFICATIONS.append({'original_text': original_title_text, 'modified_text': stripped_title_text, 'section': current_section, 'original_paragraph_index': i})
                    prospective_title_text = stripped_title_text
                
                section_counters[current_section] = section_counters.get(current_section, 0) + 1
                article_index = section_counters[current_section]
                
                structure['sections'][current_section].append({'index': i, 'text': prospective_title_text, 'type': 'article_title', 'section_index': article_index})
                
                title_cooldown_counter = 1
            else:
                structure['other_content'].append({'index': i, 'text': text, 'type': 'content', 'section': current_section})

    # Rest of the function remains the same...
    structure['title_format_modifications'] = TITLE_MODIFICATIONS

    with open(json_output_path, 'w', encoding='utf-8') as f:
        json.dump(structure, f, ensure_ascii=False, indent=2, default=str)
    print(f"Document structure extracted to: {json_output_path}")
    print_detailed_analysis(structure)
    return structure


def export_revision_log_to_txt(structure, base_path):
    """
    Export title format modifications to a separate .txt file for side-by-side viewing
    """
    txt_path = base_path.replace('.docx', '_revision_log.txt')
    
    if not structure.get('title_format_modifications'):
        print("No title modifications found - no revision log created.")
        return None
    
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write("=" * 60 + "\n")
        f.write("標題格式修改內容 (Title Format Modifications)\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total modifications: {len(structure['title_format_modifications'])}\n")
        f.write("Generated for side-by-side revision viewing\n\n")
        
        # Group modifications by section
        modifications_by_section = {}
        for mod in structure['title_format_modifications']:
            section = mod['section']
            if section not in modifications_by_section:
                modifications_by_section[section] = []
            modifications_by_section[section].append(mod)
        
        for section_name, mods in modifications_by_section.items():
            f.write("-" * 50 + "\n")
            f.write(f"Section: {section_name.upper()}\n")
            f.write("-" * 50 + "\n")
            
            for i, mod in enumerate(mods, 1):
                f.write(f"\n{i}. Paragraph Index: {mod['original_paragraph_index']}\n")
                f.write(f"   ORIGINAL:  '{mod['original_text']}'\n")
                f.write(f"   MODIFIED:  '{mod['modified_text']}'\n")
            
            f.write("\n")
        
        f.write("=" * 60 + "\n")
        f.write("End of revision log\n")
        f.write("=" * 60 + "\n")
    
    print(f"Revision log exported to: {txt_path}")
    return txt_path

def print_detailed_analysis(structure):
    print("\n=== DETAILED DOCUMENT ANALYSIS ==="); print(f"Total paragraphs: {structure['total_paragraphs']}")
    print(f"\n=== EDITORIAL MEDIA GROUPS ({len(structure['editorial_media_groups'])}) ===")
    for i, group in enumerate(structure['editorial_media_groups']): print(f"{i+1}. {group['clean_name']}")
    print("\n=== REGULAR ARTICLES (Grouped by Section) ===")
    for section_name, articles in structure['sections'].items():
        if articles:
            print(f"\n--- Section: {section_name.upper()} ({len(articles)} articles) ---")
            for article in articles: print(f"  {article['section_index']}.    {article['text'][:60]}...")
    if structure.get('title_format_modifications'):
        print(f"\n=== TITLE FORMAT MODIFICATIONS ({len(structure['title_format_modifications'])}) ===")
        for mod in structure['title_format_modifications']: print(f"  [{mod['section']}] Original Index {mod['original_paragraph_index']}: '{mod['original_text']}'\n       -> Modified: '{mod['modified_text']}'")

def rebuild_document_from_structure(doc_path, structure_json_path=None, output_path=None):
    from datetime import datetime

    if structure_json_path is None:
        structure_json_path = doc_path.replace('.docx', '_structure.json')
    if output_path is None:
        output_path = doc_path.replace('.docx', '_final_formatted.docx')
    with open(structure_json_path, 'r', encoding='utf-8') as f:
        structure = json.load(f)
    new_doc = Document()
    setup_document_fonts(new_doc)

    # 1. Add date line if not present
    today_str = datetime.now().strftime("%Y%m%d")
    add_date_line_if_needed(new_doc, today_str)

    # 2. Add editorial section header if exists
    editorial_section_header = None
    for content in structure['other_content']:
        if content['type'] == 'section_header' and content['section'] == 'editorial':
            editorial_section_header = content['text']
            break
    if editorial_section_header:
        add_section_header_to_doc(new_doc, editorial_section_header)

    # 3. Editorials: sort and output in fixed order
    editorial_groups = {g['clean_name']: g for g in structure['editorial_media_groups']}
    for name in EDITORIAL_MEDIA_ORDER:
        if name in editorial_groups:
            add_media_group_to_document(new_doc, editorial_groups[name])

    # 4. Add other content as before (sections, articles, etc.)
    all_content = []
    for content in structure['other_content']:
        # Skip editorial section header since it was already added
        if content['type'] == 'section_header' and content['section'] == 'editorial':
            continue
        all_content.append(('other', content))
    for section_name, articles in structure['sections'].items():
        for article in articles:
            all_content.append(('article', article))
    all_content.sort(key=lambda x: x[1].get('index', x[1].get('start_index', 0)))
    previous_was_content = False
    last_article_idx = -1
    for idx, (content_type, content_data) in enumerate(all_content):
        if content_type == 'other':
            if content_data['type'] == 'section_header':
                add_section_header_to_doc(new_doc, content_data['text'])
            else:
                p = new_doc.add_paragraph(content_data['text'])
                format_content_paragraph(p)
            previous_was_content = True
        elif content_type == 'article':
            add_article_to_document(new_doc, content_data, previous_was_content)
            previous_was_content = True
            last_article_idx = idx

    # 5. After the last article, add blank line and "（完）"
    if last_article_idx != -1:
        add_end_marker(new_doc)

    new_doc.save(output_path)
    print(f"Document with date line and end marker saved as: {output_path}")
    return output_path



def add_section_header_to_doc(doc, text): 
    p = doc.add_paragraph(); p.add_run(text).bold = True; format_section_header(p)

def add_article_to_document(doc, article_data, needs_spacing): 
    p = doc.add_paragraph()
    # Add four spaces between period and title for professional formatting
    title_text = f"{article_data['section_index']}.    {article_data['text']}"
    p.add_run(title_text).bold = True
    p.style = doc.styles['Normal']
    format_article_title(p, needs_spacing)

def add_media_group_to_document(new_doc, media_group):
    # Calculate the visual width of the media name + colon for alignment
    media_label = f"{media_group['clean_name']}："
    label_length = len(media_label)
    full_width_space = '\u3000'  # Unicode full-width space

    # First line: media name + colon + first item
    para = new_doc.add_paragraph()
    para.add_run(f"{media_label}{media_group['first_item']}")
    format_media_first_line_hanging(para, label_length)

    # Additional items: pad with full-width spaces to align with first item's text
    for item in media_group['additional_items']:
        item_para = new_doc.add_paragraph()
        item_para.add_run(full_width_space * label_length + item['text'])
        format_media_first_line_hanging(item_para, label_length)


def detect_section_type(text):
    if not text: return None
    sections = {'editorial': r'^報章社評\s*$', 'international': r'^國際新聞[:：]?\s*$', 'china': r'^大中華新聞\s*$', 'local': r'^本地新聞\s*$', 'financial': r'^財經新聞\s*$', 'Hong Kong': r'^香港本地新聞\s*$', 'entertainment': r'^娛樂新聞\s*$', 'sports': r'^體育新聞\s*$', 'property': r'^地產新聞\s*$'}
    for name, pattern in sections.items():
        if re.match(pattern, text): return name
    return None

def detect_editorial_media_line(text):
    """
    Detects editorial media lines using hardcoded media names.
    Handles both short names (頭條) and potential long names (頭條日報).
    """
    if not text: return None
    
    match = re.match(r'^([^：]+)：(.*)$', text)
    if match:
        potential_name, content = match.group(1).strip(), match.group(2).strip()
        
        # First check if it's one of our known editorial media names (short versions)
        if potential_name in EDITORIAL_MEDIA_NAMES:
            return {'full_name': potential_name, 'clean_name': potential_name, 'content': content}
        
        # Also check if it's a long name that maps to a short name
        if potential_name in MEDIA_NAME_MAPPINGS:
            clean_name = MEDIA_NAME_MAPPINGS[potential_name]
            return {'full_name': potential_name, 'clean_name': clean_name, 'content': content}
    
    return None


def is_editorial_continuation(text):
    """
    Detects if a line is a continuation of editorial content.
    Looks for numbered items (1., 2., etc.) or indented content.
    """
    if not text: return False
    
    # Check for numbered items (1., 2., etc.)
    if re.match(r'^\s*\d+\.\s+', text): return True
    
    # Check for content that starts with tab or multiple spaces (indented)
    if re.match(r'^[\t\s]{2,}', text): return True
    
    # Check for substantial content (longer lines likely to be editorial content)
    if len(text.strip()) > 15: return True
    
    return False

def format_content_paragraph(paragraph): pf = paragraph.paragraph_format; pf.line_spacing = 1.0; pf.left_indent = Pt(0); pf.first_line_indent = Pt(0); pf.space_before = Pt(0); pf.space_after = Pt(6)
def format_media_first_line_hanging(paragraph, label_length):
    pf = paragraph.paragraph_format
    indent_amount = Pt(54)
    pf.line_spacing = 1.0
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.keep_with_next = True
    pf.left_indent = indent_amount
    pf.first_line_indent = -indent_amount
    pf.tab_stops.clear_all()
    pf.tab_stops.add_tab_stop(indent_amount)


def format_section_header(paragraph): pf = paragraph.paragraph_format; pf.line_spacing = 1.0; pf.left_indent = Pt(0); pf.space_before = Pt(12); pf.space_after = Pt(6); pf.keep_with_next = True
def format_article_title(paragraph, needs_spacing): pf = paragraph.paragraph_format; pf.line_spacing = 1.0; pf.left_indent = Pt(0); pf.first_line_indent = Pt(0); pf.space_before = Pt(12) if needs_spacing else Pt(0); pf.space_after = Pt(0); pf.keep_with_next = True
def setup_document_fonts(doc): style = doc.styles['Normal']; font = style.font; font.name = 'Calibri'; font.size = Pt(12); style._element.rPr.rFonts.set(qn('w:eastAsia'), '標楷體')

def complete_formatting_workflow(input_file, logo_path=None):
    print("=== STEP 1: EXTRACTING WITH STATE-BASED LOGIC ===")
    structure = extract_document_structure(input_file)
    
    print("\n=== STEP 2: REBUILDING DOCUMENT ===")
    final_file = rebuild_document_from_structure(input_file)
    
    # Add header if logo path is provided
    if logo_path:
        print("\n=== STEP 3: ADDING HEADER ===")
        doc = Document(final_file)
        add_first_page_header(doc, logo_path)
        add_first_page_footer(doc)
        add_subsequent_pages_header(doc)
        doc.save(final_file)
        print("Header added successfully!")
    
    return final_file

def create_directories():
    """Create input and output directories if they don't exist"""
    input_dir = "input"
    output_dir = "output"
    
    # Create directories if they don't exist
    if not os.path.exists(input_dir):
        os.makedirs(input_dir)
        print(f"Created folder: {input_dir}")
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Created folder: {output_dir}")
    
    return input_dir, output_dir

def find_docx_files(input_dir):
    """Find all .docx files in the input directory"""
    docx_files = glob.glob(os.path.join(input_dir, "*.docx"))
    return docx_files

def get_user_confirmation():
    """Get yes/no confirmation from user with input validation"""
    while True:
        user_input = input("Press 'y' to continue processing: ").strip().lower()
        if user_input in ['y', 'yes']:
            return True
        elif user_input in ['n', 'no']:
            return False
        else:
            print("Invalid input. Please enter 'y' for yes or 'n' for no.")

def process_files_workflow():
    """Main workflow for processing files with automatic folder management"""
    print("=== DOCUMENT FORMATTING WORKFLOW ===")
    
    # Step 1: Create directories
    input_dir, output_dir = create_directories()
    
    # Step 2: Look for .docx files
    docx_files = find_docx_files(input_dir)
    
    if not docx_files:
        print(f"\nNo .docx files found in the '{input_dir}' folder.")
        print(f"Please add your document(s) to the '{input_dir}' folder and try again.")
        
        if get_user_confirmation():
            # Check again after user confirmation
            docx_files = find_docx_files(input_dir)
            if not docx_files:
                print("Still no files found. Exiting...")
                return
        else:
            print("Exiting...")
            return
    
    # Step 3: Process each file
    logo_path = "AsiaNet_logo.png"  # Make sure this is in the same directory as exe
    
    for input_file in docx_files:
        print(f"\n=== Processing: {os.path.basename(input_file)} ===")
        
        try:
            # Extract structure
            print("STEP 1: Extracting document structure...")
            structure = extract_document_structure(input_file)
            
            # Rebuild document
            print("STEP 2: Rebuilding document with formatting...")
            temp_output = rebuild_document_from_structure(input_file)
            
            # Add headers and footers
            print("STEP 3: Adding headers and footers...")
            if os.path.exists(logo_path):
                doc = Document(temp_output)
                add_first_page_header(doc, logo_path)
                add_first_page_footer(doc)
                add_subsequent_pages_header(doc)
                add_subsequent_pages_footer(doc)
                doc.save(temp_output)
            else:
                print(f"Warning: Logo file '{logo_path}' not found. Skipping header/footer.")
            
            # Move to output folder
            filename = os.path.basename(input_file)
            final_output = os.path.join(output_dir, filename.replace('.docx', '_formatted.docx'))
            
            # Move the processed file to output directory
            if os.path.exists(temp_output):
                os.rename(temp_output, final_output)
                print(f"✓ Formatted document saved: {final_output}")
            
            # Move revision log to output directory
            revision_log = input_file.replace('.docx', '_revision_log.txt')
            if os.path.exists(revision_log):
                output_log = os.path.join(output_dir, os.path.basename(revision_log))
                os.rename(revision_log, output_log)
                print(f"✓ Revision log saved: {output_log}")
        
        except Exception as e:
            print(f"✗ Error processing {input_file}: {str(e)}")
    
    print(f"\n=== PROCESSING COMPLETE ===")
    print(f"Check the '{output_dir}' folder for your formatted documents!")
    input("Press Enter to exit...")  # Keep console open to see results
    
def main():
    st.title("Document Formatting Tool")
    st.markdown("Upload your Word document to get it formatted automatically")
    
    # File uploader
    uploaded_file = st.file_uploader("Choose a Word document", type=['docx'])
    
    if uploaded_file is not None:
        # Create a temporary file to save the uploaded document
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name
        
        try:
            # Process the document using your existing functions
            st.write("Processing your document...")
            
            # Extract structure
            structure = extract_document_structure(tmp_file_path)
            
            # Rebuild document
            formatted_file = rebuild_document_from_structure(tmp_file_path)
            
            # Add headers/footers if logo exists
            logo_path = "AsiaNet_logo.png"
            if os.path.exists(logo_path):
                from docx import Document
                doc = Document(formatted_file)
                add_first_page_header(doc, logo_path)
                add_first_page_footer(doc)
                add_subsequent_pages_header(doc)
                add_subsequent_pages_footer(doc)
                doc.save(formatted_file)
            
            # Provide download link
            with open(formatted_file, 'rb') as f:
                st.download_button(
                    label="Download Formatted Document",
                    data=f.read(),
                    file_name=f"formatted_{uploaded_file.name}",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            
            st.success("Document processed successfully!")
            
        except Exception as e:
            st.error(f"Error processing document: {str(e)}")
        
        finally:
            # Clean up temporary files
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
            if os.path.exists(formatted_file):
                os.remove(formatted_file)

if __name__ == "__main__":
    main()
