#!/usr/bin/env python3
"""
OCR Script for Scanned Rule Documents
=======================================
Uses Tesseract OCR via pdf2image + pytesseract to extract text
from scanned PDF rule documents.

Scanned documents:
1. SOP OF MEDICAL BILL SCRUTINY.pdf (3 pages)
2. CASHLESS HOSPITAL BILL PASSING CHECKLIST.pdf (2 pages)
3. Approved SOP for medicine distribution.pdf (4 pages)
4. change in entitlement under MAR.pdf (1 page)

Also extracts text from text-based PDFs using pdftotext.
"""

import os
import subprocess
from pdf2image import convert_from_path
import pytesseract
from PIL import Image, ImageFilter, ImageEnhance

RULES_DIR = os.path.join(os.path.dirname(__file__), '..', 'Rules')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'ocr_output')
RAW_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'raw')

# Documents categorized
SCANNED_DOCS = [
    'SOP OF MEDICAL BILL SCRUTINY.pdf',
    'CASHLESS HOSPITAL BILL PASSING CHECKLIST.pdf',
    'Approved SOP for medicine distribution.pdf',
    'change in entitlement under MAR.pdf',
]

TEXT_DOCS = [
    '20240202 161 OM - CPRMSNE - MED BENEFIT AS DEPENDENT OF WORKING SPOUSE.pdf',
    'CIL-OM-Spectacles-reimbursement.pdf',
    'Clarification on clause 3.1.2 of  CPRMS-NE (Modified) regarding payment of OPD _ Domiciliary treatment.pdf',
    'Clarification w.r.t reimbursement of hospitalization charges to undeclared _ deceased  _ unavailable  nominee.pdf',
]


def preprocess_image(img):
    """
    Preprocess image for better OCR accuracy on scanned documents.
    - Convert to grayscale
    - Enhance contrast
    - Sharpen
    - Upscale for better recognition
    """
    # Convert to grayscale
    img = img.convert('L')
    
    # Enhance contrast
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.5)
    
    # Sharpen
    img = img.filter(ImageFilter.SHARPEN)
    
    # Upscale if too small (helps with small text)
    width, height = img.size
    if width < 2000:
        scale = 2000 / width
        img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)
    
    return img


def ocr_scanned_pdf(pdf_path, output_path):
    """OCR a scanned PDF and save the extracted text."""
    filename = os.path.basename(pdf_path)
    print(f"\n  📄 Processing: {filename}")
    
    # Convert PDF pages to images
    print(f"    Converting PDF to images...")
    try:
        images = convert_from_path(pdf_path, dpi=300)
    except Exception as e:
        print(f"    ❌ Failed to convert PDF: {e}")
        return False
    
    print(f"    Found {len(images)} pages")
    
    all_text = []
    for page_num, img in enumerate(images, 1):
        print(f"    OCR page {page_num}/{len(images)}...", end=' ')
        
        # Preprocess
        processed_img = preprocess_image(img)
        
        # OCR with Tesseract
        # Use English + preserve layout
        text = pytesseract.image_to_string(
            processed_img,
            lang='eng',
            config='--psm 6 --oem 3'  # Assume uniform block of text, use LSTM engine
        )
        
        char_count = len(text.strip())
        print(f"({char_count} chars)")
        
        all_text.append(f"--- PAGE {page_num} ---\n")
        all_text.append(text)
        all_text.append('\n\n')
    
    # Save output
    full_text = ''.join(all_text)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(full_text)
    
    total_chars = len(full_text.strip())
    print(f"    ✅ Saved: {output_path} ({total_chars} chars)")
    return True


def extract_text_pdf(pdf_path, output_path):
    """Extract text from a text-based PDF using pdftotext."""
    filename = os.path.basename(pdf_path)
    print(f"\n  📄 Extracting: {filename}")
    
    result = subprocess.run(
        ['pdftotext', '-layout', pdf_path, output_path],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        size = os.path.getsize(output_path)
        print(f"    ✅ Saved: {output_path} ({size} bytes)")
        return True
    else:
        print(f"    ❌ Failed: {result.stderr}")
        return False


def make_safe_filename(name):
    """Convert PDF name to a safe text filename."""
    base = os.path.splitext(name)[0]
    # Replace special chars
    safe = base.lower()
    safe = safe.replace(' ', '_')
    safe = safe.replace('(', '').replace(')', '')
    safe = safe.replace('__', '_').replace('___', '_')
    safe = safe.strip('_')
    return safe + '.txt'


if __name__ == '__main__':
    print("=" * 70)
    print("  Rule Document OCR & Text Extraction")
    print("=" * 70)
    
    rules_dir = os.path.abspath(RULES_DIR)
    ocr_dir = os.path.abspath(OUTPUT_DIR)
    raw_dir = os.path.abspath(RAW_DIR)
    
    os.makedirs(ocr_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)
    
    # Step 1: OCR scanned documents
    print("\n  STEP 1: OCR Scanned Documents")
    print("  " + "-" * 40)
    
    ocr_results = {}
    for doc_name in SCANNED_DOCS:
        pdf_path = os.path.join(rules_dir, doc_name)
        if not os.path.exists(pdf_path):
            print(f"\n  ⚠️  Not found: {doc_name}")
            continue
        
        output_name = make_safe_filename(doc_name)
        output_path = os.path.join(ocr_dir, output_name)
        
        success = ocr_scanned_pdf(pdf_path, output_path)
        ocr_results[doc_name] = success
    
    # Step 2: Extract text-based documents
    print("\n\n  STEP 2: Extract Text-Based Documents")
    print("  " + "-" * 40)
    
    text_results = {}
    for doc_name in TEXT_DOCS:
        pdf_path = os.path.join(rules_dir, doc_name)
        if not os.path.exists(pdf_path):
            print(f"\n  ⚠️  Not found: {doc_name}")
            continue
        
        output_name = make_safe_filename(doc_name)
        output_path = os.path.join(raw_dir, 'rules_' + output_name)
        
        success = extract_text_pdf(pdf_path, output_path)
        text_results[doc_name] = success
    
    # Summary
    print("\n\n  " + "=" * 50)
    print("  SUMMARY")
    print("  " + "=" * 50)
    
    print("\n  OCR'd Scanned Documents:")
    for doc, ok in ocr_results.items():
        status = "✅" if ok else "❌"
        print(f"    {status} {doc}")
    
    print("\n  Extracted Text Documents:")
    for doc, ok in text_results.items():
        status = "✅" if ok else "❌"
        print(f"    {status} {doc}")
    
    # Show extracted text previews
    print("\n\n  TEXT PREVIEWS")
    print("  " + "-" * 50)
    
    for dir_path in [ocr_dir, raw_dir]:
        for fname in sorted(os.listdir(dir_path)):
            if fname.endswith('.txt') and ('rules_' in fname or fname in [make_safe_filename(d) for d in SCANNED_DOCS]):
                fpath = os.path.join(dir_path, fname)
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                preview = content[:300].replace('\n', ' | ')
                print(f"\n  📄 {fname} ({len(content)} chars)")
                print(f"     Preview: {preview[:200]}...")
    
    print("\n" + "=" * 70)
    print("  Done!")
    print("=" * 70)
