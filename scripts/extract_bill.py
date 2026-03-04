#!/usr/bin/env python3
"""
Phase 1: Medical Bill Data Extraction Pipeline
=================================================
Extracts structured data from scanned medical bill PDFs using a
hybrid approach: Tesseract OCR + Local LLM (Ollama/Phi-3).

Pipeline:
  Scanned PDF → Page Images → OCR (Tesseract) → Structured Extraction (LLM)
             → Validated JSON → Database

The LLM is used to intelligently parse the messy OCR output into a
clean, structured format. All processing is 100% local — no API calls.
"""

import os
import re
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

# Check dependencies
try:
    from pdf2image import convert_from_path
    from paddleocr import PaddleOCR
    import numpy as np
    from PIL import Image, ImageFilter, ImageEnhance
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip3 install pdf2image paddleocr numpy Pillow")
    sys.exit(1)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("Warning: 'requests' not installed. LLM structuring disabled.")
    print("Install with: pip3 install requests")

# Paths
PROJECT_DIR = os.path.join(os.path.dirname(__file__), '..')
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'processed', 'medical_bills.db')
OCR_OUTPUT_DIR = os.path.join(PROJECT_DIR, 'data', 'ocr_output')
OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "phi3:3.8b"


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class PatientInfo:
    name: str = ""
    age: str = ""
    gender: str = ""
    uhid: str = ""                  # Hospital patient ID
    ip_number: str = ""             # Inpatient number
    employee_id: str = ""
    relationship: str = ""          # SELF, SPOUSE, etc.

@dataclass
class HospitalInfo:
    name: str = ""
    address: str = ""
    city: str = ""
    phone: str = ""
    registration_number: str = ""

@dataclass
class AdmissionInfo:
    admission_date: str = ""
    admission_time: str = ""
    discharge_date: str = ""
    discharge_time: str = ""
    days_stayed: int = 0
    ward_type: str = ""             # General, Semi-Private, Private, ICU
    diagnosis: str = ""
    procedures: List[str] = field(default_factory=list)
    referring_doctor: str = ""
    treating_doctor: str = ""

@dataclass 
class LineItem:
    item_type: str = ""             # CONSULTATION, ROOM_RENT, PROCEDURE, etc.
    description: str = ""
    quantity: int = 1
    unit_rate: float = 0
    amount: float = 0
    date: str = ""

@dataclass
class ExtractedBill:
    """Complete structured representation of a medical bill."""
    # Source info
    source_file: str = ""
    total_pages: int = 0
    extraction_timestamp: str = ""
    
    # Extracted data
    patient: PatientInfo = field(default_factory=PatientInfo)
    hospital: HospitalInfo = field(default_factory=HospitalInfo)
    admission: AdmissionInfo = field(default_factory=AdmissionInfo)
    line_items: List[LineItem] = field(default_factory=list)
    
    # Financial summary
    total_amount: float = 0
    discount: float = 0
    net_amount: float = 0
    advance_paid: float = 0
    balance_due: float = 0
    
    # Metadata
    bill_number: str = ""
    bill_date: str = ""
    ocr_confidence: float = 0
    extraction_method: str = ""     # OCR_ONLY, OCR_LLM, MANUAL
    raw_ocr_text: str = ""
    
    def to_dict(self):
        d = asdict(self)
        # Don't include raw OCR text in dict output (too large)
        d.pop('raw_ocr_text', None)
        return d
    
    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ============================================================================
# OCR Engine
# ============================================================================

