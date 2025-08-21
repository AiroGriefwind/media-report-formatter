import streamlit as st
import tempfile
import os
from utils.document_utils import (
    extract_document_structure, 
    rebuild_document_from_structure,
    add_first_page_header, 
    add_first_page_footer,
    add_subsequent_pages_header, 
    add_subsequent_pages_footer
)
from docx import Document

def render_document_formatting_tab():
    st.header("Document Formatting")
    st.markdown("Upload your Word document to get it formatted automatically")
    
    uploaded_file = st.file_uploader("Choose a Word document", type=['docx'])
    
    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        try:
            st.write("Processing your document...")
            progress = st.progress(0)
            
            progress.progress(25, text="Extracting document structure...")
            structure = extract_document_structure(tmp_file_path)
            
            progress.progress(50, text="Rebuilding document from structure...")
            formatted_file = rebuild_document_from_structure(tmp_file_path)
            
            progress.progress(75, text="Applying headers and footers...")
            
            # Fix the filename (note the underscore)
            logo_path = os.path.join("assets", "AsiaNet_logo.png")
            
            # Debug information
            st.write(f"Looking for logo at: {logo_path}")
            st.write(f"Logo exists: {os.path.exists(logo_path)}")
            
            # Always add headers and footers, regardless of logo
            doc = Document(formatted_file)
            
            if os.path.exists(logo_path):
                st.write("✅ Logo found - adding headers with logo")
                add_first_page_header(doc, logo_path)
            else:
                st.write("⚠️ Logo not found - adding headers without logo")
                add_first_page_header(doc, None)  # This should now work with the updated function
            
            # Always add footers and other headers
            add_first_page_footer(doc)
            add_subsequent_pages_header(doc)
            add_subsequent_pages_footer(doc)
            doc.save(formatted_file)
            
            progress.progress(100, text="Formatting complete!")
            
            # Always provide download regardless of logo
            with open(formatted_file, 'rb') as f:
                st.download_button(
                    label="📥 Download Formatted Document",
                    data=f.read(),
                    file_name=f"formatted_{uploaded_file.name}",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            
            st.success("Document processed successfully!")
            
        except Exception as e:
            st.error(f"Error processing document: {str(e)}")
            
        finally:
            # Cleanup
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
            if 'formatted_file' in locals() and formatted_file is not None and os.path.exists(formatted_file):
                os.remove(formatted_file)
