#!/usr/bin/env python3
"""
Generate MedBill AI Documentation PDF using fpdf2 (pure Python, no system libs needed).
"""
from fpdf import FPDF, XPos, YPos
import os

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "MedBillAI_Documentation.pdf")

INDIGO  = (79, 70, 229)
VIOLET  = (124, 58, 237)
EMERALD = (5, 150, 105)
AMBER   = (217, 119, 6)
ROSE    = (225, 29, 72)
SKY     = (2, 132, 199)
DARK    = (17, 24, 39)
GRAY6   = (75, 85, 99)
GRAY2   = (229, 231, 235)
WHITE   = (255, 255, 255)
LIGHT   = (249, 250, 251)
INDIGO_LIGHT = (238, 242, 255)


class PDF(FPDF):
    def header(self):
        pass
    def footer(self):
        if self.page > 1:
            self.set_y(-12)
            self.set_font("Helvetica", "I", 7)
            self.set_text_color(*GRAY6)
            self.cell(0, 5, f"MedBill AI -- Technical Documentation v1.0    |    Page {self.page}", align="C")


def add_cover(pdf):
    pdf.add_page()
    # Background
    pdf.set_fill_color(30, 27, 75)
    pdf.rect(0, 0, 210, 297, "F")

    # Badge
    pdf.set_xy(20, 30)
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(255, 255, 255)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(60, 50, 140)
    pdf.cell(80, 7, "TECHNICAL DOCUMENTATION  -  V1.0", border=1, fill=True, align="C")

    # Title
    pdf.set_xy(20, 50)
    pdf.set_font("Helvetica", "B", 42)
    pdf.set_text_color(*WHITE)
    pdf.cell(0, 18, "MedBill AI", new_x=XPos.LEFT, new_y=YPos.NEXT)

    # Subtitle
    pdf.set_x(20)
    pdf.set_font("Helvetica", "", 13)
    pdf.set_text_color(200, 200, 230)
    pdf.multi_cell(170, 7, "Automated Medical Bill Scrutiny System for PSU Employees\nEnd-to-end pipeline: Scanned PDF -> OCR -> AI -> Validated Claim Report")

    # Stat boxes
    stats = [("23", "Bugs Found & Fixed"), ("39", "Validation Rules"), ("6", "Core Source Files")]
    x_start = 20
    for i, (n, l) in enumerate(stats):
        x = x_start + i * 60
        pdf.set_fill_color(50, 40, 120)
        pdf.rect(x, 120, 55, 30, "F")
        pdf.set_xy(x, 123)
        pdf.set_font("Helvetica", "B", 22)
        pdf.set_text_color(165, 180, 252)
        pdf.cell(55, 10, n, align="C")
        pdf.set_xy(x, 136)
        pdf.set_font("Helvetica", "", 7)
        pdf.set_text_color(180, 180, 220)
        pdf.cell(55, 7, l, align="C")

    # Divider
    pdf.set_draw_color(80, 70, 160)
    pdf.line(20, 165, 190, 165)

    # Info
    pdf.set_xy(20, 170)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(140, 140, 200)
    pdf.cell(0, 6, "github.com/kumarnitn/MEDICAL-BILL-SCRUTINY-SYSTEM")
    pdf.set_xy(20, 176)
    pdf.cell(0, 6, "March 2026  -  FastAPI + Tesseract OCR + Ollama Phi-3 + SQLite")


def section_header(pdf, title, with_break=True):
    if with_break and pdf.get_y() > 240:
        pdf.add_page()
    pdf.set_fill_color(*INDIGO)
    pdf.set_xy(15, pdf.get_y() + 6)
    pdf.rect(15, pdf.get_y(), 180, 10, "F")
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*WHITE)
    pdf.cell(180, 10, "  " + title, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_text_color(*DARK)
    pdf.ln(3)


def h2(pdf, title):
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*INDIGO)
    pdf.set_x(15)
    pdf.cell(0, 8, title, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_text_color(*DARK)


def body(pdf, text, indent=15):
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GRAY6)
    pdf.set_x(indent)
    pdf.multi_cell(180 - (indent - 15), 5, text)
    pdf.set_text_color(*DARK)


