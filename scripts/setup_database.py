#!/usr/bin/env python3
"""
Database Schema Setup
======================
Creates the complete database schema for the medical bill validation system.

Tables:
1. cghs_rates         - CGHS procedure rates (already populated by parse_cghs_rates.py)
2. hospitals          - Empanelled hospitals (already populated by parse_hospital_list.py)
3. employees          - Employee master data
4. dependents         - Employee dependents (eligible for medical benefits)
5. medical_claims     - Submitted bills tracking
6. claim_line_items   - Individual line items from bills
7. validation_results - Validation rule check results
8. referrals          - Cross-referral tracking
9. rule_documents     - Source rules metadata

Also creates necessary indexes and FTS tables for search.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed', 'medical_bills.db')


def setup_employee_schema(conn):
    """Create employee & dependent tables."""
    c = conn.cursor()
    
    # ---- EMPLOYEES ----
    c.execute('''CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        
        -- Identity
        employee_id TEXT UNIQUE NOT NULL,         -- SAP/ERP employee code
        name TEXT NOT NULL,
        date_of_birth DATE,
        gender TEXT CHECK(gender IN ('M', 'F', 'O')),
        
        -- Employment
        designation TEXT,                          -- Current designation
        grade TEXT,                                -- E1-E9, NON-EXE, BOARD
        department TEXT,
        area TEXT,                                 -- SECL area/region
        subsidiary TEXT DEFAULT 'SECL',            -- CIL subsidiary
        date_of_joining DATE,
        date_of_retirement DATE,
        employment_status TEXT DEFAULT 'ACTIVE'    -- ACTIVE, RETIRED, VRS, DECEASED
            CHECK(employment_status IN ('ACTIVE', 'RETIRED', 'VRS', 'DECEASED', 'RESIGNED')),
        
        -- Medical Scheme
        medical_scheme TEXT DEFAULT 'MAR'          -- MAR (active), CPRMSE (retired exec), CPRMSNE (retired non-exec)
            CHECK(medical_scheme IN ('MAR', 'CPRMSE', 'CPRMSNE')),
        medical_card_no TEXT,
        
        -- Entitlements derived from grade
        room_entitlement TEXT,                     -- Suite, Deluxe, Private, Twin Sharing
        opd_annual_limit REAL,                     -- Annual OPD/domiciliary limit (Rs)
        overall_medical_limit REAL,                -- Overall medical limit (Rs) - for CPRMSE/NE
        
        -- Spectacles
        spectacles_ceiling REAL,                   -- Grade-wise spectacles ceiling
        spectacles_block_start DATE,               -- Start of 2-year block
        spectacles_claimed REAL DEFAULT 0,         -- Amount already claimed in current block
        
        -- Contact
        phone TEXT,
        email TEXT,
        address TEXT,
        city TEXT,
        
        -- Metadata
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # ---- DEPENDENTS ----
    c.execute('''CREATE TABLE IF NOT EXISTS dependents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id TEXT NOT NULL REFERENCES employees(employee_id),
        
        name TEXT NOT NULL,
        relationship TEXT NOT NULL                  -- SPOUSE, SON, DAUGHTER, FATHER, MOTHER
            CHECK(relationship IN ('SPOUSE', 'SON', 'DAUGHTER', 'FATHER', 'MOTHER',
                                   'FATHER_IN_LAW', 'MOTHER_IN_LAW', 'OTHER')),
        date_of_birth DATE,
        gender TEXT CHECK(gender IN ('M', 'F', 'O')),
        age INTEGER,
        
        -- Eligibility
        is_eligible INTEGER DEFAULT 1,             -- 0 = not eligible (e.g., married daughter, employed son)
        eligibility_reason TEXT,                    -- Reason if not eligible
        is_working_spouse INTEGER DEFAULT 0,        -- If spouse also works in CIL/subsidiary
        spouse_employee_id TEXT,                    -- Spouse's employee ID if working
        
        -- Medical card
        medical_card_no TEXT,                       -- Same as employee's card or separate
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    print("  ✅ Employee & Dependent tables created")


def setup_claims_schema(conn):
    """Create medical claims tracking tables."""
    c = conn.cursor()
    
    # ---- MEDICAL CLAIMS ----
    c.execute('''CREATE TABLE IF NOT EXISTS medical_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        
        -- Claim Identity
        claim_id TEXT UNIQUE NOT NULL,              -- System-generated claim reference
        employee_id TEXT NOT NULL REFERENCES employees(employee_id),
        patient_name TEXT NOT NULL,                 -- Patient (employee or dependent)
        patient_relationship TEXT,                  -- SELF, SPOUSE, SON, etc.
        
        -- Hospital
        hospital_name TEXT,
        hospital_id INTEGER REFERENCES hospitals(id),  -- Link to empanelled hospital
        is_empanelled INTEGER,                     -- 1 = empanelled, 0 = non-empanelled
        hospital_city TEXT,
        
        -- Treatment
        treatment_type TEXT                         -- OPD, IPD, DAYCARE
            CHECK(treatment_type IN ('OPD', 'IPD', 'DAYCARE', 'DOMICILIARY')),
        admission_date DATE,
        discharge_date DATE,
        length_of_stay INTEGER,                    -- Days (calculated)
        diagnosis TEXT,
        is_critical INTEGER DEFAULT 0,             -- Critical disease flag (for CPRMSE/NE)
        referral_required INTEGER DEFAULT 0,       -- Was referral needed?
        referral_id TEXT,                           -- Link to referral
        
        -- Financial
        claimed_amount REAL NOT NULL,              -- Total amount claimed
        approved_amount REAL,                      -- Amount approved after scrutiny
        disallowed_amount REAL,                    -- Amount disallowed
        disallowance_reasons TEXT,                 -- JSON array of reasons
        
        -- Bill Details
        bill_date DATE,
        bill_pdf_path TEXT,                        -- Path to uploaded bill PDF
        total_pages INTEGER,
        
        -- Status
        status TEXT DEFAULT 'PENDING'
            CHECK(status IN ('PENDING', 'UNDER_REVIEW', 'APPROVED', 'PARTIALLY_APPROVED',
                            'REJECTED', 'RETURNED', 'ESCALATED')),
        
        -- Scrutiny
        scrutinized_by TEXT,                       -- Doctor who scrutinized
        scrutinized_by_2 TEXT,                     -- 2nd doctor (for >=5L bills)
        scrutinized_by_3 TEXT,                     -- 3rd doctor (for >=10L bills)
        cms_approval_required INTEGER DEFAULT 0,   -- If stay >15 days
        cms_approval_attached INTEGER DEFAULT 0,
        
        -- Metadata
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TIMESTAMP,
        completed_at TIMESTAMP,
        notes TEXT
    )''')
    
    # ---- CLAIM LINE ITEMS ----
    c.execute('''CREATE TABLE IF NOT EXISTS claim_line_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_id TEXT NOT NULL REFERENCES medical_claims(claim_id),
        
        -- Item Details
        item_type TEXT NOT NULL                     -- Category of charge
            CHECK(item_type IN ('CONSULTATION', 'ROOM_RENT', 'ICU', 'PROCEDURE', 'PACKAGE',
                               'INVESTIGATION', 'MEDICINE', 'CONSUMABLE', 'IMPLANT',
                               'BLOOD_TRANSFUSION', 'OT_CHARGES', 'DRESSING', 'NURSING',
                               'AMBULANCE', 'OTHER')),
        description TEXT NOT NULL,                  -- Item description from bill
        
        -- Amounts
        quantity INTEGER DEFAULT 1,
        unit_rate REAL,
        claimed_amount REAL NOT NULL,
        
        -- CGHS Validation
        cghs_rate_id INTEGER REFERENCES cghs_rates(id),  -- Matched CGHS rate
        cghs_procedure_name TEXT,                   -- Matched procedure name
        cghs_rate REAL,                             -- Applicable CGHS rate
        nabh_applicable INTEGER DEFAULT 0,          -- Is NABH rate applicable?
        
        -- Validation
        approved_amount REAL,
        disallowed_amount REAL,
        disallowance_reason TEXT,
        
        -- Package context
        is_part_of_package INTEGER DEFAULT 0,       -- If this item is covered by a package
        package_item_id INTEGER,                    -- Reference to the package line item
        
        -- Dates
        service_date DATE,
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    print("  ✅ Medical Claims & Line Items tables created")


def setup_validation_schema(conn):
    """Create validation results tracking tables."""
    c = conn.cursor()
    
    # ---- VALIDATION RESULTS ----
    c.execute('''CREATE TABLE IF NOT EXISTS validation_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_id TEXT NOT NULL REFERENCES medical_claims(claim_id),
        line_item_id INTEGER REFERENCES claim_line_items(id),  -- NULL for claim-level rules
        
        -- Rule
        rule_id TEXT NOT NULL,                      -- Reference to validation_rules YAML
        rule_category TEXT NOT NULL,                -- ELIGIBILITY, RATE, PACKAGE, DOCUMENT, etc.
        rule_description TEXT,
        
        -- Result
        status TEXT NOT NULL
            CHECK(status IN ('PASS', 'FAIL', 'WARNING', 'MANUAL_REVIEW', 'NOT_APPLICABLE')),
        severity TEXT DEFAULT 'ERROR'
            CHECK(severity IN ('ERROR', 'WARNING', 'INFO')),
        
        message TEXT,                               -- Human-readable validation message
        details TEXT,                               -- JSON with detailed info
        
        -- Financial Impact
        amount_impact REAL DEFAULT 0,               -- Amount affected by this rule
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # ---- REFERRALS ----
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        
        referral_id TEXT UNIQUE NOT NULL,
        employee_id TEXT NOT NULL REFERENCES employees(employee_id),
        patient_name TEXT NOT NULL,
        
        -- Referral Details
        referring_department TEXT,                  -- E.g., SECL Medical Dept
        referred_hospital TEXT,                     -- Hospital referred to
        referred_hospital_id INTEGER REFERENCES hospitals(id),
        referral_date DATE NOT NULL,
        
        -- Validity
        valid_until DATE,                          -- 45 days from referral date
        specialty TEXT,                            -- Referred specialty
        cross_referral INTEGER DEFAULT 0,          -- Cross-referral flag
        cross_referral_from TEXT,                   -- Original referral department
        
        -- Status
        status TEXT DEFAULT 'ACTIVE'
            CHECK(status IN ('ACTIVE', 'USED', 'EXPIRED', 'CANCELLED')),
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # ---- RULE DOCUMENTS ----
    c.execute('''CREATE TABLE IF NOT EXISTS rule_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        
        document_name TEXT NOT NULL,
        document_type TEXT,                        -- SOP, OM, CIRCULAR, CHECKLIST
        reference_number TEXT,
        issued_date DATE,
        effective_date DATE,
        issuing_authority TEXT,
        
        -- Content
        pdf_path TEXT,
        text_path TEXT,                            -- Extracted/OCR'd text path
        summary TEXT,
        
        -- Status
        is_active INTEGER DEFAULT 1,
        superseded_by INTEGER REFERENCES rule_documents(id),
        
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    print("  ✅ Validation Results, Referrals & Rule Documents tables created")


def create_indexes(conn):
    """Create performance indexes."""
    c = conn.cursor()
    
    indexes = [
        # Employee lookups
        'CREATE INDEX IF NOT EXISTS idx_employees_grade ON employees(grade)',
        'CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(employment_status)',
        'CREATE INDEX IF NOT EXISTS idx_employees_scheme ON employees(medical_scheme)',
        'CREATE INDEX IF NOT EXISTS idx_employees_subsidiary ON employees(subsidiary)',
        
        # Dependent lookups
        'CREATE INDEX IF NOT EXISTS idx_dependents_employee ON dependents(employee_id)',
        'CREATE INDEX IF NOT EXISTS idx_dependents_eligible ON dependents(is_eligible)',
        
        # Claim lookups
        'CREATE INDEX IF NOT EXISTS idx_claims_employee ON medical_claims(employee_id)',
        'CREATE INDEX IF NOT EXISTS idx_claims_status ON medical_claims(status)',
        'CREATE INDEX IF NOT EXISTS idx_claims_hospital ON medical_claims(hospital_id)',
        'CREATE INDEX IF NOT EXISTS idx_claims_dates ON medical_claims(admission_date, discharge_date)',
        'CREATE INDEX IF NOT EXISTS idx_claims_amount ON medical_claims(claimed_amount)',
        
        # Line item lookups
        'CREATE INDEX IF NOT EXISTS idx_line_items_claim ON claim_line_items(claim_id)',
        'CREATE INDEX IF NOT EXISTS idx_line_items_type ON claim_line_items(item_type)',
        'CREATE INDEX IF NOT EXISTS idx_line_items_cghs ON claim_line_items(cghs_rate_id)',
        
        # Validation lookups
        'CREATE INDEX IF NOT EXISTS idx_validation_claim ON validation_results(claim_id)',
        'CREATE INDEX IF NOT EXISTS idx_validation_rule ON validation_results(rule_id)',
        'CREATE INDEX IF NOT EXISTS idx_validation_status ON validation_results(status)',
        
        # Referral lookups
        'CREATE INDEX IF NOT EXISTS idx_referrals_employee ON referrals(employee_id)',
        'CREATE INDEX IF NOT EXISTS idx_referrals_validity ON referrals(valid_until)',
    ]
    
    for idx_sql in indexes:
        c.execute(idx_sql)
    
    print(f"  ✅ {len(indexes)} indexes created")


def populate_rule_documents(conn):
    """Populate rule_documents table with metadata about all rule files."""
    c = conn.cursor()
    
    rules = [
        {
            'document_name': 'SOP for Medical Bill Scrutiny',
            'document_type': 'SOP',
            'reference_number': 'SECL/BSP/MED/CMS/24',
            'issued_date': '2024-01-01',
            'issuing_authority': 'CMS, SECL Bilaspur',
            'pdf_path': 'Rules/SOP OF MEDICAL BILL SCRUTINY.pdf',
            'text_path': 'data/ocr_output/sop_of_medical_bill_scrutiny.txt',
            'summary': 'Standard Operating Procedure for medical bill scrutiny at SECL. Covers OPD/Indoor consultation fees, pharmacy bills, procedure reimbursement, investigation charges, room rent entitlements, and CGHS/AIIMS rate applicability.',
        },
        {
            'document_name': 'Cashless Hospital Bill Passing Checklist',
            'document_type': 'CHECKLIST',
            'reference_number': None,
            'issued_date': None,
            'issuing_authority': 'SECL Medical Department',
            'pdf_path': 'Rules/CASHLESS HOSPITAL BILL PASSING CHECKLIST.pdf',
            'text_path': 'data/ocr_output/cashless_hospital_bill_passing_checklist.txt',
            'summary': '16-point checklist for passing cashless medical bills. Covers patient verification, bed charge counting, consultation rules, blood transfusion, package period rules, implant billing, test report requirements, multi-doctor scrutiny for high-value bills.',
        },
        {
            'document_name': 'SOP for Medicine Distribution (CPRMSE/CPRMSNE)',
            'document_type': 'SOP',
            'reference_number': 'SECL/BSP/MED/CMS/25/242',
            'issued_date': '2025-02-25',
            'issuing_authority': 'CMS, SECL Bilaspur',
            'pdf_path': 'Rules/Approved SOP for medicine distribution.pdf',
            'text_path': 'data/ocr_output/approved_sop_for_medicine_distribution.txt',
            'summary': 'SOP for distribution of medicines to retired CPRMSE and CPRMSNE beneficiaries via Amrit Pharmacy. Details medicine procurement, indent process, NA certificate handling, and reimbursement for unavailable medicines.',
        },
        {
            'document_name': 'Change in Entitlement under MAR',
            'document_type': 'OM',
            'reference_number': 'CIL/C5A(PC)/MAR/2024/1287',
            'issued_date': '2024-11-18',
            'issuing_authority': 'GM (P/PC), CIL',
            'pdf_path': 'Rules/change in entitlement under MAR.pdf',
            'text_path': 'data/ocr_output/change_in_entitlement_under_mar.txt',
            'summary': 'Changes room entitlement categories under MAR and CPRMSE. Board level=Suite, E8-E9=Deluxe, E5-E7=Private (AC), Upto E4=Twin Sharing (AC). Amends Clause 5.2 of MAR and 3.2.1(b) of CPRMSE.',
        },
        {
            'document_name': 'Spectacles Reimbursement under MAR',
            'document_type': 'OM',
            'reference_number': 'CIL/C5A(PC)/Spectacles reimbursement/1165-A',
            'issued_date': '2024-03-06',
            'issuing_authority': 'CIL Policy Cell',
            'pdf_path': 'Rules/CIL-OM-Spectacles-reimbursement.pdf',
            'text_path': 'data/raw/rules_cil-om-spectacles-reimbursement.txt',
            'summary': 'Amendment to include spectacles reimbursement under MAR Clause 3.3(a). Grade-wise ceiling: Board=50K, E8-E9=45K, E6-E7=35K, E4-E5=30K, E1-E3=20K, Non-Exe=10K. 2-year block from 01.04.2024. Vision correction only.',
        },
        {
            'document_name': 'CPRMS-NE: Medical Benefit as Dependent of Working Spouse',
            'document_type': 'OM',
            'reference_number': 'CIL/C5B/JBCCI/CPRMS-NE/6',
            'issued_date': '2024-02-02',
            'issuing_authority': 'GM (MP&NKC), CIL',
            'pdf_path': 'Rules/20240202 161 OM - CPRMSNE - MED BENEFIT AS DEPENDENT OF WORKING SPOUSE.pdf',
            'text_path': 'data/raw/rules_20240202_161_om_-_cprmsne_-_med_benefit_as_dependent_of_working_spouse.txt',
            'summary': 'Clarification that a CPRMS-NE member whose spouse is still working in CIL shall avail medical benefits as dependent of working spouse under MAR. CPRMS-NE card to be withheld until working spouse separates. One-time option available after both separate.',
        },
        {
            'document_name': 'Clarification on OPD/Domiciliary Treatment Payment',
            'document_type': 'OM',
            'reference_number': 'CIL/C-5B/JBCCI/CPRMS-NE/81',
            'issued_date': '2023-08-11',
            'issuing_authority': 'GM (P/PC), CIL',
            'pdf_path': 'Rules/Clarification on clause 3.1.2 of  CPRMS-NE (Modified) regarding payment of OPD _ Domiciliary treatment.pdf',
            'text_path': 'data/raw/rules_clarification_on_clause_3.1.2_of_cprms-ne_modified_regarding_payment_of_opd__domiciliary_treatment.txt',
            'summary': 'Clarifies that "per beneficiary" in Clause 3.1.2(b) of CPRMS-NE means "per CPRMS-NE card". OPD/Domiciliary limit is Rs 25,000 per annum per card (not per person). Financial year basis.',
        },
        {
            'document_name': 'Reimbursement for Undeclared/Deceased/Unavailable Nominee',
            'document_type': 'OM',
            'reference_number': None,
            'issued_date': '2023-08-11',
            'issuing_authority': 'GM (P/PC), CIL',
            'pdf_path': 'Rules/Clarification w.r.t reimbursement of hospitalization charges to undeclared _ deceased  _ unavailable  nominee.pdf',
            'text_path': 'data/raw/rules_clarification_w.r.t_reimbursement_of_hospitalization_charges_to_undeclared__deceased__unavailable_nominee.txt',
            'summary': 'Last IPD/OPD bill of expired beneficiary can be reimbursed to undeclared/deceased/unavailable nominee upon submission of: affidavit, original CPRMS-NE card, death certificate, indemnity bond, Aadhaar+PAN, bank details.',
        },
    ]
    
    for rule in rules:
        c.execute('''INSERT OR REPLACE INTO rule_documents 
                     (document_name, document_type, reference_number, issued_date,
                      issuing_authority, pdf_path, text_path, summary)
                     VALUES (?,?,?,?,?,?,?,?)''',
                  (rule['document_name'], rule['document_type'], rule['reference_number'],
                   rule['issued_date'], rule['issuing_authority'], rule['pdf_path'],
                   rule['text_path'], rule['summary']))
    
    print(f"  ✅ {len(rules)} rule documents catalogued")


def print_schema_summary(conn):
    """Print schema summary."""
    c = conn.cursor()
    
    tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    
    print(f"\n  {'='*50}")
    print(f"  DATABASE SCHEMA SUMMARY")
    print(f"  {'='*50}")
    print(f"  Database: {os.path.basename(DB_PATH)}")
    print(f"  Tables: {len(tables)}")
    
    for (table_name,) in tables:
        count = c.execute(f'SELECT COUNT(*) FROM {table_name}').fetchone()[0]
        cols = c.execute(f'PRAGMA table_info({table_name})').fetchall()
        print(f"\n  📋 {table_name} ({count} rows, {len(cols)} columns)")
        for col in cols[:8]:
            nullable = '' if col[3] else ' NULL'
            pk = ' PK' if col[5] else ''
            default = f' DEFAULT={col[4]}' if col[4] else ''
            print(f"     {col[1]:30s} {col[2]:15s}{pk}{nullable}{default}")
        if len(cols) > 8:
            print(f"     ... and {len(cols) - 8} more columns")
    
    # FTS tables
    fts_tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts%'").fetchall()
    print(f"\n  🔍 FTS Tables: {', '.join(t[0] for t in fts_tables)}")
    
    # Indexes
    indexes = c.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'").fetchall()
    print(f"  📇 Custom Indexes: {len(indexes)}")
    
    print(f"\n  {'='*50}")


if __name__ == '__main__':
    print("=" * 70)
    print("  Medical Bill Validation - Database Schema Setup")
    print("=" * 70)
    
    db_path = os.path.abspath(DB_PATH)
    print(f"\n  Database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    
    try:
        setup_employee_schema(conn)
        setup_claims_schema(conn)
        setup_validation_schema(conn)
        create_indexes(conn)
        populate_rule_documents(conn)
        conn.commit()
        
        print_schema_summary(conn)
    finally:
        conn.close()
    
    print("\n" + "=" * 70)
    print("  Done!")
    print("=" * 70)
