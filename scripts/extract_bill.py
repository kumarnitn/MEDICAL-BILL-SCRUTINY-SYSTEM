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
    import pytesseract
    from PIL import Image, ImageFilter, ImageEnhance
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip3 install pdf2image pytesseract Pillow")
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
    """Tesseract-based OCR with preprocessing for scanned medical bills."""
    
    def __init__(self, dpi: int = 300, lang: str = 'eng', max_pages: int = 0):
        self.dpi = dpi
        self.lang = lang
        self.max_pages = max_pages  # 0 = all pages
    
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
        """Convert PDF pages to images. Falls back to Ghostscript repair for damaged PDFs."""
        kwargs = {'dpi': self.dpi}
        if first_page:
            kwargs['first_page'] = first_page
        if last_page:
            kwargs['last_page'] = last_page
        
        try:
            return convert_from_path(pdf_path, **kwargs)
        except Exception as e:
            print(f"    ⚠️ pdf2image failed: {e}")
            print(f"    Attempting PDF repair...")
            repaired = self._repair_pdf(pdf_path)
            if repaired != pdf_path:
                return convert_from_path(repaired, **kwargs)
            raise
    
    def preprocess_image(self, img: Image.Image) -> Image.Image:
        """Enhance image for better OCR."""
        # Grayscale
        img = img.convert('L')
        # Enhance contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        # Sharpen
        img = img.filter(ImageFilter.SHARPEN)
        # Upscale small images
        w, h = img.size
        if w < 2000:
            scale = 2000 / w
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        return img
    
    def ocr_image(self, img: Image.Image) -> Dict:
        """OCR a single image, returning text and confidence."""
        processed = self.preprocess_image(img)
        
        # Get text
        text = pytesseract.image_to_string(
            processed, lang=self.lang,
            config='--psm 6 --oem 3'
        )
        
        # Get confidence data
        try:
            data = pytesseract.image_to_data(
                processed, lang=self.lang,
                config='--psm 6 --oem 3',
                output_type=pytesseract.Output.DICT
            )
            confidences = [int(c) for c in data['conf'] if int(c) > 0]
            avg_conf = sum(confidences) / len(confidences) if confidences else 0
        except Exception:
            avg_conf = 0
        
        return {
            'text': text,
            'confidence': avg_conf,
        }
    
    def extract_from_pdf(self, pdf_path: str) -> Dict:
        """Extract all text from a PDF."""
        print(f"    Converting PDF to images (DPI={self.dpi})...")
        
        # For large PDFs, process in batches or limit pages
        images = self.pdf_to_images(pdf_path)
        total_pages = len(images)
        print(f"    Found {total_pages} pages")
        
        # If max_pages is set, only process that many
        if self.max_pages > 0 and total_pages > self.max_pages:
            print(f"    ⚠️ Limiting OCR to first {self.max_pages} pages (of {total_pages})")
            images = images[:self.max_pages]
        
        all_text = []
        total_conf = 0
        
        for i, img in enumerate(images, 1):
            print(f"    OCR page {i}/{len(images)}...", end=' ', flush=True)
            result = self.ocr_image(img)
            print(f"({len(result['text'])} chars, conf={result['confidence']:.0f}%)")
            
            all_text.append(f"--- PAGE {i} ---\n{result['text']}")
            total_conf += result['confidence']
        
        avg_confidence = total_conf / len(images) if images else 0
        full_text = '\n\n'.join(all_text)
        
        return {
            'text': full_text,
            'pages': total_pages,
            'pages_processed': len(images),
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
        r'(?:Rs\.?|INR|₹)\s*([\d,]+\.?\d*)',
        r'([\d,]+\.?\d*)\s*(?:Rs\.?|INR|₹)',
        r'(?:Total|Amount|Net|Grand|Balance|Due|Payable)\s*[:\s]*(?:Rs\.?|INR|₹)?\s*([\d,]+\.?\d*)',
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
             r'Bill\s*(?:No|Number|#)\s*[.:]*\s*(\S+)',
             r'Invoice\s*(?:No|Number|#)\s*[.:]*\s*(\S+)'])
        bill.bill_date = self._extract_field(text,
            [r'Bill\s*Date\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
             r'Invoice\s*Date\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})',
             r'Date\s+of\s+Bill\s*[.:]*\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})'])
        
        # CIL/SECL specific: Extract Employee ID, Grade, CPRMSE card no
        bill.patient.employee_id = self._extract_field(text, [
            r'(?:Employee|Emp\s*No|EIS/NEIS)[\s.:]*(?:of\s+Employee)?[\s.:]*(\d{8,})',
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
            r'[Tt]otal\s*\|\s*([\d,]+\.\d{2})',
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
            r'(?:PO|Grand)\s*Total\s*\|\s*([\d,]+\.?\d*)',
            r'(?:PO|Grand)\s*Total\s*[.:]*\s*(?:Rs\.?)?\s*([\d,]+\.?\d*)',
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
        
        p.name = self._extract_field(text, [
            r'Patient\s*(?:Name|\'s?\s*Name)\s*[.:]*\s*([A-Z][A-Za-z\s.]+?)(?:\s{2,}|\n)',
            r'Name\s+of\s+(?:Patient|the\s+Patient)\s*[.:]*\s*([A-Z][A-Za-z\s.]+?)(?:\s{2,}|\n)',
            r'Mr\.\s*([A-Z][A-Za-z\s.]+?)(?:\s{2,}|\n)',
            r'Mrs\.\s*([A-Z][A-Za-z\s.]+?)(?:\s{2,}|\n)',
            r'Ms\.\s*([A-Z][A-Za-z\s.]+?)(?:\s{2,}|\n)',
        ])
        
        p.age = self._extract_field(text, [
            r'Age\s*[.:]*\s*(\d{1,3}\s*(?:Y(?:ears?|rs?)?|M(?:onths?)?|D(?:ays?)?))',
            r'(\d{1,3})\s*(?:years?|yrs?)\s*(?:old)?',
        ])
        
        p.gender = self._extract_field(text, [
            r'(?:Sex|Gender)\s*[.:]*\s*(Male|Female|M|F)',
        ])
        
        p.uhid = self._extract_field(text, [
            r'(?:UHID|MRN|MR\s*No|Patient\s*ID|Reg\s*No)\s*[.:]*\s*(\S+)',
        ])
        
        p.ip_number = self._extract_field(text, [
            r'(?:IP\s*No|IPD\s*No|Admission\s*No|Indoor\s*No)\s*[.:]*\s*(\S+)',
        ])
        
        return p
    
    def _extract_hospital(self, text: str) -> HospitalInfo:
        """Extract hospital information."""
        h = HospitalInfo()
        
        # Hospital name is usually in the first few lines
        lines = text.split('\n')
        for line in lines[:15]:
            line = line.strip()
            if not line:
                continue
            # Hospital name heuristics
            if re.search(r'(Hospital|Medical|Institute|Centre|Center|Clinic|Healthcare|Nursing)', line, re.IGNORECASE):
                if len(line) > 5 and not re.search(r'(Patient|Date|Bill|Discharge|Admission)', line, re.IGNORECASE):
                    h.name = line.strip()
                    break
        
        h.phone = self._extract_field(text, [
            r'(?:Ph|Tel|Phone|Contact)\s*[.:]*\s*([\d\s/\-+,()]+)',
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
        page_list = []
        i = 1
        while i < len(pages) - 1:
            page_num = int(pages[i])
            page_text = pages[i + 1].strip()
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
    
    def _post_process(self, bill: ExtractedBill) -> ExtractedBill:
        """Clean and validate extracted data."""
        # Clean patient name
        if bill.patient.name:
            bill.patient.name = bill.patient.name.strip().title()
        
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
    parser.add_argument('--dpi', type=int, default=300, help='OCR DPI (default: 300)')
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