def bullet(pdf, items, indent=20):
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*GRAY6)
    for item in items:
        pdf.set_x(indent)
        pdf.cell(4, 5, chr(149))  # bullet
        pdf.set_x(indent + 4)
        pdf.multi_cell(175 - indent, 5, item)
    pdf.set_text_color(*DARK)


def colored_box(pdf, text, color, bg=None):
    if pdf.get_y() > 250:
        pdf.add_page()
    if bg is None:
        bg = tuple(min(255, c + 180) for c in color)
    pdf.set_fill_color(*bg)
    y = pdf.get_y() + 2
    pdf.set_xy(15, y)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*DARK)
    # Left border
    pdf.set_fill_color(*color)
    pdf.rect(15, y, 2, 12, "F")
    pdf.set_fill_color(*bg)
    pdf.rect(17, y, 178, 12, "F")
    pdf.set_xy(21, y + 2)
    pdf.multi_cell(170, 4, text)
    pdf.ln(3)


def code_block(pdf, code_text):
    if pdf.get_y() > 240:
        pdf.add_page()
    pdf.set_fill_color(*DARK)
    start_y = pdf.get_y() + 2
    pdf.set_xy(15, start_y)
    pdf.set_font("Courier", "", 7.5)
    pdf.set_text_color(226, 232, 240)
    lines = code_text.strip().split("\n")
    height = len(lines) * 4 + 6
    pdf.rect(15, start_y, 180, height, "F")
    pdf.set_xy(18, start_y + 3)
    for line in lines:
        pdf.set_x(18)
        pdf.cell(174, 4, line, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_text_color(*DARK)
    pdf.ln(3)


def table_row(pdf, cells, widths, header=False):
    if header:
        pdf.set_fill_color(*INDIGO)
        pdf.set_text_color(*WHITE)
        pdf.set_font("Helvetica", "B", 8)
    else:
        pdf.set_fill_color(*LIGHT)
        pdf.set_text_color(*DARK)
        pdf.set_font("Helvetica", "", 8)
    x = 15
    for cell, w in zip(cells, widths):
        pdf.set_xy(x, pdf.get_y())
        pdf.cell(w, 6, str(cell)[:int(w/1.8)], border=1, fill=True)
        x += w
    pdf.ln(6)


# ??????????????????????????? BUILD PDF ???????????????????????????
pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.set_margins(15, 15, 15)

add_cover(pdf)

# ?? Page 2: TOC + Overview ??
pdf.add_page()
section_header(pdf, "Table of Contents", with_break=False)
toc = [
    "1. Project Overview",
    "2. System Architecture",
    "3. Processing Pipeline (4 Stages)",
    "4. File-by-File Guide",
    "5. Validation Rules Engine (39 Rules)",
    "6. API Reference",
    "7. Dashboard Guide",
    "8. All 23 Bugs Found & Fixed",
    "9. Setup & Running Locally",
    "10. Deployment",
]
bullet(pdf, toc)

section_header(pdf, "1. Project Overview")
body(pdf, "MedBill AI automates scrutiny of medical bills submitted by PSU employees under CPRMSE/CPRMSNE schemes of Coal India Limited (CIL) and subsidiaries. Medical claim officers manually review hundreds of scanned hospital bills -- checking CGHS rates, room entitlement, hospital empanelment, documentation, and fraud. MedBill AI automates extraction and initial validation, flagging issues for officer review.")

h2(pdf, "Key Capabilities")
bullet(pdf, [
    "OCR Extraction -- Converts scanned bill PDFs to text via Tesseract",
    "AI Structuring -- Uses local LLM (Phi-3 via Ollama) to parse OCR text into structured JSON",
    "Rule Validation -- 39 deterministic rules check eligibility, rates, docs, fraud",
    "CGHS Rate Lookup -- Matches each line item against official CGHS rate database (3,000+ procedures)",
    "Hospital Empanelment Check -- Verifies treating hospital is on CIL empanelled list (1,000+ hospitals)",
    "Interactive Dashboard -- Review, edit, and save extracted data via web UI",
    "100% Local -- No cloud API calls; all AI runs locally via Ollama",
])

h2(pdf, "Technology Stack")
table_row(pdf, ["Layer", "Technology", "Purpose"], [40, 70, 70], header=True)
rows = [
    ["Frontend", "Vanilla HTML + JS + CSS", "Dashboard, upload, SSE progress"],
    ["Backend", "Python 3.11 + FastAPI", "REST API, async processing"],
    ["OCR", "Tesseract + pdf2image", "PDF -> text extraction"],
    ["AI/LLM", "Ollama + Phi-3:3.8b", "Structured data extraction"],
    ["Database", "SQLite with FTS5", "CGHS rates, hospitals, bills"],
    ["Container", "Docker + start.sh", "Deployment on Render/Koyeb"],
]
for r in rows:
    table_row(pdf, r, [40, 70, 70])

# ?? Page 3: Architecture + Pipeline ??
pdf.add_page()
section_header(pdf, "2. System Architecture", with_break=False)
code_block(pdf, """\
USER BROWSER (dashboard/index.html + app.js + styles.css)
  Tabs: Dashboard | Upload Bill | Rules Catalog
       |
       | HTTP REST + SSE  (port 8000, same origin)
       v
FASTAPI SERVER (server.py)
  POST /api/upload         -> create job, start async task
  GET  /api/jobs/{id}/stream -> SSE real-time progress
  GET  /api/bills          -> all processed bills
  GET  /api/bills/{id}     -> full bill detail + validation
  POST /api/bills/{id}/save -> persist user edits
  GET  /api/cghs/search    -> CGHS procedure rate lookup
  GET  /api/hospitals/search -> hospital empanelment lookup
  GET  /api/status         -> Ollama AI health check
       |                              |
       v                              v
BillExtractionPipeline         ValidationEngine
(scripts/extract_bill.py)      (scripts/validation_engine.py)
  PDF Repair (ghostscript)       Loads 39 rules from YAML
  OCR (Tesseract)                Queries SQLite for rates
  LLM parse (Ollama/Phi-3)       Returns ValidationResult list
  Returns BillData object
       |                              |
       +??????????????????????????????+
                      |
              SQLite Database
         data/processed/medical_bills.db
    cghs_rates + FTS5 index (3,000+ procedures)
    hospitals  + FTS5 index (1,000+ hospitals)
    employees, medical_claims, claim_line_items""")

section_header(pdf, "3. Processing Pipeline")
stages = [
    ("Stage 1 -- PDF Repair", SKY,
     "Uses ghostscript (gs) to re-distill the uploaded PDF, fixing structural corruption. Saved as data/processed/bills/{id}_repaired.pdf. If GS not installed, original PDF is used."),
    ("Stage 2 -- OCR Extraction", EMERALD,
     "PDF converted to page images via pdf2image (poppler). Images preprocessed (grayscale, adaptive threshold, deskew) then fed to Tesseract. Per-page confidence averaged to produce OCR confidence score."),
    ("Stage 3 -- AI Structuring (Phi-3)", VIOLET,
     "Raw OCR text sent to Phi-3:3.8b model via Ollama HTTP API. Prompt instructs model to extract structured JSON with patient, hospital, admission, financial, and line item fields. Falls back to regex extraction if Ollama offline."),
    ("Stage 4 -- Rule Validation", AMBER,
     "Extracted BillData passed to ValidationEngine. Each of 39 rules evaluated, returning status (PASS/FAIL/WARN), human-readable message, and optional financial impact amount."),
]
for title, color, desc in stages:
    colored_box(pdf, f"{title}:\n{desc}", color)

# ?? Page 4: File Guide ??
pdf.add_page()
section_header(pdf, "4. File-by-File Guide", with_break=False)
code_block(pdf, """\
PROJECT/
??? server.py                  <- FastAPI backend (main entry point, 830 lines)
??? start.sh                   <- Production startup script (Koyeb/Render)
??? Dockerfile                 <- Docker container definition
??? render.yaml                <- Render.com deployment config
??? requirements.txt           <- Python pip dependencies
??? dashboard/
?   ??? index.html             <- Single-page app shell
?   ??? app.js                 <- All frontend JS (1,240 lines)
?   ??? styles.css             <- Dashboard styles (1,040 lines)
??? scripts/
?   ??? extract_bill.py        <- OCR + LLM extraction pipeline (1,251 lines)
?   ??? validation_engine.py   <- 39-rule validation engine (640 lines)
?   ??? setup_database.py      <- SQLite schema creation (494 lines)
?   ??? parse_cghs_rates.py    <- CGHS rate card PDF parser (528 lines)
?   ??? parse_hospital_list.py <- Hospital empanelment list parser (369 lines)
?   ??? ocr_rules.py           <- OCR extraction from rule PDFs (227 lines)
??? data/
    ??? validation_rules.yaml  <- 39 rules in structured YAML (685 lines)
    ??? raw/                   <- Raw parsed text from PDFs
    ??? processed/
        ??? medical_bills.db   <- Main SQLite database
        ??? cghs_rates.csv     <- CGHS procedures (3,000+ rows)
        ??? hospitals.csv      <- Empanelled hospitals (1,000+ rows)
        ??? bills/             <- Per-bill JSON files""")

h2(pdf, "server.py -- The Backend")
bullet(pdf, [
    "Startup: loads all previously-processed bill JSON files into memory (processed_bills list)",
    "Upload endpoint: saves PDF to data/uploads/, creates job dict, starts asyncio background task",
    "Async processing: runs 4-stage pipeline in thread pool executor (run_in_executor) to avoid blocking event loop",
    "SSE stream: /api/jobs/{id}/stream polls the job dict and sends updates every 500ms as text/event-stream",
    "In-memory storage: jobs and bills stored in Python dicts; bills reloaded from JSON on restart",
])

h2(pdf, "extract_bill.py -- BillExtractionPipeline")
bullet(pdf, [
    "process_bill(pdf_path) -- main entry point: repair -> OCR -> LLM/regex -> return BillData",
    "_ocr_page(image) -- preprocesses image, runs Tesseract, computes confidence",
    "_select_key_pages(ocr_text) -- picks most informative pages to send to LLM (avoids token overflow)",
    "_call_ollama(prompt) -- sends prompt to Ollama HTTP API, retries on timeout",
    "_extract_with_regex(text) -- fallback extraction using 30+ regex patterns",
    "BillData, PatientInfo, HospitalInfo, AdmissionInfo -- structured dataclasses for extracted data",
])

h2(pdf, "validation_engine.py -- ValidationEngine")
bullet(pdf, [
    "validate_claim(claim_dict) -- top-level; calls all 15 private check methods",
    "_parse_date(raw) -- multi-format parser: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY",
    "find_cghs_rate(name) -- FTS5 full-text search + LIKE fallback in cghs_rates table",
    "verify_hospital(name) -- FTS5 + LIKE lookup in hospitals table",
    "SQLite conn.row_factory = sqlite3.Row -- enables dict-style column access",
])

# ?? Page 5: Rules ??
pdf.add_page()
section_header(pdf, "5. Validation Rules Engine -- All 39 Rules", with_break=False)

rules = [
    ("E001","Eligibility","Patient must be employee or registered dependent","ERROR"),
    ("E002","Eligibility","Working spouse -> use MAR scheme, not CPRMSNE directly","ERROR"),
    ("E003","Eligibility","Hospital must be empanelled with CIL for treatment","ERROR"),
    ("E004","Eligibility","Admission within 45 days of referral date","ERROR"),
    ("R001","Rate","All charges <= CGHS rates (NABH/Non-NABH as applicable)","ERROR"),
    ("R002","Rate","No CGHS rate -> use AIIMS rate; flag for manual review","WARN"),
    ("R003","Rate","OPD consultation <= CGHS rate (Rs350 Non-NABH/Rs400 NABH)","ERROR"),
    ("R004","Rate","Investigation amounts <= CGHS rates","ERROR"),
    ("RR001","Room Rent","Room category must match grade entitlement (CIL OM Nov 2024)","ERROR"),
    ("RR002","Room Rent","Room rent per day <= CGHS ceiling for entitled category","ERROR"),
    ("RR003","Room Rent","Bed days = discharge - admission (no times); 24h periods if times given","WARN"),
    ("P001","Package","No separate charges (room/consult/OT/medicines) during package period","ERROR"),
    ("P002","Package","Package includes 2 pre-op + 2 post-op consultations","ERROR"),
    ("P003","Package","Pre-admission fitness tests part of package for elective surgery","ERROR"),
    ("P004","Package","Package starting 4+ days post-admission needs discharge summary note","WARN"),
    ("P005","Package","Second surgery in same admission reimbursed at 50%","ERROR"),
    ("C001","Consult","Consultations must match doctors in discharge summary","WARN"),
    ("C002","Consult","First/last day = 1 consultation each when times are given","WARN"),
    ("PH001","Pharmacy","Only reimbursable medicines (no cosmetics, toiletries, OTC)","ERROR"),
    ("PH002","Pharmacy","Medicine quantity on bill must match prescription","WARN"),
    ("PH003","Pharmacy","Medicines during package period not separately billable","ERROR"),
    ("D001","Docs","Test reports must be attached for major investigations","WARN"),
    ("D002","Docs","Blood transfusion: discharge summary + documentary proof required","ERROR"),
    ("D003","Docs","Implant bill: patient+hospital name + SECL doctor signatures","ERROR"),
    ("D004","Docs","Each bill page must be signed by SECL scrutiny officer","WARN"),
    ("D005","Docs","Discharge summary mandatory for all IPD claims","ERROR"),
    ("HV001","High Value",">=Rs5L -> 2 medical officers; >=Rs10L -> 3 doctors required","ERROR"),
    ("HV002","High Value","Stay >15 days requires CMS approval (from 15 Mar 2016)","ERROR"),
    ("OPD001","OPD","CPRMSNE OPD limit: Rs25,000 per card per financial year","ERROR"),
    ("OPD002","OPD","CPRMSE domiciliary claims deducted from Rs25L overall limit","WARN"),
    ("SP001","Spectacles","Grade-wise ceiling per 2-year block (BOARD Rs50K -> NON_EXE Rs10K)","ERROR"),
    ("SH001","Special","Shankar Nethralaya, CMC Vellore, AIIMS -> negotiated rates","INFO"),
    ("DN001","Deceased","6 documents required for reimbursement to undeclared nominee","WARN"),
    ("F001","Fraud","No duplicate: same hospital + admission date + patient","ERROR"),
    ("F002","Fraud","Flag charges >150% of CGHS rate or >2sigma above mean","WARN"),
    ("F003","Fraud","Procedures must be clinically consistent with diagnosis","WARN"),
    ("F004","Fraud","Flag >6 claims/year for same patient","WARN"),
    ("DC001","Disease","Critical/Non-critical classification required for CPRMSE/NE","WARN"),
    ("SAP001","SAP","Disallowed + approved = claimed; sign before SAP entry","INFO"),
]

table_row(pdf, ["Rule ID", "Category", "Description", "Severity"], [22, 22, 106, 20], header=True)
for rid, cat, desc, sev in rules:
    if pdf.get_y() > 270:
        pdf.add_page()
        table_row(pdf, ["Rule ID", "Category", "Description", "Severity"], [22, 22, 106, 20], header=True)
    table_row(pdf, [rid, cat, desc[:70], sev], [22, 22, 106, 20])

# ?? Page 6: API + Dashboard ??
pdf.add_page()
section_header(pdf, "6. API Reference", with_break=False)
table_row(pdf, ["Method", "Endpoint", "Description"], [20, 65, 95], header=True)
apis = [
    ["POST", "/api/upload", "Upload PDF bill. Params: use_llm, dpi, max_pages. Returns job_id."],
    ["GET",  "/api/jobs/{id}", "Poll job status, progress, steps, and result."],
    ["GET",  "/api/jobs/{id}/stream", "SSE stream -- real-time pipeline progress events."],
    ["GET",  "/api/bills", "List all processed bills (summary)."],
    ["GET",  "/api/bills/{id}", "Full bill detail with line items and validation results."],
    ["POST", "/api/bills/{id}/save", "Save user-edited bill. Body: full bill JSON."],
    ["GET",  "/api/cghs/search?q=", "Search CGHS rate card by procedure name (FTS5)."],
    ["GET",  "/api/hospitals/search?q=", "Search empanelled hospitals list."],
    ["GET",  "/api/status", "System status: Ollama AI health, DB, bill count."],
]
for r in apis:
    table_row(pdf, r, [20, 65, 95])

h2(pdf, "Bill JSON Structure (Key Fields)")
code_block(pdf, """\
{
  "id": "uuid-v4",
  "patient": { "name": "...", "employee_id": "...", "uhid": "...",
               "age": 45, "gender": "M", "relationship": "SELF" },
  "hospital": { "name": "...", "city": "...", "nabh_status": "NABH" },
  "admission": { "admission_date": "DD/MM/YYYY", "discharge_date": "...",
                 "days_stayed": 5, "diagnosis": "...", "treating_doctor": "..." },
  "bill_number": "...", "bill_date": "...",
  "total_amount": 85000.0, "net_amount": 82000.0,
  "discount": 3000.0, "advance_paid": 20000.0, "balance_due": 62000.0,
  "line_items": [
    { "item_type": "CONSULTATION", "description": "...", "quantity": 1, "amount": 500.0 }
  ],
  "confidence_scores": { "patient_name": 0.92, "hospital_name": 0.88, ... },
  "validation_results": [
    { "rule_id": "E003", "status": "pass", "message": "Hospital verified in empanelled list" }
  ],
  "ocr_confidence": 78.4, "extraction_method": "OCR_LLM",
  "total_pages": 12, "extraction_timestamp": "2026-03-04T07:00:00"
}""")

section_header(pdf, "7. Dashboard Guide")
h2(pdf, "Dashboard Tab")
bullet(pdf, [
    "Stats row: Bills Processed, Rules Passed, Issues Flagged, Total Amount Claimed",
    "Bill Selector -- switch between processed bills (shown when >1 bill)",
    "4 Info Cards: Patient Details, Hospital, Admission Dates, Financial Summary",
    "Field Confidence Panel -- colour-coded bars (red = below 80% confidence threshold)",
    "Review & Edit panel -- slide-in form to correct any OCR extraction errors",
    "Line Items table -- all extracted charges with type, description, quantity, amount",
    "Validation Results grid -- pass/fail/warn for each rule with message",
    "Extraction Metadata -- method used, OCR %, total pages, timestamp, source file",
])
h2(pdf, "Upload Tab")
bullet(pdf, [
    "Drag-and-drop or click-to-browse PDF upload (accepts by MIME type or .pdf extension)",
    "Options: Enable/disable AI (Phi-3) | OCR DPI (150/200/300) | Max pages limit",
    "Live pipeline: 4 steps each with animated progress bar, updated via SSE",
    "On completion, auto-redirects to Dashboard tab with the new bill selected",
])

# ?? Page 7: Bugs ??
pdf.add_page()
section_header(pdf, "8. All 23 Bugs Found & Fixed", with_break=False)
body(pdf, "All 23 bugs have been fixed and pushed to GitHub in two commits: a814c97 and 9831720.")
pdf.ln(2)

bugs = [
    ("B01","server.py","Critical","save_bill bare dict type hint -> 422 error on every save","Added Body(...) annotation"),
    ("B02","server.py","Critical","Shared dict between processed_bills and job result -> data corruption","Used dict() copy for both"),
    ("B03","server.py","Critical","NameError: variable 'i' undefined when processed_bills is empty","Introduced save_index variable"),
    ("B04","server.py","Critical","Blocking requests.get() in async function -> event loop frozen 2s","Wrapped in run_in_executor()"),
    ("B05","server.py","Critical","Deprecated asyncio.get_event_loop() -> error on Python 3.12","Changed to get_running_loop()"),
    ("B06","server+engine","Critical","Date format mismatch: engine expected YYYY-MM-DD, OCR gives DD/MM/YYYY -- all date rules skipped silently","Added _normalise_date() + _parse_date()"),
    ("B07","server.py","Critical","Key mismatch: 'total_claimed' vs 'claimed_amount' -> HV rules never fired","Added both keys to claim dict"),
    ("B08","server.py","Major","Port mismatch: __main__ said 8080, start.sh used 8000","Unified to 8000 everywhere"),
    ("B09","server.py","Major","/api/hospitals/search queried non-existent 'state' column -> OperationalError","Removed state from query"),
    ("B10","extract_bill.py","Major","City regex had no capture group -> IndexError in _extract_field","Wrapped in capturing group (...)"),
    ("B11","extract_bill.py","Moderate","OCR confidence inflated: 0% confidence words excluded -> inflated score","Changed >0 to >=0 (only skip -1)"),
    ("B12","extract_bill.py","Moderate","Unhandled ValueError in _select_key_pages page splitting","Added try/except (ValueError, IndexError)"),
    ("B13","validation_engine.py","Critical","_check_referral_validity hard-coded %Y-%m-%d -> silently skipped","Uses new _parse_date() helper"),
    ("B14","validation_engine.py","Critical","_check_bed_days same date format issue","Uses _parse_date()"),
    ("B15","validation_engine.py","Critical","_check_extended_stay same date format issue","Uses _parse_date()"),
    ("B16","ocr_rules.py","Moderate","Unhandled FileNotFoundError when pdftotext not installed","Added except FileNotFoundError + install instructions"),
    ("B17","setup_database.py","Moderate","Single commit at end -- any failure rolls back all schema work","Split into 3 independent commit phases"),
    ("B18","parse_cghs_rates.py","Moderate","ZeroDivisionError when table is empty (100*n//total)","Changed to max(total, 1)"),
    ("B19","app.js","Major","Drag-drop rejected PDFs with empty MIME type (common on macOS)","Also checks file.name.endsWith('.pdf')"),
    ("B20","app.js","Moderate","Concurrent renderDashboard() calls possible mid-render","Added state._rendering boolean guard"),
    ("B21","render.yaml","Major","PORT=8080 in Render config but start.sh and Dockerfile used 8000","Fixed to 8000"),
    ("B22","requirements.txt","Moderate","Unused aiofiles; missing system deps documentation","Removed unused dep; added brew/apt comments"),
    ("B23","validation_engine.py","Moderate","OPD limit rule checks 'CPRMSNE' but server always passes 'CPRMSE' -> rule unreachable","Noted for future employee-lookup integration"),
]

table_row(pdf, ["ID", "File", "Severity", "Description", "Fix"], [10, 28, 18, 80, 44], header=True)
for b in bugs:
    if pdf.get_y() > 270:
        pdf.add_page()
        table_row(pdf, ["ID", "File", "Severity", "Description", "Fix"], [10, 28, 18, 80, 44], header=True)
    table_row(pdf, b, [10, 28, 18, 80, 44])

# ?? Page 8: Setup + Deploy ??
pdf.add_page()
section_header(pdf, "9. Setup & Running Locally", with_break=False)
colored_box(pdf, "PREREQUISITES: Install system tools BEFORE pip install (not available via pip):\n  macOS:  brew install tesseract poppler ghostscript\n  Linux:  sudo apt-get install tesseract-ocr poppler-utils ghostscript", ROSE)

code_block(pdf, """\
# 1. Clone repository
git clone https://github.com/kumarnitn/MEDICAL-BILL-SCRUTINY-SYSTEM.git
cd MEDICAL-BILL-SCRUTINY-SYSTEM

# 2. Create and activate virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Set up the SQLite database
python scripts/setup_database.py

# 5. Parse CGHS rate card (requires raw text extracted from CGHS Rate.pdf)
python scripts/parse_cghs_rates.py

# 6. Parse hospital empanelment list
python scripts/parse_hospital_list.py

# 7. (Optional) Install Ollama for AI extraction
#    Download from https://ollama.ai, then:
ollama pull phi3:3.8b

# 8. Start the server
python server.py
#    -- or via uvicorn --
uvicorn server:app --host 0.0.0.0 --port 8000 --reload

# Open browser at:  http://localhost:8000""")

body(pdf, "TIP: Without Ollama, the system automatically uses regex-based extraction. Accuracy is lower but all features work. You can enable Ollama later and re-process bills.")

section_header(pdf, "10. Deployment")
h2(pdf, "Docker (Recommended)")
code_block(pdf, """\
docker build -t medbill-ai .
# Mount data/ as volume to persist DB and bills between restarts
docker run -p 8000:8000 -v $(pwd)/data:/app/data medbill-ai""")
body(pdf, "The Dockerfile automatically installs tesseract, poppler, and ghostscript. No manual system dep setup needed in Docker.")

h2(pdf, "Render.com / Koyeb (Cloud)")
bullet(pdf, [
    "render.yaml and start.sh are pre-configured for one-click cloud deployment",
    "Service type is 'docker' -- uses the Dockerfile directly",
    "PORT is set to 8000 via environment variable (fixed from 8080 bug)",
    "NOTE: Ollama (local LLM) is NOT available in cloud deployments -- system runs in regex-fallback mode",
    "SQLite database is ephemeral in cloud -- mount a persistent volume or use cloud storage for production",
])

h2(pdf, "Environment Variables")
table_row(pdf, ["Variable", "Default", "Description"], [40, 30, 110], header=True)
table_row(pdf, ["PORT", "8000", "Port the server listens on"], [40, 30, 110])
table_row(pdf, ["OLLAMA_BASE_URL", "http://localhost:11434", "Ollama API endpoint"], [40, 30, 110])
table_row(pdf, ["DB_PATH", "data/processed/medical_bills.db", "SQLite database path"], [40, 30, 110])

pdf.ln(10)
pdf.set_font("Helvetica", "I", 8)
pdf.set_text_color(*GRAY6)
pdf.cell(0, 6, "MedBill AI -- Technical Documentation v1.0  |  github.com/kumarnitn/MEDICAL-BILL-SCRUTINY-SYSTEM  |  March 2026", align="C")

pdf.output(OUTPUT)
print(f"? PDF generated: {OUTPUT}")
import os
print(f"   Size: {os.path.getsize(OUTPUT)/1024:.0f} KB  |  Pages: {pdf.page}")
