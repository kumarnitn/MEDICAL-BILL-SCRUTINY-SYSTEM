#!/usr/bin/env python3
"""
CGHS Rate Card Parser v3 — State Machine Approach
===================================================
Uses a state machine to correctly distinguish serial numbers from rates.

States:
  SEEKING_ENTRY → looking for serial number or category header
  HAVE_SR_NO → got serial number, now looking for procedure name
  HAVE_PROCEDURE → got procedure name, next numbers must be rates
  
This reliably differentiates "350" as a rate from "350" as a serial number.
"""

import re
import csv
import sqlite3
import os

RAW_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'raw', 'cghs_rates_raw.txt')
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed', 'cghs_rates.csv')
OUTPUT_DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed', 'medical_bills.db')


# ---- Utility functions ---- #

def is_skip_line(line):
    """Lines to completely ignore (headers, column labels, etc.)"""
    patterns = [
        r'^\s*$',
        r'^\s*NonNABH/NonNABL\s+Rates?\s*$',
        r'^\s*NABH/NABL\s*$',
        r'^\s*rates?\s+in\s+Rupee\s*$',
        r'^\s*Rates?\s*$',
        r'^\s*CGHS\s+TREATMENT\s*$',
        r'^\s*CGHS\s+package\s+rate.*$',
        r'^\s*CITY\s*:.*$',
        r'^\s*Sr\.?\s*$',
        r'^\s*No\.?\s*$',
        r'^\s*PROCEDURE/INVESTIGATION\s+LIST\s*$',
        r'^\s*\(DELHI/NCR\)\s*$',
        r'^\s*INVESTIGATIONS?\s+AND\s+PROCEDURES?\s*$',
    ]
    for pat in patterns:
        if re.match(pat, line, re.IGNORECASE):
            return True
    return False