class OCREngine:
    """PaddleOCR-based text extraction for scanned medical bills."""
    
    def __init__(self, dpi: int = 150, lang: str = 'en', max_pages: int = 0):
        self.dpi = dpi
        self.lang = lang
        self.max_pages = max_pages  # 0 = all pages
        # Suppress Paddle logging
        import logging
        logging.getLogger('ppocr').setLevel(logging.ERROR)
        logging.getLogger('paddle').setLevel(logging.ERROR)
        
        # Initialize PaddleOCR engine with PP-OCRv4 (much lighter than v5/PaddleX)
        self.ocr = PaddleOCR(use_textline_orientation=False, lang=self.lang, ocr_version='PP-OCRv4')
    
    def _repair_pdf(self, pdf_path: str) -> str:
        """Repair a damaged PDF using Ghostscript."""
        repaired_path = pdf_path.rsplit('.', 1)[0] + '_repaired.pdf'
        try:
            result = subprocess.run(
                ['gs', '-dBATCH', '-dNOPAUSE', '-dQUIET', '-sDEVICE=pdfwrite',
                 f'-sOutputFile={repaired_path}', pdf_path],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and os.path.exists(repaired_path):
                print(f"    📝 PDF repaired with Ghostscript → {os.path.basename(repaired_path)}")
                return repaired_path
        except FileNotFoundError:
            print("    ⚠️ Ghostscript (gs) not available for PDF repair")
        except Exception as e:
            print(f"    ⚠️ PDF repair failed: {e}")
        return pdf_path
    
    def pdf_to_images(self, pdf_path: str, first_page: int = None, last_page: int = None) -> List[Image.Image]:
        """Convert PDF pages to images. Falls back to Ghostscript repair for damaged PDFs.
        
        MEMORY: Uses JPEG format (1-2MB/page) instead of default PPM (25MB/page).
        Also limits poppler to 1 thread to avoid memory spikes.
        """
        kwargs = {
            'dpi': self.dpi,
            'fmt': 'jpeg',        # ~10x less memory than PPM
            'thread_count': 1,    # prevent memory spikes from parallel rendering
        }
        if first_page:
            kwargs['first_page'] = first_page
        if last_page:
            kwargs['last_page'] = last_page
        
        try:
            return convert_from_path(pdf_path, **kwargs)
        except Exception as e:
            print(f"    pdf2image failed: {e}")
            print(f"    Attempting PDF repair...")
            repaired = self._repair_pdf(pdf_path)
            if repaired != pdf_path:
                return convert_from_path(repaired, **kwargs)
            raise
    
    def preprocess_image(self, img: Image.Image) -> Image.Image:
        """Minimal preprocessing — grayscale only.
        
        PERF: Tesseract's LSTM engine (OEM 1) handles contrast and noise
        internally. Contrast boost + sharpen + upscale was adding 200-400ms
        per page with minimal quality benefit on printed hospital bills.
        """
        img = img.convert('RGB')
        # Only upscale very small images (mobile photos, cropped scans)
        w, h = img.size
        if w < 800:
            scale = 800 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
        return img
    
    def ocr_image(self, img: Image.Image) -> Dict:
        """OCR a single image using PaddleOCR, returning text and confidence."""
        processed = self.preprocess_image(img)
        # PaddleOCR expects a numpy array
        img_array = np.array(processed)
        
        # Run OCR
        res = list(self.ocr.predict(img_array))
        
        text_lines = []
        confidences = []
        boxes = []
        
        if res and len(res) > 0:
            result_dict = res[0]
            if 'rec_texts' in result_dict and 'rec_polys' in result_dict:
                texts = result_dict['rec_texts']
                scores = result_dict['rec_scores']
                polys = result_dict['rec_polys']
                
                for i in range(len(texts)):
                    txt = texts[i]
                    conf = scores[i]
                    coords = polys[i] # shape (4, 2)
                    
                    # Compute center Y and left X for document layout reconstruction
                    y_c = (coords[0][1] + coords[2][1]) / 2.0
                    x_l = min(coords[0][0], coords[3][0])
                    boxes.append((y_c, x_l, txt, conf))
        
        # Group text blocks into lines based on Y-coordinate tolerance
        boxes.sort(key=lambda b: b[0])  # Sort by Y first
        
        current_y = None
        current_line = []
        Y_TOLERANCE = 15  # pixels
        
        for b in boxes:
            y_c, x_l, txt, conf = b
            confidences.append(conf)
            
            if current_y is None:
                current_y = y_c
                current_line.append(b)
            elif abs(y_c - current_y) <= Y_TOLERANCE:
                current_line.append(b)
                # update rolling average for current_y
                current_y = (current_y * (len(current_line)-1) + y_c) / len(current_line)
            else:
                # Sort line by X and join
                current_line.sort(key=lambda v: v[1])
                text_lines.append("   ".join(v[2] for v in current_line))
                current_line = [b]
                current_y = y_c
                
        if current_line:
            current_line.sort(key=lambda v: v[1])
            text_lines.append("   ".join(v[2] for v in current_line))
        
        text = '\n'.join(text_lines)
        # Convert 0-1 confidence to 0-100%
        avg_conf = sum(confidences) / len(confidences) * 100 if confidences else 0
        
        return {
            'text': text,
            'confidence': avg_conf,
        }
    

    
    def extract_from_pdf(self, pdf_path: str) -> Dict:
        """Extract all text from a PDF.
        
        MEMORY-SAFE: Processes in batches of 5 pages to keep peak memory
        at ~55MB regardless of PDF length. A 120-page bill at 200 DPI would
        need ~1.3GB if all pages were rendered at once.
        
        Strategy:
        1. pdfinfo_from_path() — gets page count without rendering (zero RAM)
        2. Batch loop: convert 5 pages → OCR each → free all → next batch
        3. Only converts pages that will actually be OCR'd (respects max_pages)
        """
        import gc
        
        BATCH_SIZE = 10  # pages per poppler invocation — fewer calls = faster
        
        print(f"    Converting PDF to images (DPI={self.dpi})...")
        
        # Step 1: Get page count WITHOUT rendering any images
        try:
            from pdf2image.pdf2image import pdfinfo_from_path
            info = pdfinfo_from_path(pdf_path)
            total_pages = info.get('Pages', 0)
        except Exception:
            # Fallback: render just page 1 to probe
            probe = self.pdf_to_images(pdf_path, first_page=1, last_page=1)
            total_pages = len(probe) if probe else 0
            for p in probe:
                p.close()
            del probe
        
        if total_pages == 0:
            raise RuntimeError("Could not read any pages from PDF")
        
        print(f"    Found {total_pages} pages")
        
        # Step 2: Determine how many pages to actually process
        pages_to_process = total_pages
        if self.max_pages > 0 and total_pages > self.max_pages:
            pages_to_process = self.max_pages
            print(f"    Limiting OCR to first {pages_to_process} pages (of {total_pages})")
        
        # Step 3: Process in batches with PARALLEL OCR within each batch
        from concurrent.futures import ThreadPoolExecutor
        
        all_text = [None] * pages_to_process
        total_conf = 0
        
        for batch_start in range(1, pages_to_process + 1, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE - 1, pages_to_process)
            batch_count = batch_end - batch_start + 1
            
            # Render this batch
            images = self.pdf_to_images(
                pdf_path, first_page=batch_start, last_page=batch_end
            )
            
            print(f"    OCR pages {batch_start}-{batch_end} ({batch_count} pages)...", flush=True)
            
            for local_idx, img in enumerate(images):
                page_num = batch_start + local_idx
                global_idx = page_num - 1
                
                result = self.ocr_image(img)
                img.close()
                
                all_text[global_idx] = f"--- PAGE {page_num} ---\n{result['text']}"
                total_conf += result['confidence']
                chars = len(result['text'])
                conf = result['confidence']
                print(f"      Page {page_num}: {chars} chars, conf={conf:.0f}%")
            
            del images
            gc.collect()
        
        processed_count = sum(1 for t in all_text if t is not None)
        avg_confidence = total_conf / processed_count if processed_count else 0
        full_text = '\n\n'.join(t for t in all_text if t is not None)
        
        return {
            'text': full_text,
            'pages': total_pages,
            'pages_processed': processed_count,
            'avg_confidence': avg_confidence,
        }


# ============================================================================
# Rule-Based Extractor (Fallback when LLM is unavailable)
# ============================================================================

class RuleBasedExtractor:
    """
    Extract structured data from OCR text using regex patterns.
    This is a deterministic fallback when the LLM is not available.
    """
    
    # Date patterns
    DATE_PATTERNS = [
        r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(\d{1,2}\.\d{1,2}\.\d{2,4})',
        r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{2,4})',
    ]
    
    # Amount patterns
    AMOUNT_PATTERNS = [
        r'(?:Rs\.?|INR|₹)[\s:|]*([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)[\s:|]*(?:Rs\.?|INR|₹)',
        r'(?:Total|Amount|Net|Grand|Balance|Due|Payable)[\s:|]*(?:Rs\.?|INR|₹)?[\s:|]*([\d,]+\.?\d*)',
    ]
    
    def extract(self, text: str) -> ExtractedBill:
        """Extract structured data using regex patterns."""
        bill = ExtractedBill()
        bill.extraction_method = 'OCR_ONLY'
        bill.raw_ocr_text = text
        
        # Patient info
        bill.patient = self._extract_patient(text)
        
        # Hospital info
        bill.hospital = self._extract_hospital(text)
        
        # Admission info
        bill.admission = self._extract_admission(text)
        
        # Financial info
        amounts = self._extract_amounts(text)
        bill.total_amount = amounts.get('total', 0)
        bill.net_amount = amounts.get('net', 0)
        bill.advance_paid = amounts.get('advance', 0)
        bill.balance_due = amounts.get('balance', 0)
        bill.discount = amounts.get('discount', 0)
        
        # Line items (basic extraction)
        bill.line_items = self._extract_line_items(text)
        
        # Bill metadata
        bill.bill_number = self._extract_field(text, 
            [r'Invoice\s*#\s*(\S+)',
             r'Bill\s*(?:No|Number|#)[\s.:\-]*(\S+)',
             r'Invoice\s*(?:No|Number|#)[\s.:\-]*(\S+)',
             r'(INV-[A-Z0-9\-]+)'])
        bill.bill_date = self._extract_field(text,
            [r'Bill\s*Date\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
             r'Invoice\s*Date\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
             r'Date\s+of\s+Bill\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'])
        
        # CIL/SECL specific: Extract Employee ID, Grade, CPRMSE card no
        bill.patient.employee_id = self._extract_field(text, [
            r'(?:Employee|Emp\s*No|EIS/NEIS)[\s.:]*(?:of\s+Employee)?[\s.:\-]*(\d{8,})',
            r'(?:Shri|Mr\.?)\s+[A-Za-z\s]+\((\d{8,})\)',
        ])
        
        # Extract referral number
        referral = self._extract_field(text, [
            r'Referral\s*(?:No|Number)\s*[.:]*\s*([\w/\-]+)',
        ])
        if referral:
            bill.admission.referring_doctor = f"Ref: {referral}"
        
        # Extract total bill amount from cover letter (often the most reliable source)
        cover_total = self._extract_field(text, [
            r'[Tt]otal[\s:|]*([\d,]+\.\d{2})',
            r'In\s*Words?\s*[.:]*\s*([A-Za-z\s]+(?:Lacs?|Lakhs?|Thousand)[A-Za-z\s]+Only)',
        ])
        # Try for the larger total from breakdown table
        large_total_match = re.search(
            r'(\d[\d,]+)\.\d{2}\|\s*\d[\d,]+\.\d{2}\s*\|\s*$',
            text, re.MULTILINE
        )
        if large_total_match:
            try:
                large_total = float(large_total_match.group(1).replace(',', ''))
                if large_total > bill.total_amount:
                    bill.total_amount = large_total
            except ValueError:
                pass
        
        # Better total from "PO Total" or similar summary lines
        po_total = self._extract_field(text, [
            r'(?:PO|Grand)\s*Total[\s:|]*([\d,]+\.?\d*)',
            r'(?:PO|Grand)\s*Total[\s.:|]*(?:Rs\.?)?[\s|]*([\d,]+\.?\d*)',
            r'[Tt]otal[\s:|]*([\d,]+\.\d{2})',
        ])
        if po_total:
            try:
                total_val = float(po_total.replace(',', ''))
                if total_val > bill.total_amount:
                    bill.total_amount = total_val
            except ValueError:
                pass
        
        return bill
    
    def _extract_field(self, text: str, patterns: List[str]) -> str:
        """Try multiple patterns, return first match."""
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""
    
    def _extract_patient(self, text: str) -> PatientInfo:
        """Extract patient information."""
        p = PatientInfo()

        # Priority order matters — explicit labelled fields are most reliable.
        # The SECL cover-letter format says:
        #   "Medical Bill Payment of Mr. Anil Kumar Pandey"
        # The hospital bill page 2 says:
        #   "Patient Name- Mr. Anil Kumar Pandey"
        # Bare "Mr." on a garbled OCR page is the LEAST reliable.
        # NAME_END_LOOKAHEAD: pattern of what always follows a name in these bills
        # — a field label, colon, or end-of-line
        _NAME_END = r'(?=\s*(?:Bill|Date|MRN|UHID|Age|Ward|Ref|DOB|IP|INV|Inv|\d|\n|$))'

        p.name = self._extract_field(text, [
            # 1. Explicit 'Patient Name' label (highest priority)
            r'Patient\s*Name\s*[-:\s]*(?:Mr\.?|Mrs?\.?|Ms\.?|Shri\.?|Smt\.?)?\s*([A-Z][A-Za-z]+(?: [A-Za-z]+){1,4})' + _NAME_END,
            # 2. Name of the Patient label
            r'Name\s+of\s+(?:Patient|the\s+Patient)\s*[-:\s]*(?:Mr\.?|Mrs?\.?|Ms\.?)?\s*([A-Z][A-Za-z]+(?: [A-Za-z]+){1,4})' + _NAME_END,
            # 3. SECL cover-letter: "Medical Bill Payment of Mr. Anil Kumar Pandey"
            r'Bill\s+Payment\s+of\s+(?:Mr\.?|Mrs?\.?|Ms\.?|Shri\.?|Smt\.?)\s*([A-Z][A-Za-z]+(?: [A-Za-z]+){1,3})(?=\s*[,\n.\t]|$)',
            # 4. Generic "Payment of Mr. ..."
            r'Payment\s+of\s+(?:Mr\.?|Mrs?\.?|Ms\.?|Shri\.?|Smt\.?)\s*([A-Z][A-Za-z]+(?: [A-Za-z]+){1,3})(?=\s*[,\n.\t]|$)',
            # 5. Shri/Smt with employee number: "Shri Anil Kumar Pandey (90262908)"
            r'Shri\s+([A-Z][A-Za-z]+(?: [A-Za-z]+){1,3})\s*\(\d{7,}\)',
            # 6. Mr./Mrs. prefix — only if followed by properly-cased words then line end / non-word
            r'(?:Mr\.?|Mrs?\.?|Ms\.?)\s+([A-Z][a-z]{2,}(?: [A-Z][a-z]{2,}){1,3})(?=[^\w]|\n|$)',
            # 7. Tabular layout catch when Name label is on the row above the actual value
            r'(?:Patient\s+Name[^\n]*)[\r\n]+(?:\d+:\s*)?([A-Z]{3,}(?:\s+[A-Z]{2,})*)',
        ])

        p.age = self._extract_field(text, [
            r'Age\s*[-:]*\s*(\d{1,3}\s*(?:Y(?:ears?|rs?)?|M(?:onths?)?|D(?:ays?)?))',
            r'(\d{1,3})\s*(?:years?|yrs?)\s*(?:old)?',
        ])

        p.gender = self._extract_field(text, [
            r'(?:Sex|Gender)\s*[-:]*\s*(Male|Female|M|F)(?:\b|\W)',
        ])

        p.uhid = self._extract_field(text, [
            # Common hospital MRN formats: BMC0049654, MRN-12345, UHID: 12345
            r'(?:UHID|MRN(?:No)?|MR\s*No|Patient\s*ID|Reg\s*No)[\s.:\-]*((?:[A-Z]{2,5})?-?\d{4,})',
            r'MRN-\s*(\w+)',
        ])

        p.ip_number = self._extract_field(text, [
            r'(?:IP\s*No|IPD\s*No|Admission\s*No|Indoor\s*No)\s*[-:]*\s*(\S+)',
        ])

        return p
    
    def _extract_hospital(self, text: str) -> HospitalInfo:
        """Extract hospital information."""
        h = HospitalInfo()

        # --- Hospital Name ---
        # Strategy 1: Look for a clean line containing hospital keywords,
        #   stripped of leading OCR symbols (@, *, #, unicode noise).
        #   Search through early pages (up to 300 lines).
        lines = text.split('\n')
        best_name = ''
        for line in lines[:300]:
            raw = line.strip()
            if not raw:
                continue
            # Strip leading non-alphabetic symbols (OCR garbage like '@ ', '} ', etc.)
            cleaned = re.sub(r'^[^A-Za-z]+', '', raw).strip()
            if not cleaned:
                continue
            # Must contain a hospital keyword
            if not re.search(r'(Hospital|Medical|Institute|Centre|Center|Clinic|Healthcare|Nursing|Foundation|Medicare)',
                             cleaned, re.IGNORECASE):
                continue
            # Must NOT be a patient/bill line
            if re.search(r'(Patient|Bill\s+No|Discharge|Admission|Date|Invoice|Amount|UHID|MRN)',
                         cleaned, re.IGNORECASE):
                continue
            # Prefer shorter, cleaner lines (no special chars)
            if len(cleaned) > 80:
                continue
            # Strip trailing OCR noise (non-word characters)
            cleaned = re.sub(r'[\s@*#|_\u2018\u2019\u201c\u201d\u2014\u2013]+$', '', cleaned).strip()
            if len(cleaned) > 5:
                best_name = cleaned
                break

        h.name = best_name

        # --- City ---
        # Patterns like "Naya Raipur", "Sector-36, Naya Raipur", "ABC City, State"
        # NOTE: every pattern here MUST have exactly one capture group, because
        # _extract_field calls m.group(1). The last pattern is a direct-word match
        # so we wrap the alternatives in a single capturing group.
        h.city = self._extract_field(text, [
            r'(?:Sector[\-\s]*\d+,\s*)([A-Z][A-Za-z\s]+)(?:,|\n)',   # Sector-36, Naya Raipur
            r'(?:Hospital|Medical|Centre)[^,\n]+,\s*([A-Z][A-Za-z\s]+)(?:,|\n)',
            r'([A-Z][A-Za-z\s]+)\s*(?:CG|MP|UP|MH|TN|KA|AP|WB|RJ|GJ|HR|PB)\b',  # city + state abbr
            r'\b(Raipur|Bilaspur|Mumbai|Delhi|Chennai|Bengaluru|Kolkata|Hyderabad'  # direct city names
            r'|Pune|Jaipur|Patna|Lucknow|Bhopal|Indore|Nagpur|Bhubaneswar)\b',
        ]) or ''

        # If city matched the full state-pattern, extract just the city part
        if h.city and re.search(r'\b(CG|MP|UP|MH|TN|KA|AP|WB|RJ|GJ|HR|PB)\b', h.city):
            h.city = re.sub(r'\s*\b(CG|MP|UP|MH|TN|KA|AP|WB|RJ|GJ|HR|PB)\b.*', '', h.city).strip()

        h.phone = self._extract_field(text, [
            r'(?:Ph|Tel|Phone|Contact|Mob)\s*[.:]*\s*([\d\s/\-+,()]{8,})',
        ])

        h.registration_number = self._extract_field(text, [
            r'(?:Reg|Registration)\s*(?:No|Number)\s*[.:]*\s*(\S+)',
        ])

        return h


    def _extract_admission(self, text: str) -> AdmissionInfo:
        """Extract admission details."""
        a = AdmissionInfo()
        
        a.admission_date = self._extract_field(text, [
            r'(?:Admission|Admitted|DOA|Date\s+of\s+Admission)\s*(?:Date)?\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
        ])
        
        a.discharge_date = self._extract_field(text, [
            r'(?:Discharge|DOD|Date\s+of\s+Discharge)\s*(?:Date)?\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
        ])
        
        a.admission_time = self._extract_field(text, [
            r'(?:Admission|Admitted)\s*(?:Time)?\s*[.:]*\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s+(\d{1,2}[:]\d{2}(?:\s*[AP]M)?)',
        ])
        
        a.discharge_time = self._extract_field(text, [
            r'(?:Discharge)\s*(?:Time)?\s*[.:]*\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\s+(\d{1,2}[:]\d{2}(?:\s*[AP]M)?)',
        ])
        
        a.diagnosis = self._extract_field(text, [
            r'(?:Diagnosis|Final\s+Diagnosis|Primary\s+Diagnosis|Provisional\s+Diagnosis)\s*[.:]*\s*(.+?)(?:\n|$)',
        ])
        
        a.ward_type = self._extract_field(text, [
            r'(?:Ward|Room|Bed)\s*(?:Type|Category)?\s*[.:]*\s*(General|Private|Semi|Deluxe|Suite|ICU|HDU|NICU|Twin)',
        ])
        
        a.treating_doctor = self._extract_field(text, [
            r'(?:Treating|Attending|Consultant)\s*(?:Doctor|Physician|Surgeon)\s*[.:]*\s*(?:Dr\.?\s*)?([A-Z][A-Za-z\s.]+?)(?:\s{2,}|\n)',
        ])
        
        return a
    
    def _extract_amounts(self, text: str) -> Dict[str, float]:
        """Extract financial amounts."""
        amounts = {}
        
        def parse_amount(s: str) -> float:
            try:
                return float(s.replace(',', ''))
            except (ValueError, TypeError):
                return 0
        
        # Total amount
        total_patterns = [
            r'(?:Grand\s+Total|Total\s+Bill|Total\s+Amount|Gross\s+Amount)\s*[.:]*\s*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
            r'(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)\s*(?:is the total)',
        ]
        for pat in total_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                amounts['total'] = parse_amount(m.group(1))
                break
        
        # Net amount
        net_patterns = [
            r'(?:Net\s+(?:Amount|Payable|Bill)|Amount\s+Payable|Bill\s+Amount)\s*[.:]*\s*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
        ]
        for pat in net_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                amounts['net'] = parse_amount(m.group(1))
                break
        
        # Advance
        adv_patterns = [
            r'(?:Advance|Deposit|Paid|Payment\s+Received)\s*[.:]*\s*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
        ]
        for pat in adv_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                amounts['advance'] = parse_amount(m.group(1))
                break
        
        # Balance
        bal_patterns = [
            r'(?:Balance|Due|Remaining|Outstanding)\s*(?:Amount)?\s*[.:]*\s*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
        ]
        for pat in bal_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                amounts['balance'] = parse_amount(m.group(1))
                break
        
        # Discount
        disc_patterns = [
            r'(?:Discount|Concession)\s*[.:]*\s*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
        ]
        for pat in disc_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                amounts['discount'] = parse_amount(m.group(1))
                break
        
        return amounts
    
    def _extract_line_items(self, text: str) -> List[LineItem]:
        """Extract line items (basic regex approach)."""
        items = []
        
        # Look for common billing categories
        categories = {
            'ROOM_RENT': [r'Room\s*(?:Rent|Charges?)', r'Bed\s*Charges?', r'Ward\s*Charges?'],
            'CONSULTATION': [r'Consultation\s*(?:Fee|Charges?)', r'Doctor\s*(?:Fee|Charges?|Visit)'],
            'PROCEDURE': [r'(?:Surgery|Surgical|Procedure|Operation)\s*Charges?', r'OT\s*Charges?'],
            'INVESTIGATION': [r'(?:Lab|Laboratory|Investigation|Pathology|Radiology|Imaging)\s*Charges?',
                            r'(?:X-Ray|MRI|CT\s*Scan|Ultrasound|ECG|EEG)\s*Charges?'],
            'MEDICINE': [r'(?:Medicine|Pharmacy|Drug)\s*Charges?', r'Pharma\s*Charges?'],
            'CONSUMABLE': [r'(?:Consumable|Disposable|Surgical\s*Items?)\s*Charges?'],
            'NURSING': [r'Nursing\s*Charges?'],
            'ICU': [r'ICU\s*Charges?', r'(?:Intensive\s*Care|Critical\s*Care)\s*Charges?'],
            'IMPLANT': [r'Implant\s*(?:Cost|Charges?)', r'Prosthesis'],
            'BLOOD_TRANSFUSION': [r'Blood\s*(?:Transfusion|Bank)\s*Charges?'],
            'AMBULANCE': [r'Ambulance\s*Charges?'],
            'OTHER': [r'Miscellaneous\s*Charges?', r'Other\s*Charges?', r'Sundry'],
        }
        
        for item_type, patterns in categories.items():
            for pat in patterns:
                # Look for pattern followed by an amount
                full_pat = pat + r'\s*[.:]*\s*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)'
                for m in re.finditer(full_pat, text, re.IGNORECASE):
                    amount_str = m.group(1) if m.lastindex >= 1 else ''
                    try:
                        amount = float(amount_str.replace(',', ''))
                    except (ValueError, TypeError):
                        amount = 0
                    
                    if amount > 0:
                        items.append(LineItem(
                            item_type=item_type,
                            description=m.group(0).strip(),
                            amount=amount,
                        ))
                        break  # One match per category is enough
        
        return items


