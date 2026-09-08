#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_image_pdf.py — OCR-based extraction for scanned/image PDFs.

Detects image-based PDFs (those with minimal extractable text) and applies OCR.
Falls back gracefully when OCR fails or is unavailable.

Usage:
    python3 extract_image_pdf.py input.pdf [output.txt]

Strategy:
1. Try pdfplumber text extraction first (fast, text-based PDFs)
2. If text yield is low (< 100 chars for file > 500KB), treat as scanned
3. Attempt OCR via available methods:
   a. Claude's pdf-ocr skill (if available in current session)
   b. System tesseract (if installed)
   c. macOS Vision framework (if on macOS with Python objc bindings)
4. On failure, return error with [需人工录入] tag

Dependencies:
- pdfplumber (pip3 install pdfplumber)
- Optional: pytesseract + tesseract-ocr (brew install tesseract; pip3 install pytesseract)
- Optional: pyobjc-framework-Vision (pip3 install pyobjc-framework-Vision) for macOS Vision OCR
"""
import sys
import os
from pathlib import Path

def die(msg):
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(1)

def get_file_size(path):
    """Get file size in KB."""
    return os.path.getsize(path) / 1024

def extract_text_pdfplumber(path):
    """Try extracting text with pdfplumber (works for text-based PDFs)."""
    try:
        import pdfplumber
    except ImportError:
        die("pdfplumber not installed. Run: pip3 install pdfplumber")

    text_parts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        sys.stderr.write(f"pdfplumber extraction failed: {e}\n")
        return ""

    return "\n\n".join(text_parts)

def is_scanned_pdf(path, text_content):
    """Heuristic: if file > 500KB but extracted text < 100 chars, likely scanned."""
    file_size_kb = get_file_size(path)
    text_length = len(text_content.strip())

    if file_size_kb > 500 and text_length < 100:
        return True
    if file_size_kb > 1000 and text_length < 500:
        return True
    return False

def ocr_with_tesseract(path):
    """OCR using tesseract (requires system installation)."""
    try:
        import pytesseract
        from PIL import Image
        import pdf2image
    except ImportError:
        sys.stderr.write("pytesseract or pdf2image not installed. Skipping tesseract OCR.\n")
        return None

    try:
        # Convert PDF pages to images
        sys.stderr.write("Converting PDF to images...\n")
        images = pdf2image.convert_from_path(path)

        text_parts = []
        for i, image in enumerate(images, 1):
            sys.stderr.write(f"OCR page {i}/{len(images)}...\n")
            page_text = pytesseract.image_to_string(image, lang='chi_sim+eng')
            text_parts.append(page_text)

        result = "\n\n".join(text_parts)
        sys.stderr.write(f"Tesseract OCR extracted {len(result)} characters\n")
        return result
    except Exception as e:
        sys.stderr.write(f"Tesseract OCR failed: {e}\n")
        return None

def ocr_with_macos_vision(path):
    """OCR using macOS Vision framework (macOS only, requires pyobjc)."""
    if sys.platform != 'darwin':
        return None

    try:
        from Quartz import PDFDocument
        from Vision import VNRecognizeTextRequest, VNImageRequestHandler
        from Foundation import NSURL
        import Quartz
    except ImportError:
        sys.stderr.write("macOS Vision framework bindings not installed. Skipping.\n")
        return None

    try:
        pdf_url = NSURL.fileURLWithPath_(path)
        pdf_doc = PDFDocument.alloc().initWithURL_(pdf_url)

        if not pdf_doc:
            sys.stderr.write("Could not open PDF with Quartz\n")
            return None

        page_count = pdf_doc.pageCount()
        sys.stderr.write(f"Processing {page_count} pages with Vision OCR...\n")

        text_parts = []
        for i in range(page_count):
            page = pdf_doc.pageAtIndex_(i)
            page_image = page.thumbnailOfSize_forBox_((2000, 2000), Quartz.kPDFDisplayBoxMediaBox)

            # Create Vision request
            request = VNRecognizeTextRequest.alloc().init()
            request.setRecognitionLevel_(1)  # Accurate level
            request.setRecognitionLanguages_(["zh-Hans", "en-US"])

            handler = VNImageRequestHandler.alloc().initWithCGImage_options_(
                page_image, {}
            )

            success = handler.performRequests_error_([request], None)
            if success:
                observations = request.results()
                page_text = "\n".join([obs.text() for obs in observations])
                text_parts.append(page_text)

        result = "\n\n".join(text_parts)
        sys.stderr.write(f"Vision OCR extracted {len(result)} characters\n")
        return result
    except Exception as e:
        sys.stderr.write(f"Vision OCR failed: {e}\n")
        return None

def main():
    if len(sys.argv) < 2:
        die("usage: python3 extract_image_pdf.py input.pdf [output.txt]")

    input_path = sys.argv[1]
    if not os.path.exists(input_path):
        die(f"input not found: {input_path}")

    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    # Step 1: Try normal text extraction
    sys.stderr.write("Attempting text extraction with pdfplumber...\n")
    text = extract_text_pdfplumber(input_path)

    # Step 2: Detect if scanned
    if is_scanned_pdf(input_path, text):
        sys.stderr.write("Detected scanned PDF (low text yield). Attempting OCR...\n")

        # Try OCR methods in order of preference
        ocr_result = None

        # Method 1: Tesseract (cross-platform, good accuracy)
        sys.stderr.write("Trying Tesseract OCR...\n")
        ocr_result = ocr_with_tesseract(input_path)

        # Method 2: macOS Vision (macOS only, fast, good accuracy)
        if not ocr_result:
            sys.stderr.write("Trying macOS Vision OCR...\n")
            ocr_result = ocr_with_macos_vision(input_path)

        if ocr_result and len(ocr_result.strip()) > 100:
            text = ocr_result
            sys.stderr.write("OCR succeeded.\n")
        else:
            sys.stderr.write("\n[需人工录入] OCR failed or produced insufficient text.\n")
            sys.stderr.write("This file requires manual data entry.\n")
            if output_path:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write("[需人工录入] OCR extraction failed. Manual entry required.\n")
            sys.exit(2)  # Exit code 2 = OCR failure

    # Output
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(text)
        sys.stderr.write(f"Extracted text saved to: {output_path}\n")
    else:
        print(text)

    sys.stderr.write(f"Extracted {len(text)} characters from {Path(input_path).name}\n")

if __name__ == "__main__":
    main()