def detect_category(line):
    """Check if this line is a category/section header. Returns category name or None."""
    cleaned = line.strip()
    if not cleaned:
        return None
    
    category_map = [
        (r'TREATMENT\s+PROCEDURE\s+SKIN', 'SKIN'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*OP[TH]+ALMOLOGY', 'OPHTHALMOLOGY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*DENTAL', 'DENTAL'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*ENT', 'ENT'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*HEAD\s*[&AND]*\s*NECK', 'HEAD & NECK SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*BREAST', 'BREAST SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*GENERAL\s+SURGERY', 'GENERAL SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*OESOPHAGUS', 'OESOPHAGUS'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*STOMACH', 'STOMACH'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*LIVER', 'LIVER'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*GALL\s*BLADDER', 'GALL BLADDER'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*PANCREAS', 'PANCREAS'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*SMALL\s+BOWEL', 'SMALL BOWEL'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*LARGE\s+BOWEL', 'LARGE BOWEL / COLON'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*APPENDIX', 'APPENDIX'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*RECTUM', 'RECTUM'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*HERNIA', 'HERNIA'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*SPLEEN', 'SPLEEN'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*UROLOGY', 'UROLOGY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*KIDNEY', 'KIDNEY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*VASCULAR', 'VASCULAR SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*CARDIOVASCULAR', 'CARDIOVASCULAR & CARDIAC SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*CARDIAC\s+SURGERY', 'CARDIOVASCULAR & CARDIAC SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*OBSTETRICS', 'OBSTETRICS & GYNAECOLOGY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*GYNAECOLOGY', 'OBSTETRICS & GYNAECOLOGY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*ORTHO[PA]*EDIC', 'ORTHOPAEDICS'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*NEURO\s*SURGERY', 'NEUROSURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*PAEDIATRIC', 'PAEDIATRIC SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*PLASTIC', 'PLASTIC SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*BURNS', 'BURNS'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*THORACIC', 'THORACIC SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*ENDOCRINE', 'ENDOCRINE SURGERY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*PULMONOLOGY', 'PULMONOLOGY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*ONCOLOGY', 'ONCOLOGY'),
        (r'TREATMENT\s+PROCEDURE\s*[/\s]*ICU', 'ICU PROCEDURES'),
        (r'LIST\s+OF\s+(?:PROCEDURES|INVESTIGATION).*GASTROENTEROLOGY', 'GASTROENTEROLOGY'),
        (r'LIST\s+OF\s+(?:PROCEDURES|INVESTIGATION).*ENDOSCOP', 'GASTROENTEROLOGY / ENDOSCOPY'),
        (r'LABORATORY\s+MEDICINE.*BIO.*CHEMISTRY', 'LABORATORY - BIOCHEMISTRY'),
        (r'LABORATORY\s+MEDICINE.*CLINICAL.*PATHOLOGY', 'LABORATORY - CLINICAL PATHOLOGY'),
        (r'LABORATORY\s+MEDICINE.*HAEMATOLOGY', 'LABORATORY - HAEMATOLOGY'),
        (r'LABORATORY\s+MEDICINE.*BLOOD\s+BANK', 'LABORATORY - BLOOD BANK'),
        (r'LABORATORY\s+MEDICINE.*MICROBIOLOGY', 'LABORATORY - MICROBIOLOGY'),
        (r'LABORATORY\s+MEDICINE.*CYTOLOGY', 'LABORATORY - CYTOLOGY'),
        (r'LABORATORY\s+MEDICINE.*TUMOU?R\s+MARKER', 'LABORATORY - TUMOUR MARKERS'),
        (r'NUCLEAR\s+MEDICINE', 'NUCLEAR MEDICINE'),
        (r'CHEMOTHERAPY', 'CHEMOTHERAPY'),
        (r'RADIOTHERAPY', 'RADIOTHERAPY'),
        (r'NAME\s+OF\s+INVESTIGATION.*RADIOLOGY', 'RADIOLOGY'),
        (r'NAME\s+OF\s+INVESTIGATION.*CARDIOLOGY', 'CARDIOLOGY INVESTIGATIONS'),
        (r'NAME\s+OF\s+INVESTIGATION.*PET\s+SCAN', 'PET SCAN'),
        (r'NAME\s+OF\s+INVESTIGATION.*PULMONARY', 'PULMONARY INVESTIGATIONS'),
        (r'NAME\s+OF\s+INVESTIGATION.*NEUROLOG', 'NEUROLOGY INVESTIGATIONS'),
        (r'CARDIAC\s+SURGERY\s*[&AND]*\s*INVESTIGATIONS', 'CARDIOVASCULAR & CARDIAC SURGERY'),
        (r'\(SPECIAL\s+CARE\s+CASES\)', 'SPECIAL CARE'),
        (r'SPECIAL\s+CARE\s+CASES', 'SPECIAL CARE'),
        (r'NEW\s+PROCE[DU]+RES?\s+ADDED', 'NEW PROCEDURES (2023)'),
        (r'^NEUROLOGICAL\s*$', 'NEUROLOGY INVESTIGATIONS'),
        (r'^NEURO\s*LOGICAL\s+INVESTIGATIONS', 'NEUROLOGY INVESTIGATIONS'),
    ]
    
    for pattern, category in category_map:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return category
    
    # Catch remaining all-caps section headers (>15 chars, no digits at start)
    if (cleaned.isupper() and len(cleaned) > 15 and not cleaned[0].isdigit()
        and 'TREATMENT PROCEDURE' in cleaned):
        return cleaned.replace('TREATMENT PROCEDURE', '').strip()
    
    return None