# ============================================================================
# LLM-Based Extractor (Ollama/Phi-3)
# ============================================================================

class LLMExtractor:
    """
    Uses a local LLM (via Ollama) to structure OCR text into clean JSON.
    The LLM is much better at handling messy OCR output than pure regex.
    """
    
    def __init__(self, model: str = DEFAULT_MODEL, base_url: str = OLLAMA_URL):
        self.model = model
        self.base_url = base_url
    
    def is_available(self) -> bool:
        """Check if Ollama is running and model is available."""
        if not HAS_REQUESTS:
            return False
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=2)
            if resp.status_code == 200:
                models = resp.json().get('models', [])
                model_names = [m['name'] for m in models]
                # Check if our model (or variant) is available
                return any(self.model.split(':')[0] in name for name in model_names)
            return False
        except Exception:
            return False

    def extract(self, ocr_text: str, rule_extractor: RuleBasedExtractor = None) -> ExtractedBill:
        """
        Use LLM to structure OCR text.
        First gets a rule-based extraction, then uses LLM to refine/fill gaps.
        """
        # Start with rule-based extraction as baseline
        if rule_extractor:
            bill = rule_extractor.extract(ocr_text)
        else:
            bill = ExtractedBill()
        
        bill.extraction_method = 'OCR_LLM'
        bill.raw_ocr_text = ocr_text
        
        # Build LLM prompt
        prompt = self._build_extraction_prompt(ocr_text)
        
        # Call LLM
        print("    Sending to LLM for structured extraction...")
        llm_response = self._call_ollama(prompt)
        
        if llm_response:
            # Parse LLM JSON response
            structured = self._parse_llm_response(llm_response)
            if structured:
                bill = self._merge_extractions(bill, structured)
                print("    ✅ LLM extraction successful")
            else:
                print("    ⚠️ LLM response could not be parsed, using rule-based extraction")
        else:
            print("    ⚠️ LLM call failed, using rule-based extraction")
            bill.extraction_method = 'OCR_ONLY'
        
        return bill
    
    def _select_key_pages(self, ocr_text: str, max_chars: int = 4500) -> str:
        """
        Intelligently select the most informative pages from OCR text.
        Instead of blindly truncating, picks pages with the richest data:
        - First 2 pages (cover letter, summary, patient details)
        - Pages with financial keywords (bill summary, total, discharge)
        - Last 2 pages (often contain bill totals, signatures)
        """
        # Split into pages by our page markers
        pages = re.split(r'--- PAGE (\d+) ---', ocr_text)

        # Build page list: [(page_num, text), ...]
        # re.split with a capturing group produces: ['pre', '1', 'text1', '2', 'text2', ...]
        page_list = []
        i = 1
        while i < len(pages) - 1:
            try:
                page_num = int(pages[i])
            except (ValueError, IndexError):
                i += 1
                continue
            page_text = pages[i + 1].strip() if (i + 1) < len(pages) else ''
            page_list.append((page_num, page_text))
            i += 2
        
        if not page_list:
            # No page markers found, just truncate
            return ocr_text[:max_chars]
        
        # Score each page by information richness
        financial_keywords = [
            r'total', r'grand\s*total', r'net\s*(?:amount|payable)', r'bill\s*(?:no|number|date)',
            r'invoice', r'(?:admission|discharge)\s*date', r'diagnosis', r'procedure',
            r'consultation', r'room\s*(?:rent|charges)', r'surgery', r'package',
            r'advance', r'balance', r'discount', r'rupees?|rs\.?|₹|inr',
            r'discharge\s*summary', r'patient\s*name', r'hospital', r'UHID|MRN|IP\s*No',
            r'ward', r'icu', r'ot\s*charges', r'anesthesia', r'blood\s*bank',
        ]
        
        page_scores = []
        for page_num, text in page_list:
            score = 0
            text_lower = text.lower()
            for kw in financial_keywords:
                if re.search(kw, text_lower):
                    score += 1
            # Bonus for pages with large amounts
            amounts = re.findall(r'[\d,]{4,}\.?\d{0,2}', text)
            score += min(len(amounts), 5)
            # Bonus for first and last pages
            if page_num <= 2:
                score += 10
            if page_num >= page_list[-1][0] - 1:
                score += 5
            page_scores.append((page_num, text, score))
        
        # Sort by score descending, pick top pages that fit in max_chars
        page_scores.sort(key=lambda x: x[2], reverse=True)
        
        selected = []
        remaining_chars = max_chars
        for page_num, text, score in page_scores:
            page_content = f"--- PAGE {page_num} ---\n{text}"
            if len(page_content) <= remaining_chars:
                selected.append((page_num, page_content))
                remaining_chars -= len(page_content)
            if remaining_chars < 200:
                break
        
        # Sort selected pages by page number for coherent reading
        selected.sort(key=lambda x: x[0])
        
        result = "\n\n".join(content for _, content in selected)
        omitted = len(page_list) - len(selected)
        if omitted > 0:
            result += f"\n\n[NOTE: {omitted} lower-relevance pages omitted to fit context]"
        
        return result

    def _build_extraction_prompt(self, ocr_text: str) -> str:
        """Build a structured extraction prompt with smart page selection."""
        # Use intelligent page selection instead of blind truncation
        focused_text = self._select_key_pages(ocr_text, max_chars=4500)
        
        prompt = f"""You are a medical bill data extraction assistant for Indian hospital bills (CIL/CPRMSE scheme). Extract structured information from this OCR text.

OCR TEXT (key pages selected):
```
{focused_text}
```

Extract information and return ONLY a valid JSON object with this structure:
{{
  "patient_name": "",
  "patient_age": "",
  "patient_gender": "",
  "patient_uhid": "",
  "patient_ip_number": "",
  "employee_id": "",
  "hospital_name": "",
  "hospital_city": "",
  "hospital_phone": "",
  "admission_date": "",
  "discharge_date": "",
  "diagnosis": "",
  "procedures": [],
  "treating_doctor": "",
  "ward_type": "",
  "bill_number": "",
  "bill_date": "",
  "total_amount": 0,
  "discount": 0,
  "net_amount": 0,
  "advance_paid": 0,
  "balance_due": 0,
  "line_items": [
    {{"type": "CONSULTATION/PROCEDURE/ROOM_RENT/ICU/MEDICINE/INVESTIGATION/CONSUMABLE/IMPLANT/OTHER", "description": "", "amount": 0}}
  ]
}}

Rules: Use exact numbers from the bill. For dates use DD/MM/YYYY. Return ONLY JSON."""
        
        return prompt
    
    def _call_ollama(self, prompt: str) -> Optional[str]:
        """Call Ollama API."""
        if not HAS_REQUESTS:
            return None
        
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    'model': self.model,
                    'prompt': prompt,
                    'stream': False,
                    'options': {
                        'temperature': 0.1,      # Low temp for factual extraction
                        'num_predict': 2048,     # Max output tokens
                        'top_p': 0.9,
                    }
                },
                timeout=120  # 2 minutes max
            )
            
            if resp.status_code == 200:
                return resp.json().get('response', '')
            else:
                print(f"    Ollama error: {resp.status_code} - {resp.text[:200]}")
                return None
        except requests.exceptions.ConnectionError:
            print("    Ollama not running. Start with: ollama serve")
            return None
        except Exception as e:
            print(f"    LLM error: {e}")
            return None
    
    def _parse_llm_response(self, response: str) -> Optional[Dict]:
        """Parse JSON from LLM response (handling markdown code blocks etc)."""
        # Try direct JSON parse
        response = response.strip()
        
        # Remove markdown code blocks if present
        if '```json' in response:
            response = response.split('```json')[1].split('```')[0]
        elif '```' in response:
            response = response.split('```')[1].split('```')[0]
        
        response = response.strip()
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            # Try to find JSON object in response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass
        
        return None
    
    def _merge_extractions(self, bill: ExtractedBill, llm_data: Dict) -> ExtractedBill:
        """Merge LLM extraction into the bill, preferring LLM for non-empty fields."""
        
        # Patient info
        if llm_data.get('patient_name'):
            bill.patient.name = llm_data['patient_name']
        if llm_data.get('patient_age'):
            bill.patient.age = str(llm_data['patient_age'])
        if llm_data.get('patient_gender'):
            bill.patient.gender = llm_data['patient_gender']
        if llm_data.get('patient_uhid'):
            bill.patient.uhid = llm_data['patient_uhid']
        if llm_data.get('patient_ip_number'):
            bill.patient.ip_number = llm_data['patient_ip_number']
        
        # Hospital
        if llm_data.get('hospital_name'):
            bill.hospital.name = llm_data['hospital_name']
        if llm_data.get('hospital_city'):
            bill.hospital.city = llm_data['hospital_city']
        
        # Admission
        if llm_data.get('admission_date'):
            bill.admission.admission_date = llm_data['admission_date']
        if llm_data.get('admission_time'):
            bill.admission.admission_time = llm_data['admission_time']
        if llm_data.get('discharge_date'):
            bill.admission.discharge_date = llm_data['discharge_date']
        if llm_data.get('discharge_time'):
            bill.admission.discharge_time = llm_data['discharge_time']
        if llm_data.get('diagnosis'):
            bill.admission.diagnosis = llm_data['diagnosis']
        if llm_data.get('procedures'):
            bill.admission.procedures = llm_data['procedures']
        if llm_data.get('treating_doctor'):
            bill.admission.treating_doctor = llm_data['treating_doctor']
        if llm_data.get('ward_type'):
            bill.admission.ward_type = llm_data['ward_type']
        
        # Financials — prefer LLM if it found non-zero values
        if llm_data.get('total_amount', 0) > 0:
            bill.total_amount = float(llm_data['total_amount'])
        if llm_data.get('net_amount', 0) > 0:
            bill.net_amount = float(llm_data['net_amount'])
        if llm_data.get('discount', 0) > 0:
            bill.discount = float(llm_data['discount'])
        if llm_data.get('advance_paid', 0) > 0:
            bill.advance_paid = float(llm_data['advance_paid'])
        if llm_data.get('balance_due', 0) > 0:
            bill.balance_due = float(llm_data['balance_due'])
        
        # Bill metadata
        if llm_data.get('bill_number'):
            bill.bill_number = llm_data['bill_number']
        if llm_data.get('bill_date'):
            bill.bill_date = llm_data['bill_date']
        
        # Line items from LLM
        llm_items = llm_data.get('line_items', [])
        if llm_items and isinstance(llm_items, list):
            bill.line_items = []
            for item in llm_items:
                if isinstance(item, dict):
                    bill.line_items.append(LineItem(
                        item_type=item.get('type', 'OTHER'),
                        description=item.get('description', ''),
                        amount=float(item.get('amount', 0)),
                    ))
        
        return bill


