import streamlit as st
import tempfile
import os
import pytz
from datetime import datetime, timedelta

from utils.document_utils import (
    extract_document_structure, 
    rebuild_document_from_structure,
    add_first_page_header, 
    add_first_page_footer,
    add_subsequent_pages_header, 
    add_subsequent_pages_footer
)
from utils.firebase_logging import ensure_logger
from docx import Document

def is_monday_in_hk():
    tz = pytz.timezone('Asia/Hong_Kong')
    today_hk = datetime.now(tz).date()
    return today_hk.weekday() == 0  # 0 is Monday

def render_document_formatting_tab():
    st.header("Document Formatting")
    st.markdown("Upload your Word document to get it formatted automatically")
    
    # === Detect Monday in Hong Kong automatically ===
    auto_monday = is_monday_in_hk()
    monday_mode = st.checkbox("Today is Monday", value=auto_monday)
    sunday_date = None
    if monday_mode:
        default_sunday = (datetime.now(pytz.timezone('Asia/Hong_Kong')).date() - timedelta(days=1)).strftime("%Y%m%d")
        sunday_date = st.text_input("Date of Previous Sunday", value=default_sunday, help="e.g., 20250914")

    # Initialize Firebase logger for document processing
    logger = ensure_logger(st, run_context="document_formatting")

    uploaded_file = st.file_uploader("Choose a Word document", type=['docx'])

    if uploaded_file is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_file_path = tmp_file.name

        try:
            st.write("Processing your document...")
            logger.info("Document processing started", 
                       filename=uploaded_file.name, 
                       file_size=uploaded_file.size)

            # Save original file for upload
            original_file_path = f"temp_original_{uploaded_file.name}"
            with open(original_file_path, 'wb') as f:
                f.write(uploaded_file.getvalue())

            progress = st.progress(0)

            progress.progress(25, text="Extracting document structure...")
            logger.info("Extracting document structure")
            structure = extract_document_structure(tmp_file_path, monday_mode=monday_mode, sunday_date=sunday_date)

            progress.progress(50, text="Rebuilding document from structure...")
            logger.info("Rebuilding document from structure")
            formatted_file = rebuild_document_from_structure(tmp_file_path, monday_mode=monday_mode, sunday_date=sunday_date)

            progress.progress(75, text="Applying headers and footers...")
            logger.info("Applying headers and footers")

            # Fix the filename (note the underscore)
            logo_path = os.path.join("assets", "AsiaNet_logo.png")

            # Debug information
            st.write(f"Looking for logo at: {logo_path}")
            st.write(f"Logo exists: {os.path.exists(logo_path)}")

            # Always add headers and footers, regardless of logo
            doc = Document(formatted_file)

            if os.path.exists(logo_path):
                st.write("✅ Logo found - adding headers with logo")
                logger.info("Adding headers with logo")
                add_first_page_header(doc, logo_path)
            else:
                st.write("⚠️ Logo not found - adding headers without logo")
                logger.warn("Logo not found, adding headers without logo", logo_path=logo_path)
                add_first_page_header(doc, None)

            # Always add footers and other headers
            add_first_page_footer(doc)
            add_subsequent_pages_header(doc)
            add_subsequent_pages_footer(doc)
            doc.save(formatted_file)

            progress.progress(90, text="Uploading files to Firebase...")

            # Upload original file to Firebase
            try:
                original_remote_path = f"document_processing/{logger.session_id}/{logger.run_id}/input/{uploaded_file.name}"
                original_gs_url = logger.upload_file_to_firebase(original_file_path, original_remote_path)
                logger.info("Original file uploaded to Firebase", 
                           gs_url=original_gs_url, 
                           remote_path=original_remote_path)

                # Upload processed file to Firebase
                processed_filename = f"formatted_{uploaded_file.name}"
                processed_remote_path = f"document_processing/{logger.session_id}/{logger.run_id}/output/{processed_filename}"
                processed_gs_url = logger.upload_file_to_firebase(formatted_file, processed_remote_path)
                logger.info("Processed file uploaded to Firebase", 
                           gs_url=processed_gs_url, 
                           remote_path=processed_remote_path)

                st.write("✅ Files uploaded to Firebase storage")

            except Exception as upload_error:
                logger.error("Failed to upload files to Firebase", error=str(upload_error))
                st.warning(f"Warning: Could not upload to Firebase: {str(upload_error)}")

            progress.progress(100, text="Formatting complete!")

            # Always provide download regardless of logo or Firebase upload status
            with open(formatted_file, 'rb') as f:
                st.download_button(
                    label="📥 Download Formatted Document",
                    data=f.read(),
                    file_name=f"formatted_{uploaded_file.name}",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )

            st.success("Document processed successfully!")

            # End the logging run with summary
            logger.end_run(status="completed", summary={
                "original_file": uploaded_file.name,
                "original_size": uploaded_file.size,
                "processed_file": f"formatted_{uploaded_file.name}",
                "logo_found": os.path.exists(logo_path),
                "firebase_upload": "success" if 'processed_gs_url' in locals() else "failed"
            })

        except Exception as e:
            logger.error("Document processing failed", 
                        error=str(e), 
                        filename=uploaded_file.name)
            st.error(f"Error processing document: {str(e)}")

            # End run with error status
            logger.end_run(status="error", summary={
                "error": str(e),
                "filename": uploaded_file.name
            })

        finally:
            # Cleanup temporary files
            if os.path.exists(tmp_file_path):
                os.remove(tmp_file_path)
            if 'original_file_path' in locals() and os.path.exists(original_file_path):
                os.remove(original_file_path)
            if 'formatted_file' in locals() and formatted_file is not None and os.path.exists(formatted_file):
                os.remove(formatted_file)