def parse_number(text):
    """Try to parse text as a number (rate). Returns float or None."""
    cleaned = text.strip().replace(',', '')
    # Remove non-numeric suffixes like 'per eye'
    cleaned = re.sub(r'(per\s+eye|both\s+eyes?|per\s+session|per\s+sitting|per\s+cycle)',
                     '', cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def is_pure_number(text):
    """Check if text is purely a number (no alpha chars except common suffixes)."""
    cleaned = text.strip().replace(',', '')
    cleaned = re.sub(r'(per\s+eye|both\s+eyes?|per\s+session|per\s+sitting|per\s+cycle)',
                     '', cleaned, flags=re.IGNORECASE).strip()
    if not cleaned:
        return False
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def extract_notes(text):
    """Extract special annotations from text."""
    notes = set()
    tl = text.lower()
    if 'per eye' in tl: notes.add('per eye')
    if 'both eye' in tl: notes.add('both eyes')
    if 'per session' in tl: notes.add('per session')
    if 'per sitting' in tl: notes.add('per sitting')
    if 'per cycle' in tl: notes.add('per cycle')
    if 'including gst' in tl: notes.add('including GST')
    m = re.search(r'see\s+code\s+(\d+)', tl)
    if m: notes.add(f'see code {m.group(1)}')
    return '; '.join(sorted(notes))


# ---- Main Parser ---- #

def parse_cghs_rates(raw_file_path):
    """
    State machine parser for CGHS rate card.
    
    The document follows this repeating pattern:
        [SR_NO]  →  [PROCEDURE_TEXT (1+ lines)]  →  [NonNABH rate]  →  [NABH rate]
    
    With variations:
        - SR_NO can be on its own line ("1") or inline ("717 Partial Nephrectomy")
        - Category headers appear between groups
        - Page headers repeat throughout
    """
    
    with open(raw_file_path, 'r', encoding='utf-8') as f:
        raw_text = f.read()
    
    # Clean form feeds
    raw_text = raw_text.replace('\f', '\n')
    lines = raw_text.split('\n')
    
    entries = []
    current_category = 'GENERAL'
    
    # State machine
    STATE_SEEKING = 'SEEKING'       # Looking for next sr_no or category
    STATE_PROC_NAME = 'PROC_NAME'   # Collecting procedure name text
    STATE_RATE1 = 'RATE1'           # Expecting first rate (non-NABH)
    STATE_RATE2 = 'RATE2'           # Expecting second rate (NABH)
    
    state = STATE_SEEKING
    current_sr = None
    current_proc_parts = []
    current_non_nabh = None
    current_nabh = None
    current_rate_texts = []
    expected_next_sr = 1  # Track expected sequence for disambiguation
    
    def flush_entry():
        """Save the current entry if valid."""
        nonlocal current_sr, current_proc_parts, current_non_nabh, current_nabh, current_rate_texts
        if current_sr is not None and current_proc_parts:
            proc_name = ' '.join(current_proc_parts).strip()
            proc_name = re.sub(r'\s+', ' ', proc_name)
            all_text = proc_name + ' ' + ' '.join(current_rate_texts)
            notes = extract_notes(all_text)
            
            entries.append({
                'sr_no': current_sr,
                'category': current_category,
                'procedure_name': proc_name,
                'non_nabh_rate': current_non_nabh,
                'nabh_rate': current_nabh if current_nabh is not None else current_non_nabh,
                'notes': notes,
            })
        # Reset
        current_sr = None
        current_proc_parts = []
        current_non_nabh = None
        current_nabh = None
        current_rate_texts = []
    
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        
        # Skip empty and header lines
        if is_skip_line(stripped):
            continue
        
        # Category headers (always process regardless of state)
        cat = detect_category(stripped)
        if cat is not None:
            if state in (STATE_RATE1, STATE_RATE2, STATE_PROC_NAME):
                flush_entry()
            current_category = cat
            state = STATE_SEEKING
            expected_next_sr = None  # Reset, will be set by next entry
            continue
        
        # ========================
        # STATE: SEEKING next entry
        # ========================
        if state == STATE_SEEKING:
            # Look for serial number (standalone or inline)
            
            # Standalone number
            if re.match(r'^\d{1,4}$', stripped):
                num = int(stripped)
                current_sr = num
                current_proc_parts = []
                state = STATE_PROC_NAME
                if expected_next_sr is None:
                    expected_next_sr = num + 1
                else:
                    expected_next_sr = num + 1
                continue
            
            # Inline: "717 Partial Nephrectomy -open"
            m = re.match(r'^(\d{1,4})\s+(.+)', stripped)
            if m:
                num = int(m.group(1))
                text = m.group(2).strip()
                current_sr = num
                current_proc_parts = [text]
                state = STATE_PROC_NAME
                expected_next_sr = num + 1
                continue
            
            # Text line while seeking — could be a continuation or orphaned text, skip it
            continue
        
        # ================================
        # STATE: PROC_NAME — collecting procedure name
        # ================================
        elif state == STATE_PROC_NAME:
            # Is this line a number? Need to decide: rate or next serial number?
            if re.match(r'^\d[\d,]*$', stripped.replace(' ', '')):
                num_val = parse_number(stripped)
                
                if num_val is not None:
                    # Heuristic: is this a rate or a serial number?
                    # It's a rate if:
                    #   1. We already have procedure text (current_proc_parts is not empty), AND
                    #   2. The number doesn't equal expected_next_sr, OR
                    #   3. The number is "too big" to be a sr_no in context
                    
                    int_val = int(num_val)
                    
                    if current_proc_parts:
                        # We have procedure text → this must be a rate
                        current_non_nabh = num_val
                        current_rate_texts.append(stripped)
                        state = STATE_RATE2
                        continue
                    else:
                        # No procedure text yet → this sr_no was followed by another number?
                        # Unlikely, but could be the procedure name is completely numeric (rare)
                        # Treat as rate
                        current_non_nabh = num_val
                        current_rate_texts.append(stripped)
                        state = STATE_RATE2
                        continue
            
            # Is this a rate with text annotation? (e.g., "3400 per eye")
            if is_pure_number(stripped):
                num_val = parse_number(stripped)
                if num_val is not None and current_proc_parts:
                    current_non_nabh = num_val
                    current_rate_texts.append(stripped)
                    state = STATE_RATE2
                    continue
            
            # Check if this is an inline entry (new serial number with text)
            m = re.match(r'^(\d{1,4})\s+(.+)', stripped)
            if m:
                num = int(m.group(1))
                text = m.group(2).strip()
                
                # If this is the expected next serial and text has alpha characters → new entry
                if not is_pure_number(text):
                    # Flush current entry first
                    flush_entry()
                    current_sr = num
                    current_proc_parts = [text]
                    expected_next_sr = num + 1
                    state = STATE_PROC_NAME
                    continue
                else:
                    # It might be sr_no followed by rate on same line (rare formatting)
                    # Treat as text continuation for now
                    current_proc_parts.append(stripped)
                    continue
            
            # Otherwise it's procedure name text
            current_proc_parts.append(stripped)
            continue
        
        # ================================
        # STATE: RATE2 — expecting NABH rate
        # ================================
        elif state == STATE_RATE2:
            # Should be the NABH rate
            if is_pure_number(stripped):
                num_val = parse_number(stripped)
                if num_val is not None:
                    current_nabh = num_val
                    current_rate_texts.append(stripped)
                    flush_entry()
                    state = STATE_SEEKING
                    continue
            
            # Could be a "See code XXXX" reference instead of a rate
            if re.search(r'see\s+code\s+\d+', stripped, re.IGNORECASE):
                current_rate_texts.append(stripped)
                flush_entry()
                state = STATE_SEEKING
                continue
            
            # Check if this is a new serial number (skipped NABH rate)
            if re.match(r'^\d{1,4}$', stripped):
                num = int(stripped)
                # If close to expected next sr, this is a new entry
                if expected_next_sr and abs(num - expected_next_sr) <= 2:
                    # NABH rate was missing — flush with just non-NABH
                    flush_entry()
                    current_sr = num
                    current_proc_parts = []
                    expected_next_sr = num + 1
                    state = STATE_PROC_NAME
                    continue
            
            # Inline new entry
            m = re.match(r'^(\d{1,4})\s+(.+)', stripped)
            if m and not is_pure_number(m.group(2)):
                # New inline entry, nabh rate was missing
                flush_entry()
                current_sr = int(m.group(1))
                current_proc_parts = [m.group(2).strip()]
                expected_next_sr = current_sr + 1
                state = STATE_PROC_NAME
                continue
            
            # Category header
            cat = detect_category(stripped)
            if cat is not None:
                flush_entry()
                current_category = cat
                state = STATE_SEEKING
                continue
            
            # Otherwise treat as additional info and flush
            current_rate_texts.append(stripped)
            flush_entry()
            state = STATE_SEEKING
            continue
    
    # Flush last entry
    if state != STATE_SEEKING:
        flush_entry()
    
    return entries


def save_to_csv(entries, output_path):
    """Save to CSV."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['sr_no', 'category', 'procedure_name',
                                                'non_nabh_rate', 'nabh_rate', 'notes'])
        writer.writeheader()
        writer.writerows(entries)
    print(f"✅ CSV: {len(entries)} entries → {output_path}")


def save_to_sqlite(entries, db_path):
    """Save to SQLite with FTS5 index."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute('DROP TABLE IF EXISTS cghs_rates_fts')
    c.execute('DROP TABLE IF EXISTS cghs_rates')
    
    c.execute('''CREATE TABLE cghs_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sr_no INTEGER,
        category TEXT NOT NULL,
        procedure_name TEXT NOT NULL,
        non_nabh_rate REAL,
        nabh_rate REAL,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE VIRTUAL TABLE cghs_rates_fts USING fts5(
        procedure_name, category,
        content='cghs_rates', content_rowid='id'
    )''')
    
    for e in entries:
        c.execute('INSERT INTO cghs_rates (sr_no,category,procedure_name,non_nabh_rate,nabh_rate,notes) VALUES (?,?,?,?,?,?)',
                  (e['sr_no'], e['category'], e['procedure_name'], e['non_nabh_rate'], e['nabh_rate'], e['notes']))
    
    c.execute('INSERT INTO cghs_rates_fts (rowid, procedure_name, category) SELECT id, procedure_name, category FROM cghs_rates')
    conn.commit()
    
    # ---- Reports ----
    total = c.execute('SELECT COUNT(*) FROM cghs_rates').fetchone()[0]
    with_rates = c.execute('SELECT COUNT(*) FROM cghs_rates WHERE non_nabh_rate IS NOT NULL').fetchone()[0]
    
    print(f"\n✅ SQLite: {total} entries → {db_path}")
    print(f"   Rates present: {with_rates}/{total} ({100*with_rates//total}%)")
    
    cats = c.execute('SELECT category, COUNT(*) FROM cghs_rates GROUP BY category ORDER BY MIN(id)').fetchall()
    print(f"\n   Categories ({len(cats)}):")
    for name, cnt in cats:
        print(f"     {cnt:4d} | {name}")
    
    # Spot checks
    print(f"\n   Spot checks:")
    checks = [
        (1, 'Consultation OPD', 350.0, 350.0),
        (3, 'Dressings of wounds', 255.0, 300.0),
        (14, 'Phimosis', 5100.0, 6000.0),
        (83, 'OCT', 2125.0, 2500.0),
        (350, None, None, None),  # Just check it exists
    ]
    
    all_pass = True
    for sr, name_hint, exp_non, exp_nabh in checks:
        row = c.execute('SELECT procedure_name, non_nabh_rate, nabh_rate FROM cghs_rates WHERE sr_no = ? ORDER BY id LIMIT 1', (sr,)).fetchone()
        if row:
            if exp_non is not None:
                ok = (row[1] == exp_non and row[2] == exp_nabh)
                status = "✅" if ok else "❌"
                if not ok: all_pass = False
                print(f"     {status} #{sr:4d} {row[0][:45]:45s}  ₹{row[1]:>8} / ₹{row[2]:>8}  (expected ₹{exp_non} / ₹{exp_nabh})")
            else:
                print(f"     ✅ #{sr:4d} {row[0][:45]:45s}  ₹{row[1]} / ₹{row[2]}")
        else:
            print(f"     ❌ #{sr} NOT FOUND")
            all_pass = False
    
    print(f"\n   First 3 entries:")
    for r in c.execute('SELECT sr_no, procedure_name, non_nabh_rate, nabh_rate FROM cghs_rates ORDER BY id LIMIT 3'):
        print(f"     #{r[0]:4d}: {r[1][:50]:50s} ₹{r[2]} / ₹{r[3]}")
    
    print(f"   Last 3 entries:")
    for r in c.execute('SELECT sr_no, procedure_name, non_nabh_rate, nabh_rate FROM cghs_rates ORDER BY id DESC LIMIT 3'):
        print(f"     #{r[0]:4d}: {r[1][:50]:50s} ₹{r[2]} / ₹{r[3]}")
    
    conn.close()
    return all_pass


if __name__ == '__main__':
    print("=" * 70)
    print("  CGHS Rate Card Parser v3 (State Machine)")
    print("=" * 70)
    
    raw_path = os.path.abspath(RAW_FILE)
    print(f"\n  Input: {raw_path}")
    
    entries = parse_cghs_rates(raw_path)
    print(f"  Parsed {len(entries)} procedure entries")
    
    save_to_csv(entries, os.path.abspath(OUTPUT_CSV))
    ok = save_to_sqlite(entries, os.path.abspath(OUTPUT_DB))
    
    print("\n" + "=" * 70)
    if ok:
        print("  ✅ All spot checks PASSED!")
    else:
        print("  ⚠️  Some spot checks failed — may need further tuning")
    print("=" * 70)