# ============================================================================
# Main Pipeline
# ============================================================================

class BillExtractionPipeline:
    """End-to-end bill extraction pipeline."""
    
    def __init__(self, model: str = DEFAULT_MODEL):
        self.ocr = OCREngine()
        self.rule_extractor = RuleBasedExtractor()
        self.llm_extractor = LLMExtractor(model=model)
        self.db_path = os.path.abspath(DB_PATH)
    
    def process_bill(self, pdf_path: str, use_llm: bool = True) -> ExtractedBill:
        """Process a single bill PDF end-to-end."""
        pdf_path = os.path.abspath(pdf_path)
        filename = os.path.basename(pdf_path)
        
        print(f"\n{'='*60}")
        print(f"  Processing: {filename}")
        print(f"{'='*60}")
        
        # Step 1: OCR
        print(f"\n  📷 Step 1: OCR Extraction")
        ocr_result = self.ocr.extract_from_pdf(pdf_path)
        ocr_text = ocr_result['text']
        
        print(f"    Total text: {len(ocr_text)} chars, avg confidence: {ocr_result['avg_confidence']:.0f}%")
        
        # Save raw OCR output
        ocr_output_path = os.path.join(
            os.path.abspath(OCR_OUTPUT_DIR), 
            f"bill_{os.path.splitext(filename)[0]}.txt"
        )
        os.makedirs(os.path.dirname(ocr_output_path), exist_ok=True)
        with open(ocr_output_path, 'w', encoding='utf-8') as f:
            f.write(ocr_text)
        print(f"    OCR saved: {ocr_output_path}")
        
        # Step 2: Structured Extraction
        print(f"\n  🔍 Step 2: Structured Extraction")
        
        if use_llm and self.llm_extractor.is_available():
            print(f"    Using LLM: {self.llm_extractor.model}")
            bill = self.llm_extractor.extract(ocr_text, self.rule_extractor)
        else:
            if use_llm:
                print(f"    ⚠️ LLM ({self.llm_extractor.model}) not available, using rule-based extraction")
            else:
                print(f"    Using rule-based extraction (LLM disabled)")
            bill = self.rule_extractor.extract(ocr_text)
        
        # Fill metadata
        bill.source_file = pdf_path
        bill.total_pages = ocr_result['pages']
        bill.ocr_confidence = ocr_result['avg_confidence']
        bill.extraction_timestamp = datetime.now().isoformat()
        
        # Step 3: Post-processing
        print(f"\n  ✨ Step 3: Post-processing")
        bill = self._post_process(bill)
        
        # Step 4: Display results
        self._print_results(bill)
        
        # Step 5: Save to JSON
        json_output_path = os.path.join(
            os.path.abspath(OCR_OUTPUT_DIR),
            f"bill_{os.path.splitext(filename)[0]}_extracted.json"
        )
        with open(json_output_path, 'w', encoding='utf-8') as f:
            f.write(bill.to_json())
        print(f"\n  💾 JSON saved: {json_output_path}")
        
        return bill
    
    @staticmethod
    def _clean_patient_name(raw: str) -> str:
        """
        Post-process a raw extracted patient name to remove OCR noise.

        Rules:
        - Strip leading/trailing whitespace and honorific titles
        - Each word must be all-alphabetic (no digits, no special chars)
        - Each word must be >= 3 characters (eliminates single-char OCR garbage)
        - The cleaned name must have at least 2 words
        - Known OCR noise syllables (e.g., 'ving', 'andty', 'ze') are removed
        """
        # Strip titles / honorifics
        name = re.sub(
            r'^(?:Mr\.?|Mrs?\.?|Ms\.?|Shri\.?|Smt\.?|Dr\.?|Prof\.?)\s*',
            '', raw, flags=re.IGNORECASE
        ).strip()

        # Known OCR noise tokens — lowercase matches
        KNOWN_NOISE = {
            'ving', 'ze', 'ra', 'andty', 'ancy', 'ang', 'ane', 'ng', 'vy',
            'yj', 'jf', 'flt', 'tlt', 'lt', 'lf', 'lft', 'fr', 'ile', 'le',
            'ke', 'jk', 'ji', 'ds', 'sd', 'fn', 'nf', 'xx', 'xz', 'zx',
        }

        # Also strip trailing field-label words that bleed into a name
        LABEL_WORDS = {'bill', 'date', 'ward', 'ref', 'mrn', 'uhid', 'age',
                       'dob', 'gender', 'sex', 'diagnosis', 'admission', 'discharge'}

        clean_words = []
        for w in name.split():
            # Must be all-alphabetic (allow hyphens for double-barrelled names)
            if not re.match(r'^[A-Za-z][A-Za-z\-]*$', w): continue
            # Must be at least 3 characters
            if len(w) < 3: continue
            # Must not be a known noise syllable or field label
            if w.lower() in KNOWN_NOISE: continue
            if w.lower() in LABEL_WORDS: break   # stop at first label word
            clean_words.append(w)

        if len(clean_words) < 2:
            # Cleaning was too aggressive — return the raw best-effort title-cased name
            return raw.strip().title()

        return ' '.join(clean_words).title()

    @staticmethod
    def _clean_hospital_name(raw: str) -> str:
        """
        Strip OCR noise from extracted hospital name.
        - Remove trailing garbage tokens (single chars, numbers, special chars)
        - Collapse internal whitespace
        """
        if not raw:
            return raw
        # Remove unicode noise and non-printable chars
        cleaned = re.sub(r'[\u2018\u2019\u201c\u201d\u2014\u2013\u00e2\u0080\u0099]+', '', raw)
        # Remove leading non-alphabetic characters
        cleaned = re.sub(r'^[^A-Za-z]+', '', cleaned).strip()
        # Split into tokens and drop garbage at the end
        tokens = cleaned.split()
        good_tokens = []
        for t in tokens:
            # Stop at the first token that is clearly garbage:
            # - only 1-2 chars (single letters or numbers)
            # - contains only non-alphabetic characters
            # - looks like a formatting artefact
            stripped = re.sub(r'[^A-Za-z0-9]', '', t)
            if len(stripped) <= 2:
                break
            if re.match(r'^[\d,._\-@|]+$', t):
                break
            good_tokens.append(t)
        return ' '.join(good_tokens).strip()

    def _post_process(self, bill: ExtractedBill) -> ExtractedBill:
        """Clean and validate extracted data."""
        # Clean patient name — remove OCR noise and title-case
        if bill.patient.name:
            bill.patient.name = self._clean_patient_name(bill.patient.name)

        # Clean hospital name — strip trailing OCR garbage
        if bill.hospital.name:
            bill.hospital.name = self._clean_hospital_name(bill.hospital.name)

        # Clean city — take only the first line, strip state abbreviation
        if bill.hospital.city:
            city = bill.hospital.city.split('\n')[0].strip()
            city = re.sub(r'\s*\b(?:SECL|CIL|Ltd|Limited|Pvt)\b.*', '', city, flags=re.IGNORECASE).strip()
            bill.hospital.city = city

        # Calculate length of stay
        if bill.admission.admission_date and bill.admission.discharge_date:
            try:
                for fmt in ['%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y']:
                    try:
                        adm = datetime.strptime(bill.admission.admission_date, fmt)
                        dis = datetime.strptime(bill.admission.discharge_date, fmt)
                        bill.admission.days_stayed = (dis - adm).days
                        break
                    except ValueError:
                        continue
            except Exception:
                pass
        
        # Validate total amount
        if bill.line_items and not bill.total_amount:
            bill.total_amount = sum(item.amount for item in bill.line_items)
        
        # Try to match hospital in database
        if bill.hospital.name:
            self._match_hospital(bill)
        
        return bill
    
    def _match_hospital(self, bill: ExtractedBill):
        """Try to find the hospital in empanelled list."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # Search by name
            rows = c.execute(
                "SELECT hospital_name, city FROM hospitals WHERE hospital_name LIKE ? LIMIT 3",
                (f'%{bill.hospital.name[:20]}%',)
            ).fetchall()
            
            if rows:
                print(f"    🏥 Hospital matched: {rows[0][0]} ({rows[0][1]})")
                if not bill.hospital.city and rows[0][1]:
                    bill.hospital.city = rows[0][1]
            else:
                print(f"    ⚠️ Hospital '{bill.hospital.name}' not found in empanelled list")
            
            conn.close()
        except Exception:
            pass
    
    def _print_results(self, bill: ExtractedBill):
        """Pretty print extracted results."""
        print(f"\n  {'─'*56}")
        print(f"  📋 EXTRACTION RESULTS")
        print(f"  {'─'*56}")
        
        print(f"  Method: {bill.extraction_method}")
        print(f"  OCR Confidence: {bill.ocr_confidence:.0f}%")
        print(f"  Pages: {bill.total_pages}")
        
        print(f"\n  👤 Patient:")
        print(f"     Name:    {bill.patient.name or '(not found)'}")
        print(f"     Age:     {bill.patient.age or '(not found)'}")
        print(f"     Gender:  {bill.patient.gender or '(not found)'}")
        print(f"     UHID:    {bill.patient.uhid or '(not found)'}")
        print(f"     IP No:   {bill.patient.ip_number or '(not found)'}")
        
        print(f"\n  🏥 Hospital:")
        print(f"     Name:    {bill.hospital.name or '(not found)'}")
        print(f"     City:    {bill.hospital.city or '(not found)'}")
        
        print(f"\n  📅 Admission:")
        print(f"     Admitted:   {bill.admission.admission_date or '(not found)'} {bill.admission.admission_time}")
        print(f"     Discharged: {bill.admission.discharge_date or '(not found)'} {bill.admission.discharge_time}")
        print(f"     Stay:       {bill.admission.days_stayed} days")
        print(f"     Diagnosis:  {bill.admission.diagnosis or '(not found)'}")
        print(f"     Ward:       {bill.admission.ward_type or '(not found)'}")
        print(f"     Doctor:     {bill.admission.treating_doctor or '(not found)'}")
        
        print(f"\n  💰 Financial:")
        print(f"     Bill No:     {bill.bill_number or '(not found)'}")
        print(f"     Bill Date:   {bill.bill_date or '(not found)'}")
        print(f"     Total:       ₹{bill.total_amount:,.2f}")
        print(f"     Discount:    ₹{bill.discount:,.2f}")
        print(f"     Net:         ₹{bill.net_amount:,.2f}")
        print(f"     Advance:     ₹{bill.advance_paid:,.2f}")
        print(f"     Balance:     ₹{bill.balance_due:,.2f}")
        
        if bill.line_items:
            print(f"\n  📝 Line Items ({len(bill.line_items)}):")
            for item in bill.line_items:
                print(f"     {item.item_type:20s} ₹{item.amount:>10,.2f}  {item.description[:40]}")
        
        print(f"  {'─'*56}")


# ============================================================================
# CLI Entry Point
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Medical Bill Extraction Pipeline - Phase 1',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process sample bill with LLM
  python3 extract_bill.py "../Sample Bill (1).pdf"
  
  # Process without LLM (rule-based only)
  python3 extract_bill.py "../Sample Bill (1).pdf" --no-llm
  
  # Use a different model
  python3 extract_bill.py "../Sample Bill (1).pdf" --model llama3.2:3b
"""
    )
    parser.add_argument('pdf_path', help='Path to the medical bill PDF')
    parser.add_argument('--no-llm', action='store_true', help='Skip LLM, use rule-based extraction only')
    parser.add_argument('--model', default=DEFAULT_MODEL, help=f'Ollama model name (default: {DEFAULT_MODEL})')
    parser.add_argument('--dpi', type=int, default=150, help='OCR DPI (default: 150)')
    parser.add_argument('--max-pages', type=int, default=20, help='Max pages to OCR (default: 20, 0=all)')
    
    args = parser.parse_args()
    
    pdf_path = args.pdf_path
    if not os.path.isabs(pdf_path):
        pdf_path = os.path.abspath(os.path.join(os.getcwd(), pdf_path))
    
    if not os.path.exists(pdf_path):
        print(f"❌ File not found: {pdf_path}")
        sys.exit(1)
    
    pipeline = BillExtractionPipeline(model=args.model)
    pipeline.ocr.dpi = args.dpi
    pipeline.ocr.max_pages = args.max_pages
    
    bill = pipeline.process_bill(pdf_path, use_llm=not args.no_llm)
    
    print(f"\n{'='*60}")
    print(f"  ✅ Extraction complete!")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
